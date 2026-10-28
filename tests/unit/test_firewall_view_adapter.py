# -*- coding: utf-8 -*-
"""防火墙中心策略展示数据适配测试。"""

from app.ui.adapters.firewall import adapt_center_rule_row, adapt_center_rule_rows


def test_center_json_rule_adapter_preserves_owner_flow_action_and_priority() -> None:
    row = adapt_center_rule_row(
        {
            "id": "rule-12345678",
            "_row_kind": "json",
            "_owner_id": "group-dev",
            "owner_type": "group",
            "owner_id": "group-dev",
            "deployment_target": "center",
            "action": "accept",
            "protocol": "tcp",
            "source_subnet": "10.8.0.0/24",
            "dest_ip": "172.16.0.10",
            "dest_port": "443",
            "priority": 20,
            "enabled": True,
            "description": "开发访问 API",
        },
        group_names={"group-dev": "开发组"},
    )

    assert row["id"] == "rule-12345678"
    assert row["row_kind"] == "json"
    assert row["owner_type"] == "group"
    assert row["owner_id"] == "group-dev"
    assert row["owner_label"] == "开发组"
    assert row["kind_label"] == "中心节点"
    assert row["source"] == "10.8.0.0/24"
    assert row["dest"] == "172.16.0.10"
    assert row["dest_port"] == "443"
    assert row["rule_expression"] == "10.8.0.0/24 → 172.16.0.10:443"
    assert row["protocol"] == "TCP"
    assert row["action_label"] == "ACCEPT"
    assert row["action_tone"] == "success"
    assert row["priority"] == 20
    assert row["status_label"] == "启用"
    assert row["raw"]["description"] == "开发访问 API"
    assert "开发组" not in row["search_text"]
    assert "172.16.0.10" in row["search_text"]
    assert "开发访问 API" in row["search_text"]


def test_user_rule_adapter_uses_source_ips_and_normalizes_any_protocol() -> None:
    row = adapt_center_rule_row(
        {
            "id": "user-rule",
            "_row_kind": "json",
            "_owner_id": "alice",
            "owner_type": "user",
            "owner_id": "alice",
            "action": "drop",
            "protocol": "any",
            "source_ips": ["10.8.0.10", "10.8.0.11"],
            "dest_ip": "",
            "dest_port": "",
            "priority": 30,
            "enabled": False,
        },
    )

    assert row["owner_label"] == "alice"
    assert row["source"] == "10.8.0.10,10.8.0.11"
    assert row["dest"] == "*"
    assert row["dest_port"] == "*"
    assert row["protocol"] == "ALL"
    assert row["action_label"] == "DROP"
    assert row["action_tone"] == "failed"
    assert row["status_label"] == "停用"
    assert row["status_tone"] == "disabled"


def test_center_peer_virtual_row_preserves_peer_identity_and_special_semantics() -> None:
    row = adapt_center_rule_row(
        {
            "id": "ce-peer-550e8400-e29b-41d4-a716-446655440000",
            "_row_kind": "center_peer",
            "_peer_id": "550e8400-e29b-41d4-a716-446655440000",
            "_owner_id": "550e8400-e29b-41d4-a716-446655440000",
            "owner_type": "peer",
            "owner_id": "550e8400-e29b-41d4-a716-446655440000",
            "deployment_target": "center",
            "action": "accept",
            "protocol": "udp",
            "source_subnet": "192.168.10.0/24,192.168.20.0/24",
            "dest_ip": "10.0.0.0/8",
            "dest_port": "53",
            "priority": 500000,
            "enabled": True,
            "description": "站点 DNS",
            "_center_tag_line": "nicevpn-center-peer peer=550e8400-e29b-41d4-a716-446655440000",
        },
        peer_names={"550e8400-e29b-41d4-a716-446655440000": "广州站点"},
    )

    assert row["is_center_peer"] is True
    assert row["peer_id"] == "550e8400-e29b-41d4-a716-446655440000"
    assert row["owner_label"] == "广州站点 (550e8400)"
    assert row["kind_label"] == "对端·中心放行"
    assert row["source"] == "192.168.10.0/24,192.168.20.0/24"
    assert row["description"].startswith("站点 DNS | ")
    assert row["raw"]["_row_kind"] == "center_peer"


def test_peer_deployment_marker_is_not_confused_with_center_peer_virtual_row() -> None:
    row = adapt_center_rule_row(
        {
            "id": "rule-peer-marker",
            "_row_kind": "json",
            "_owner_id": "group-1",
            "owner_type": "group",
            "owner_id": "group-1",
            "deployment_target": "peer",
            "action": "reject",
            "protocol": "icmp",
            "priority": 40,
            "enabled": True,
        },
    )

    assert row["is_center_peer"] is False
    assert row["kind_label"] == "对端仅标识"
    assert row["action_label"] == "REJECT"
    assert row["action_tone"] == "warning"


def test_batch_adapter_keeps_service_priority_order() -> None:
    rows = adapt_center_rule_rows(
        [
            {
                "id": "r10",
                "owner_type": "group",
                "owner_id": "g",
                "priority": 10,
                "action": "accept",
            },
            {
                "id": "r20",
                "owner_type": "group",
                "owner_id": "g",
                "priority": 20,
                "action": "drop",
            },
        ],
    )

    assert [row["id"] for row in rows] == ["r10", "r20"]
    assert [row["priority"] for row in rows] == [10, 20]
