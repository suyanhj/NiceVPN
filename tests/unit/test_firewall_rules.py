# -*- coding: utf-8 -*-
"""防火墙规则校验单元测试"""
import json

import pytest
from pydantic import ValidationError

from app.models.firewall import FirewallRule


# 创建 FirewallRule 的最小必填字段
REQUIRED_FIELDS = {
    "owner_type": "group",
    "owner_id": "grp-001",
    "instance": "vpn-main",
    "action": "accept",
    "priority": 100,
}


def _make_rule(**overrides) -> FirewallRule:
    """辅助函数：基于默认必填字段创建规则，可覆盖任意字段"""
    fields = {**REQUIRED_FIELDS, **overrides}
    return FirewallRule(**fields)


class TestPortValidation:
    """测试端口校验逻辑"""

    def test_valid_single_port(self):
        """测试合法的单端口"""
        for port in ["80", "443", "65535", "1"]:
            rule = _make_rule(dest_port=port)
            assert rule.dest_port == port

    def test_valid_port_range(self):
        """测试合法的端口范围"""
        for port_range in ["1024:65535", "80:443", "1:65535"]:
            rule = _make_rule(dest_port=port_range)
            assert rule.dest_port == port_range

    def test_port_range_hyphen_normalized_to_colon(self):
        """连字符范围与冒号等价，落库为冒号以与 iptables 一致。"""
        rule = _make_rule(dest_port="80-400")
        assert rule.dest_port == "80:400"
        rule2 = _make_rule(dest_port="10, 20-30")
        assert rule2.dest_port == "10,20:30"

    def test_invalid_port(self):
        """测试非法端口值"""
        # 端口 0 不在有效范围 1-65535 内
        with pytest.raises(ValidationError):
            _make_rule(dest_port="0")

        # 端口 65536 超出范围
        with pytest.raises(ValidationError):
            _make_rule(dest_port="65536")

        # 非数字字符串
        with pytest.raises(ValidationError):
            _make_rule(dest_port="abc")

        # 起始端口大于结束端口
        with pytest.raises(ValidationError):
            _make_rule(dest_port="80:70")

    def test_null_port(self):
        """测试端口为 None 应通过校验"""
        rule = _make_rule(dest_port=None)
        assert rule.dest_port is None

        # 不传 dest_port，默认也是 None
        rule2 = _make_rule()
        assert rule2.dest_port is None


class TestPriorityAndReorder:
    """测试规则优先级排序"""

    def test_priority_sorting(self):
        """创建多条规则，验证按 priority 升序排序（数值越小优先级越高）"""
        rules = [
            _make_rule(priority=300, description="低优先级"),
            _make_rule(priority=100, description="高优先级"),
            _make_rule(priority=200, description="中优先级"),
        ]

        # 按 priority 字段升序排序
        sorted_rules = sorted(rules, key=lambda r: r.priority)

        assert sorted_rules[0].priority == 100
        assert sorted_rules[0].description == "高优先级"
        assert sorted_rules[1].priority == 200
        assert sorted_rules[1].description == "中优先级"
        assert sorted_rules[2].priority == 300
        assert sorted_rules[2].description == "低优先级"


def test_center_json_rollback_on_rebuild_fail(tmp_path, monkeypatch):
    """iptables 重建失败时，中心 owner JSON 从 prepush 恢复为改前内容。"""
    from app.services.firewall import rule_service as rs

    monkeypatch.setattr(rs, "FIREWALL_DIR", tmp_path)
    original = {
        "rules": [
            {
                **REQUIRED_FIELDS,
                "id": "r1",
                "priority": 100,
                "enabled": True,
                "description": "orig",
            },
        ]
    }
    (tmp_path / "grp-001.json").write_text(json.dumps(original), encoding="utf-8")

    svc = rs.FirewallRuleService()

    def boom():
        raise RuntimeError("iptables 模拟失败")

    monkeypatch.setattr(svc, "_refresh_vpn_forward_only", boom)

    with pytest.raises(RuntimeError, match="iptables"):
        svc.set_enabled("r1", False)

    data = json.loads((tmp_path / "grp-001.json").read_text(encoding="utf-8"))
    assert data["rules"][0]["enabled"] is True
    assert data["rules"][0]["description"] == "orig"
    assert not (tmp_path / "grp-001.prepush.bak").is_file()


def test_owner_reorder_only_refreshes_vpn_forward(tmp_path, monkeypatch):
    """owner 内拖拽排序只能走 VPN_FORWARD 专用刷新路径，不能触发完整重建。"""
    from app.services.firewall import rule_service as rs

    monkeypatch.setattr(rs, "FIREWALL_DIR", tmp_path)
    (tmp_path / "grp-001.json").write_text(
        json.dumps(
            {
                "rules": [
                    {**REQUIRED_FIELDS, "id": "r1", "priority": 10, "enabled": True},
                    {**REQUIRED_FIELDS, "id": "r2", "priority": 20, "enabled": True},
                ]
            }
        ),
        encoding="utf-8",
    )

    svc = rs.FirewallRuleService()
    called = {"vpn_forward": 0}
    monkeypatch.setattr(
        svc,
        "_rebuild_all_rules",
        lambda: (_ for _ in ()).throw(AssertionError("规则页操作不允许完整重建")),
    )
    monkeypatch.setattr(
        svc,
        "_refresh_vpn_forward_only",
        lambda: called.__setitem__("vpn_forward", called["vpn_forward"] + 1),
    )

    assert svc.reorder("grp-001", ["r2", "r1"])

    data = json.loads((tmp_path / "grp-001.json").read_text(encoding="utf-8"))
    assert [r["id"] for r in data["rules"]] == ["r2", "r1"]
    assert [r["priority"] for r in data["rules"]] == [10, 20]
    assert called["vpn_forward"] == 1
    assert not (tmp_path / "grp-001.prepush.bak").is_file()


def test_center_update_rejects_invalid_dest_before_save(tmp_path, monkeypatch):
    """目标地址非法时须在落库前拒绝，不触发 prepush/iptables，与对端链地址策略一致。"""
    from app.services.firewall import rule_service as rs

    monkeypatch.setattr(rs, "FIREWALL_DIR", tmp_path)
    (tmp_path / "grp-001.json").write_text(
        json.dumps(
            {
                "rules": [
                    {**REQUIRED_FIELDS, "id": "r1", "priority": 100, "dest_ip": "10.0.0.1"},
                ]
            }
        ),
        encoding="utf-8",
    )
    svc = rs.FirewallRuleService()
    with pytest.raises(ValueError, match="非法 IP"):
        svc.update_by_id("r1", {"dest_ip": "1.1.1.1.1"})


def test_center_json_rollback_on_rebuild_non_runtime_error(tmp_path, monkeypatch):
    """重建阶段抛出非 RuntimeError 时须同样从 prepush 回滚，与对端「失败即回滚」一致。"""
    from app.services.firewall import rule_service as rs

    monkeypatch.setattr(rs, "FIREWALL_DIR", tmp_path)
    original = {
        "rules": [
            {
                **REQUIRED_FIELDS,
                "id": "r1",
                "priority": 100,
                "enabled": True,
            },
        ]
    }
    (tmp_path / "grp-001.json").write_text(json.dumps(original), encoding="utf-8")

    svc = rs.FirewallRuleService()
    monkeypatch.setattr(
        svc, "_refresh_vpn_forward_only", lambda: (_ for _ in ()).throw(ValueError("模拟子系统异常"))
    )

    with pytest.raises(ValueError, match="模拟"):
        svc.set_enabled("r1", False)

    data = json.loads((tmp_path / "grp-001.json").read_text(encoding="utf-8"))
    assert data["rules"][0]["enabled"] is True
    assert not (tmp_path / "grp-001.prepush.bak").is_file()


class TestIptablesInstCommentSanitize:
    """inst 写入 iptables 注释前的规范化（避免 Linux 上 -A 失败）"""

    def test_ascii_passthrough(self):
        from app.services.firewall.iptables_mgr import IptablesManager

        assert IptablesManager._sanitize_inst_for_iptables_comment("srv-01") == "srv-01"

    def test_unicode_replaced(self):
        from app.services.firewall.iptables_mgr import IptablesManager

        # 非 ASCII 经替换后若仅剩下划线，回退为 server
        assert IptablesManager._sanitize_inst_for_iptables_comment("实例甲") == "server"
        assert IptablesManager._sanitize_inst_for_iptables_comment("___") == "server"

    def test_long_truncated_with_digest_suffix(self):
        from app.services.firewall.iptables_mgr import (
            IptablesManager,
            _IPTABLES_INST_COMMENT_MAX,
        )

        long_id = "a" * 200
        s = IptablesManager._sanitize_inst_for_iptables_comment(long_id)
        assert len(s) <= _IPTABLES_INST_COMMENT_MAX
        assert "_" in s


class TestPeerChainNaming:
    """对端中心侧专用链名。"""

    def test_peer_chain_name_stable_and_short(self):
        from app.services.firewall.iptables_mgr import IptablesManager, PEER_CHAIN_PREFIX

        pid = "550e8400-e29b-41d4-a716-446655440000"
        n = IptablesManager.peer_chain_name_for_id(pid)
        assert n == IptablesManager.peer_chain_name_for_id(pid)
        assert n.startswith(PEER_CHAIN_PREFIX)
        assert len(n) <= 31


class TestIptablesIpsetStrategy:
    """验证源/目标地址的 ipset 选择策略。"""

    def test_multi_specific_ip_use_ipset(self):
        """多个具体 IP 触发 ipset。"""
        from app.services.firewall.iptables_mgr import IptablesManager

        assert IptablesManager._has_multi_specific_ips(["10.0.0.1", "10.0.0.2"])
        assert IptablesManager._has_multi_specific_ips(["10.0.0.1", "10.0.0.0/24", "10.0.0.2"])

    def test_cidr_or_single_ip_not_use_ipset(self):
        """仅 CIDR 或单 IP 不触发 ipset。"""
        from app.services.firewall.iptables_mgr import IptablesManager

        assert not IptablesManager._has_multi_specific_ips(["10.0.0.0/24", "10.1.0.0/24"])
        assert not IptablesManager._has_multi_specific_ips(["10.0.0.1"])
        assert not IptablesManager._has_multi_specific_ips([])

    def test_expand_rule_lines_dest_multi_cidr_direct_iptables(self):
        """目标多 CIDR 直接展开为 -d，不走 dst ipset。"""
        from app.services.firewall.iptables_mgr import IptablesManager

        rule = _make_rule(
            id="r-cidr",
            source_subnet="10.8.0.0/24",
            dest_ip="172.16.0.0/16,192.168.0.0/24",
            protocol="tcp",
            dest_port="443",
        )
        lines = IptablesManager()._expand_rule_lines(rule)
        assert len(lines) == 2
        assert all("--match-set" not in x for x in lines)
        assert any("-d 172.16.0.0/16" in x for x in lines)
        assert any("-d 192.168.0.0/24" in x for x in lines)

    def test_expand_rule_lines_src_multi_specific_ip_use_ipset(self):
        """源多具体 IP 时使用 src ipset。"""
        from app.services.firewall.iptables_mgr import IptablesManager

        rule = _make_rule(
            id="r-src-ipset",
            source_ips=["10.0.0.1", "10.0.0.2"],
            dest_ip="172.16.0.0/16",
        )
        lines = IptablesManager()._expand_rule_lines(rule)
        assert len(lines) == 1
        assert "--match-set " + IptablesManager._rule_ipset_name("r-src-ipset", "src") + " src" in lines[0]
        assert "-d 172.16.0.0/16" in lines[0]

    def test_expand_rule_lines_src_single_ip_dest_multi_specific_ip(self):
        """任一侧满足多具体 IP 即使用对应 ipset，另一侧按直接匹配。"""
        from app.services.firewall.iptables_mgr import IptablesManager

        rule = _make_rule(
            id="r-dst-ipset",
            source_ips=["10.0.0.1"],
            dest_ip="172.16.0.10,172.16.0.20",
        )
        lines = IptablesManager()._expand_rule_lines(rule)
        assert len(lines) == 1
        assert "-s 10.0.0.1" in lines[0]
        assert "--match-set " + IptablesManager._rule_ipset_name("r-dst-ipset", "dst") + " dst" in lines[0]


class TestRemoteRestsFromCreateFields:
    """对端「与中心同表单」转 rest 与 :func:`peer_rests_from_simplified_line` 一致。"""

    def test_one_subnet_and_dest_port(self):
        from app.services.firewall.simple_rule_import import remote_rests_from_create_fields

        lines = remote_rests_from_create_fields(
            source_subnet="192.168.1.0/24",
            source_ips=None,
            action="accept",
            protocol="tcp",
            dest_ip="10.0.0.1",
            dest_port="443",
        )
        assert len(lines) == 1
        assert "-s 192.168.1.0/24" in lines[0] and "-d 10.0.0.1" in lines[0]
        assert "-p tcp" in lines[0] and "--dport 443" in lines[0] and "ACCEPT" in lines[0]
