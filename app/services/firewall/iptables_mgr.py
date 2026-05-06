# -*- coding: utf-8 -*-
"""iptables + ipset 规则原子重建服务

通过 ipset 存放源网段，iptables 使用 -m set 匹配，避免过长 -s 列表。
仍通过 iptables-restore 对 VPN_FORWARD 链做原子替换。

**组网双向放行（概念）**：对端与中心互通需要在两边各写策略，缺一不可。
- **中心（本机）**：放行「源地址为对端后方内网」经 VPN 进入本机转发的流量（我允许你进我家门）。
  落点为本链 ``VPN_FORWARD``，注释 ``ovpn-mgmt-center-peer``。
- **对端主机**：放行「源地址为中心 VPN 地址池、从隧道网卡进入」的转发流量（你允许我进你家门）。
  由 ``remote_peer_iptables.apply_peer_site_iptables_via_ssh`` 写入对端，**不在本文件里建链**。
二者共同构成双向通路；只配中心或只配对端都会导致单向不通。

FORWARD 默认策略常为 DROP 时，必须在 FORWARD 中跳转至 VPN_FORWARD，否则 VPN_FORWARD 显示
「0 references」、其中 ACCEPT 永不生效。重建成功后会自动补齐（顺序有语义）：
- ``-i tun+ -j VPN_FORWARD``（中心业务 JSON + **对端内网放行**，对端相关规则注释 ``ovpn-mgmt-center-peer``）
- 再 RELATED,ESTABLISHED -j ACCEPT（未在 VPN_FORWARD 命中的已建连/相关流在链尾放行）
- **说明**：对端本机上的 ``VPN_PEER_<hash>`` 仅由 **SSH** 创建；中心进程不创建该链名。
- 上述固定钩子与 INPUT / nat MASQUERADE 的 iptables 注释均带 ``inst=<本机实例标识>``（见
  ``get_local_openvpn_instance_id`` / ``SystemConfig.vpn_instance_id``），便于未来多机组网时与远端规则区分。
- INPUT：仅当 filter INPUT 默认策略为 DROP（或非 ACCEPT）时，追加从 tun+ 进入且源为 global_subnet
  的放行（访问本机 eth0 等走 INPUT）；若 INPUT 已为 ACCEPT，则不追加（默认已允许全部入站）
- VPN_FORWARD：界面 JSON 启用规则与对端内网放行合并写入（注释 ``ovpn-mgmt-center-peer peer=…``）；JSON 规则仅当填写「规则描述」时附加 ``--comment``
- net.ipv4.ip_forward=1（可写时写入）
- nat POSTROUTING：对 **源为 global_subnet** 的流量做 **MASQUERADE**，
  **不依赖出网网卡名**；便于多网卡环境。旧版 ``-o`` MASQUERADE 或固定 SNAT 由重建前清理逻辑删除。
"""

import hashlib
import ipaddress
import json
import logging
import os
import re
import shlex
import subprocess
import tempfile
from typing import Optional

from pydantic import ValidationError

from app.models.firewall import FirewallRule

logger = logging.getLogger(__name__)

# 自定义链名称，中心业务 JSON 规则放入此链
CHAIN_NAME = "VPN_FORWARD"
# 旧版共享链名（仅迁移清理用）
LEGACY_CHAIN_PEER = "VPN_PEER"
# 对端专用链前缀（后缀为 md5 短哈希，总长 ≤ iptables 链名限制）
PEER_CHAIN_PREFIX = "VPN_PEER_"
# ipset 集合名前缀（总长度需 <=31）
IPSET_PREFIX = "ovpnfw_"
IPSET_PREFIX_PEER = "ovpnpeer_"
# xt_comment 整条注释通常 ≤256；前缀 ``ovpn-mgmt-… inst=`` 约占 40 字节，inst 值单独限长
_IPTABLES_INST_COMMENT_MAX = 96


class IptablesManager:
    """iptables + ipset 规则的原子重建、导出与导入。"""

    def _build_merged_forward_entries(
        self,
        rules: list[FirewallRule],
        center_peers: Optional[list[tuple]] = None,
    ) -> tuple[list[tuple[int, str, object]], int]:
        """按 priority 合并中心 JSON 规则与对端中心放行，返回 ``VPN_FORWARD`` 写入顺序。"""
        center_peers = center_peers or []
        peer_plan = sum(
            1 for r in rules if r.enabled and r.deployment_target == "peer"
        )
        merged: list[tuple[int, str, object]] = []
        for r in rules:
            if r.enabled and r.deployment_target == "center":
                merged.append((r.priority, "j", r))
        for ent in center_peers:
            pid = str(ent[0]).strip()
            cidrs = ent[1]
            pri = int(ent[2])
            extra = ent[3] if len(ent) > 3 else {}
            if not isinstance(extra, dict):
                extra = {}
            cln = [str(c).strip() for c in cidrs if str(c).strip()]
            if cln:
                merged.append((pri, "p", (pid, cln, extra)))
        merged.sort(key=lambda x: (x[0], 0 if x[1] == "j" else 1))
        return merged, peer_plan

    def _ensure_ipsets_for_merged_forward(self, merged: list[tuple[int, str, object]]) -> None:
        """为 ``VPN_FORWARD`` 中会引用的 ipset 创建/刷新集合。"""
        for _pri, kind, payload in merged:
            if kind == "j":
                rule = payload
                assert isinstance(rule, FirewallRule)
                if self._has_multi_specific_ips(list(rule.source_ips or [])):
                    self._ipset_ensure_add_multi(
                        self._rule_ipset_name(rule.id, "src"), rule.source_ips
                    )
                if self._rule_should_use_dest_ipset(rule):
                    dest_ips = [
                        ip.strip() for ip in str(rule.dest_ip or "").split(",") if ip.strip()
                    ]
                    if dest_ips:
                        self._ipset_ensure_add_multi(
                            self._rule_ipset_name(rule.id, "dst"), dest_ips
                        )
            else:
                pid, cln, extra = payload  # type: ignore[misc]
                if self._has_multi_specific_ips(cln):
                    self._ipset_ensure_add_multi(self._peer_lan_ipset_name(pid), cln)
                dest_ip = str((extra or {}).get("dest_ip") or "").strip()
                if dest_ip:
                    dest_ips = [ip.strip() for ip in dest_ip.split(",") if ip.strip()]
                    if self._has_multi_specific_ips(dest_ips):
                        self._ipset_ensure_add_multi(
                            self._rule_ipset_name(f"ce-peer-{pid}", "dst"), dest_ips
                        )

    def refresh_vpn_forward_only(
        self,
        rules: list[FirewallRule],
        *,
        center_peers: Optional[list[tuple]] = None,
    ) -> bool:
        """仅重写 ``VPN_FORWARD`` 链，不触碰 INPUT/FORWARD 固定钩子、ip_forward、MASQUERADE。"""
        if os.name == "nt":
            return True
        saved = self._iptables_save_chain_rule_lines(CHAIN_NAME)
        if saved is None:
            logger.error("无法备份 VPN_FORWARD 链，已中止刷新")
            return False

        def _kernel_rollback_and_fail(reason: str) -> bool:
            logger.error("VPN_FORWARD 刷新失败: %s，正恢复 VPN_FORWARD 链", reason)
            if not self._iptables_restore_chain_rule_lines(CHAIN_NAME, saved):
                logger.critical("恢复 VPN_FORWARD 链失败，请人工检查 iptables")
            return False

        merged, peer_plan = self._build_merged_forward_entries(rules, center_peers)
        try:
            self._ensure_ipsets_for_merged_forward(merged)
        except RuntimeError as e:
            logger.error("刷新 VPN_FORWARD 前配置 ipset 失败: %s", e)
            return _kernel_rollback_and_fail(f"ipset: {e}")
        try:
            rules_text = self._generate_merged_rules_text(merged)
        except Exception as e:
            logger.error("生成 VPN_FORWARD 规则文本失败: %s", e)
            return _kernel_rollback_and_fail(f"规则生成: {e}")

        try:
            fd, tmp_path = tempfile.mkstemp(prefix="iptables_vpn_forward_", suffix=".rules")
        except OSError as e:
            logger.error("创建 VPN_FORWARD 临时规则文件失败: %s", e)
            return _kernel_rollback_and_fail(f"mkstemp: {e}")

        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(rules_text)

            self._flush_chain()
            result = subprocess.run(
                ["iptables-restore", "--noflush", tmp_path],
                capture_output=True,
                text=True,
                check=False,
                timeout=10,
            )
            if result.returncode != 0:
                err = (result.stderr or result.stdout or "").strip()
                logger.error("VPN_FORWARD iptables-restore 失败: %s", err or "(无 stderr/stdout)")
                return _kernel_rollback_and_fail("iptables-restore 非零")

            center_json = sum(1 for _p, k, _ in merged if k == "j")
            center_peer = sum(1 for _p, k, _ in merged if k == "p")
            logger.info(
                "已仅刷新 VPN_FORWARD：JSON 中心 %d 条；对端内网放行 %d 段；规划对端 JSON %d 条未下发",
                center_json,
                center_peer,
                peer_plan,
            )
            return True
        except subprocess.TimeoutExpired as e:
            logger.error("VPN_FORWARD iptables-restore 执行超时: %s", e)
            return _kernel_rollback_and_fail("iptables-restore 超时")
        except OSError as e:
            logger.error("VPN_FORWARD iptables-restore 执行异常: %s", e)
            return _kernel_rollback_and_fail(f"iptables-restore: {e}")
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

    def reorder_vpn_forward_only(
        self,
        rules: list[FirewallRule],
        *,
        center_peers: Optional[list[tuple]] = None,
    ) -> bool:
        """兼容旧调用名；实际执行 ``VPN_FORWARD`` 专用刷新。"""
        return self.refresh_vpn_forward_only(rules, center_peers=center_peers)

    def rebuild_rules(
        self,
        rules: list[FirewallRule],
        *,
        center_peers: Optional[list[tuple]] = None,
    ) -> bool:
        """按全局 priority 合并 JSON 中心规则与对端内网放行，写入 ``VPN_FORWARD``。

        ``center_peers`` 每项为 ``(peer_id, lan_cidrs, priority, match_extra)``；
        ``match_extra`` 含目标/端口/协议/自定义注释，与对端 JSON 中 ``center_forward_*`` 一致。

        源CIDR → 直接使用 iptables -s（原生支持）
        源IP列表 → ipset + iptables -m set --match-set src
        目标多IP → ipset + iptables -m set --match-set dst

        在 ``flush``/``ipset`` 任一步失败时，用重建前 ``iptables-save -t filter`` 快照恢复内核；
        快照若失败则**不** ``flush``，直接返回 False。
        """
        if os.name == "nt":
            return True
        saved = self._iptables_save_filter_t_bytes()
        if saved is None:
            logger.error("无法备份 filter 表，已中止重建（未对内核作 flush）")
            return False

        def _kernel_rollback_and_fail(reason: str) -> bool:
            logger.error("iptables 重建失败: %s，正用快照恢复 filter 表", reason)
            if not self._iptables_restore_filter_t_bytes(saved):
                logger.critical("从快照恢复 filter 表也失败，请人工检查 iptables")
            return False

        self._flush_chain()
        self._cleanup_managed_ipsets()

        merged, peer_plan = self._build_merged_forward_entries(rules, center_peers)

        try:
            self._ensure_ipsets_for_merged_forward(merged)
        except RuntimeError as e:
            logger.error("ipset 配置失败: %s", e)
            return _kernel_rollback_and_fail(f"ipset: {e}")

        try:
            rules_text = self._generate_merged_rules_text(merged)
        except Exception as e:
            logger.error("生成合并规则文本失败: %s", e)
            return _kernel_rollback_and_fail(f"规则生成: {e}")

        try:
            fd, tmp_path = tempfile.mkstemp(prefix="iptables_", suffix=".rules")
        except OSError as e:
            logger.error("创建临时规则文件失败: %s", e)
            return _kernel_rollback_and_fail(f"mkstemp: {e}")

        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(rules_text)

            result = subprocess.run(
                ["iptables-restore", "--noflush", tmp_path],
                capture_output=True,
                text=True,
                check=False,
                timeout=10,
            )
            if result.returncode != 0:
                err = (result.stderr or result.stdout or "").strip()
                logger.error("iptables-restore 失败: %s", err or "(无 stderr/stdout)")
                return _kernel_rollback_and_fail("iptables-restore 非零")

            if not self._ensure_forward_hooks():
                return _kernel_rollback_and_fail("FORWARD 钩子未就绪")

            if not self._ensure_input_from_vpn():
                return _kernel_rollback_and_fail("INPUT 钩子未就绪")

            self._ensure_ipv4_forward_enabled()
            self._ensure_vpn_nat_masquerade()

            center_json = sum(1 for _p, k, _ in merged if k == "j")
            center_peer = sum(1 for _p, k, _ in merged if k == "p")
            logger.info(
                "iptables 已原子重建 VPN_FORWARD：JSON 中心 %d 条；对端内网放行 %d 段；规划对端 JSON %d 条未下发",
                center_json,
                center_peer,
                peer_plan,
            )
            return True
        except subprocess.TimeoutExpired as e:
            logger.error("iptables-restore 执行超时: %s", e)
            return _kernel_rollback_and_fail("iptables-restore 超时")
        except OSError as e:
            logger.error("iptables-restore 执行异常: %s", e)
            return _kernel_rollback_and_fail(f"iptables-restore: {e}")
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

    @staticmethod
    def _rule_ipset_name(rule_id: str, direction: str = "src") -> str:
        """由规则 ID 和方向（src/dst）派生固定长度且合法的 ipset 名称。"""
        h = hashlib.md5(f"{rule_id}_{direction}".encode("utf-8")).hexdigest()[:20]
        return f"{IPSET_PREFIX}{direction[0]}{h}"

    @staticmethod
    def _is_specific_ip(token: str) -> bool:
        """判断是否为“具体 IP”（非 CIDR）。"""
        t = str(token or "").strip()
        if not t or "/" in t:
            return False
        try:
            ipaddress.ip_address(t)
            return True
        except ValueError:
            return False

    @classmethod
    def _has_multi_specific_ips(cls, tokens: list[str]) -> bool:
        """仅当出现两个及以上具体 IP 时返回 True。"""
        n = 0
        for item in tokens:
            if cls._is_specific_ip(item):
                n += 1
                if n >= 2:
                    return True
        return False

    @classmethod
    def _rule_should_use_dest_ipset(cls, rule: FirewallRule) -> bool:
        """目标侧：只要有多个具体 IP（非 CIDR）则使用 ipset。"""
        if not rule.dest_ip or "," not in rule.dest_ip:
            return False
        tokens = [ip.strip() for ip in str(rule.dest_ip).split(",") if ip.strip()]
        return cls._has_multi_specific_ips(tokens)

    @staticmethod
    def _filter_table_chain_policy(table: str, chain: str) -> str | None:
        """从 iptables-save 读取某链默认策略（ACCEPT / DROP / RETURN 等）。"""
        try:
            r = subprocess.run(
                ["iptables-save", "-t", table],
                capture_output=True,
                text=True,
                timeout=10,
            )
        except (OSError, subprocess.TimeoutExpired) as e:
            logger.debug("读取 iptables-save 失败: %s", e)
            return None
        if r.returncode != 0 or not r.stdout:
            return None
        prefix = f":{chain} "
        for line in r.stdout.splitlines():
            if line.startswith(prefix):
                toks = line.split()
                if len(toks) >= 2:
                    return toks[1]
        return None

    @staticmethod
    def _iptables_delete_rule_repeat(rule_spec: list[str]) -> None:
        """按完整规则规格重复 -D，直到不存在匹配项（用于重排或清理）。"""
        while True:
            r = subprocess.run(
                ["iptables", "-D"] + rule_spec,
                capture_output=True,
                text=True,
                timeout=10,
            )
            if r.returncode != 0:
                break

    @staticmethod
    def _iptables_delete_nat_postrouting_repeat(rule_tail: list[str]) -> None:
        """nat 表 POSTROUTING：按规则尾部重复 -D，直到删净。"""
        while True:
            r = subprocess.run(
                ["iptables", "-t", "nat", "-D", "POSTROUTING"] + rule_tail,
                capture_output=True,
                text=True,
                timeout=10,
            )
            if r.returncode != 0:
                break

    def _delete_nat_postrouting_for_local_masq(self, local_inst_id: str) -> None:
        """删除本机写入的 nat POSTROUTING 规则：无 ``inst=`` 的旧版，或 ``inst=<local_inst_id>`` 的当前版。

        注释中含 ``inst=`` 且非本机 id 的规则保留（供未来远端节点或其它 VPN 实例）。
        """
        try:
            r = subprocess.run(
                ["iptables", "-t", "nat", "-S", "POSTROUTING"],
                capture_output=True,
                text=True,
                timeout=10,
            )
        except (OSError, subprocess.TimeoutExpired) as e:
            logger.warning("读取 nat POSTROUTING 失败: %s", e)
            return
        if r.returncode != 0 or not r.stdout:
            return
        tails: list[list[str]] = []
        for line in r.stdout.splitlines():
            if "ovpn-mgmt-masq" not in line:
                continue
            line = line.strip()
            if not line.startswith("-A POSTROUTING "):
                continue
            try:
                tokens = shlex.split(line)
            except ValueError:
                continue
            if len(tokens) < 3 or tokens[0] != "-A" or tokens[1] != "POSTROUTING":
                continue
            comment_blob = " ".join(tokens)
            if " inst=" in comment_blob and f"inst={local_inst_id}" not in comment_blob:
                continue
            tails.append(tokens[2:])
        for tail in tails:
            self._iptables_delete_nat_postrouting_repeat(tail)

    def remove_ovpn_mgmt_iptables_hooks(self) -> None:
        """删除本模块写入的、带 ``ovpn-mgmt-*`` 注释的固定钩子（FORWARD：对端 ``VPN_PEER_*`` 跳转、VPN_FORWARD、ct、INPUT tun+、nat POSTROUTING MASQUERADE）。

        同时尝试删除升级前无 ``inst=`` 的旧版注释。nat 仅删本机 ``inst=`` 或无 ``inst=`` 的 ovpn-mgmt-masq。

        应在**全部** OpenVPN 实例均已停止后调用（单机单实例下即停服后）。
        """
        if os.name == "nt":
            return
        iid = self._iptables_inst_comment_value()

        pl = self._forward_hook_peer_specs_legacy_no_inst()
        pn = self._forward_hook_peer_specs()
        lj, lc = self._forward_hook_specs_legacy_no_inst()
        self._iptables_delete_rule_repeat(pl)
        self._iptables_delete_rule_repeat(pn)
        self._iptables_delete_rule_repeat(lj)
        self._iptables_delete_rule_repeat(lc)
        jump, ct = self._forward_hook_specs()
        self._iptables_delete_rule_repeat(jump)
        self._iptables_delete_rule_repeat(ct)

        for _ in range(512):
            rules = self._enumerate_forward_rules()
            hit = None
            for ln in rules:
                if "ovpn-mgmt-jump-peer" in ln and "peer=" in ln:
                    hit = ln
                    break
            if not hit:
                break
            if not self._delete_forward_rule_line(hit):
                break

        self._remove_legacy_shared_vpn_peer_jump_and_chain()
        self._flush_delete_chains_matching_prefix(PEER_CHAIN_PREFIX)

        try:
            from app.core.config import load_config

            cidr = str(load_config().get("global_subnet") or "").strip()
        except Exception as e:
            logger.error("读取 global_subnet 失败，跳过 INPUT tun+ 钩子清理: %s", e)
            cidr = ""
        if cidr:
            legacy_in = self._input_tun_rule_parts(cidr, legacy_no_inst=True)
            self._iptables_delete_rule_repeat(legacy_in)
            parts_in = self._input_tun_rule_parts(cidr, legacy_no_inst=False, instance_id=iid)
            self._iptables_delete_rule_repeat(parts_in)

        self._delete_nat_postrouting_for_local_masq(iid)
        logger.info("已尝试清理 ovpn-mgmt FORWARD / INPUT / nat POSTROUTING 钩子规则 (inst=%s)", iid)

    def _resolve_vpn_instance_id(self) -> str:
        """本机逻辑上的 VPN 实例标识（配置 vpn_instance_id / 实例名 / server）。"""
        from app.services.openvpn.instance import get_local_openvpn_instance_id

        return get_local_openvpn_instance_id()

    @staticmethod
    def _sanitize_inst_for_iptables_comment(raw: str) -> str:
        """将实例 id 规范为可写入 xt_comment 的片段（ASCII、无空白与引号、限长）。

        原始逻辑标识仍由 ``_resolve_vpn_instance_id`` 决定；此处仅影响 ``--comment`` 内 ``inst=`` 文本，
        避免非 ASCII、过长或特殊字符导致 Linux 上 ``iptables -A`` 失败，进而使 ``rebuild_rules`` 中途返回 False、
        VPN_FORWARD 已被 flush 却未载入新规则。
        """
        s = str(raw or "").strip() or "server"
        s = re.sub(r"[^\x21-\x7e]", "_", s)
        s = re.sub(r"[\s;\"'\\]", "_", s)
        if not re.sub(r"_+", "", s):
            s = "server"
        if len(s) > _IPTABLES_INST_COMMENT_MAX:
            digest = hashlib.sha256(raw.encode("utf-8", errors="surrogateescape")).hexdigest()[:10]
            keep = _IPTABLES_INST_COMMENT_MAX - 11
            s = f"{s[:keep]}_{digest}"
        return s

    def _iptables_inst_comment_value(self) -> str:
        """与 ``--comment`` 中 ``inst=`` 写入值一致（清理后）；删除/匹配钩子须用同一函数。"""
        return self._sanitize_inst_for_iptables_comment(self._resolve_vpn_instance_id())

    @staticmethod
    def _forward_hook_specs_legacy_no_inst() -> tuple[list[str], list[str]]:
        """升级前无 ``inst=`` 的 FORWARD 钩子（停止/迁移时删除）。"""
        jump = [
            "FORWARD",
            "-i",
            "tun+",
            "-m",
            "comment",
            "--comment",
            "ovpn-mgmt-jump-vpn-forward",
            "-j",
            CHAIN_NAME,
        ]
        ct = [
            "FORWARD",
            "-m",
            "conntrack",
            "--ctstate",
            "RELATED,ESTABLISHED",
            "-m",
            "comment",
            "--comment",
            "ovpn-mgmt-ct-established",
            "-j",
            "ACCEPT",
        ]
        return jump, ct

    def _forward_hook_specs(self) -> tuple[list[str], list[str]]:
        """FORWARD 两条钩子：先跳转 VPN_FORWARD，后放行 RELATED,ESTABLISHED（注释含 inst）。"""
        iid = self._iptables_inst_comment_value()
        jump = [
            "FORWARD",
            "-i",
            "tun+",
            "-m",
            "comment",
            "--comment",
            f"ovpn-mgmt-jump-vpn-forward inst={iid}",
            "-j",
            CHAIN_NAME,
        ]
        ct = [
            "FORWARD",
            "-m",
            "conntrack",
            "--ctstate",
            "RELATED,ESTABLISHED",
            "-m",
            "comment",
            "--comment",
            f"ovpn-mgmt-ct-established inst={iid}",
            "-j",
            "ACCEPT",
        ]
        return jump, ct

    @staticmethod
    def _input_tun_rule_parts(
        cidr: str,
        *,
        legacy_no_inst: bool,
        instance_id: str | None = None,
    ) -> list[str]:
        """INPUT tun+ 放行规则规格（filter 表）。"""
        comment = "ovpn-mgmt-input-tun" if legacy_no_inst else f"ovpn-mgmt-input-tun inst={instance_id}"
        return [
            "INPUT",
            "-i",
            "tun+",
            "-s",
            cidr,
            "-m",
            "comment",
            "--comment",
            comment,
            "-j",
            "ACCEPT",
        ]

    def _forward_hooks_order_ok(self) -> bool:
        """FORWARD：各对端 ``ovpn-mgmt-jump-peer`` 与 ``VPN_FORWARD`` 均在 ct 之前；``VPN_FORWARD`` 在 ct 之前。"""
        try:
            r = subprocess.run(
                ["iptables", "-S", "FORWARD"],
                capture_output=True,
                text=True,
                timeout=10,
            )
        except (OSError, subprocess.TimeoutExpired) as e:
            logger.debug("读取 FORWARD 顺序失败: %s", e)
            return False
        if r.returncode != 0 or not r.stdout:
            return False
        rules = [ln for ln in r.stdout.splitlines() if ln.startswith("-A FORWARD ")]
        jump_idx: int | None = None
        ct_idx: int | None = None
        for i, line in enumerate(rules):
            if "ovpn-mgmt-jump-vpn-forward" in line and f"-j {CHAIN_NAME}" in line:
                jump_idx = i
            if "ovpn-mgmt-ct-established" in line:
                ct_idx = i
        if jump_idx is None or ct_idx is None:
            return False
        if not (jump_idx < ct_idx):
            return False
        return True

    @staticmethod
    def peer_chain_name_for_id(peer_id: str) -> str:
        """SSH 对端主机上的专用链名（稳定、长度合法）：``VPN_PEER_`` + md5 短哈希。

        本函数供 ``remote_peer_iptables`` 与中心侧清理逻辑共用同一命名；**中心不写此链**。
        """
        pid = str(peer_id or "").strip()
        h = hashlib.md5(f"peer_chain:{pid}".encode("utf-8")).hexdigest()[:12]
        return f"{PEER_CHAIN_PREFIX}{h}"

    CENTER_PEER_COMMENT_TAG = "ovpn-mgmt-center-peer"

    def _purge_center_vpn_peer_subchains(self) -> None:
        """移除本机误建的 ``VPN_PEER_*`` 用户链及 FORWARD 中 ``-j VPN_PEER_*`` 跳转（该链仅存在于 SSH 对端）。"""
        if os.name == "nt":
            return
        for _ in range(256):
            rules = self._enumerate_forward_rules()
            found = None
            for ln in rules:
                try:
                    toks = shlex.split(ln)
                except ValueError:
                    continue
                if "-j" in toks:
                    ji = toks.index("-j")
                    if ji + 1 < len(toks) and str(toks[ji + 1]).startswith(PEER_CHAIN_PREFIX):
                        found = ln
                        break
            if not found:
                break
            if not self._delete_forward_rule_line(found):
                logger.error("无法删除指向 %s 的 FORWARD 跳转", PEER_CHAIN_PREFIX)
                break
        self._flush_delete_chains_matching_prefix(PEER_CHAIN_PREFIX)

    def _forward_hook_peer_specs_legacy_no_inst(self) -> list[str]:
        """旧版共享 VPN_PEER 跳转（迁移清理用）。"""
        return [
            "FORWARD",
            "-i",
            "tun+",
            "-m",
            "comment",
            "--comment",
            "ovpn-mgmt-jump-vpn-peer",
            "-j",
            LEGACY_CHAIN_PEER,
        ]

    def _forward_hook_peer_specs(self) -> list[str]:
        """旧版带 inst 的共享 VPN_PEER 跳转（迁移清理用）。"""
        iid = self._iptables_inst_comment_value()
        return [
            "FORWARD",
            "-i",
            "tun+",
            "-m",
            "comment",
            "--comment",
            f"ovpn-mgmt-jump-vpn-peer inst={iid}",
            "-j",
            LEGACY_CHAIN_PEER,
        ]

    def _remove_legacy_shared_vpn_peer_jump_and_chain(self) -> None:
        """删除旧版 FORWARD -> 共享 ``VPN_PEER`` 跳转并尝试移除空链。"""
        pl = self._forward_hook_peer_specs_legacy_no_inst()
        pn = self._forward_hook_peer_specs()
        self._iptables_delete_rule_repeat(pl)
        self._iptables_delete_rule_repeat(pn)
        subprocess.run(
            ["iptables", "-F", LEGACY_CHAIN_PEER],
            capture_output=True,
            text=True,
            timeout=10,
        )
        subprocess.run(
            ["iptables", "-X", LEGACY_CHAIN_PEER],
            capture_output=True,
            text=True,
            timeout=10,
        )

    def _flush_delete_chains_matching_prefix(self, prefix: str) -> None:
        """清空并删除 filter 表中用户链名以 ``prefix`` 开头的链（如 ``VPN_PEER_<hash>``）。"""
        if os.name == "nt":
            return
        try:
            r = subprocess.run(
                ["iptables-save", "-t", "filter"],
                capture_output=True,
                text=True,
                timeout=10,
            )
        except (OSError, subprocess.TimeoutExpired) as e:
            logger.warning("读取 iptables-save 失败: %s", e)
            return
        if r.returncode != 0 or not r.stdout:
            return
        names: list[str] = []
        for line in r.stdout.splitlines():
            if not line.startswith(":"):
                continue
            nm = line[1:].split()[0]
            if nm.startswith(prefix):
                names.append(nm)
        for nm in sorted(set(names), reverse=True):
            subprocess.run(
                ["iptables", "-F", nm],
                capture_output=True,
                text=True,
                timeout=10,
            )
            subprocess.run(
                ["iptables", "-X", nm],
                capture_output=True,
                text=True,
                timeout=10,
            )

    def _enumerate_forward_rules(self) -> list[str]:
        """返回 ``iptables -S FORWARD`` 中每条 ``-A FORWARD`` 行（有序）。"""
        r = subprocess.run(
            ["iptables", "-S", "FORWARD"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if r.returncode != 0 or not r.stdout:
            return []
        return [ln for ln in r.stdout.splitlines() if ln.startswith("-A FORWARD ")]

    def _delete_forward_rule_line(self, line: str) -> bool:
        """将 ``-A FORWARD ...`` 转为 ``-D FORWARD ...`` 删除一条规则。"""
        s = line.strip()
        if not s.startswith("-A FORWARD "):
            return False
        try:
            parts = shlex.split(s)
        except ValueError:
            return False
        if len(parts) < 3 or parts[0] != "-A" or parts[1] != "FORWARD":
            return False
        dr = subprocess.run(
            ["iptables", "-D"] + parts[2:],
            capture_output=True,
            text=True,
            timeout=10,
        )
        return dr.returncode == 0

    def _ensure_forward_hooks(self) -> bool:
        """FORWARD：先迁移掉旧版共享 ``VPN_PEER``；再 ``tun+ -> VPN_FORWARD``；再 ct。

        并清理本机误建的 ``VPN_PEER_*``（该链仅应存在于 SSH 对端）。

        返回:
            True 表示成功；失败时记录日志并返回 False。
        """
        if os.name == "nt":
            return True
        self._remove_legacy_shared_vpn_peer_jump_and_chain()
        self._purge_center_vpn_peer_subchains()

        lj, lc = self._forward_hook_specs_legacy_no_inst()
        jump, ct = self._forward_hook_specs()
        if self._forward_hooks_order_ok():
            return True

        self._iptables_delete_rule_repeat(lj)
        self._iptables_delete_rule_repeat(lc)
        self._iptables_delete_rule_repeat(jump)
        self._iptables_delete_rule_repeat(ct)

        for parts in (jump, ct):
            add_cmd = ["iptables", "-A"] + parts
            ar = subprocess.run(
                add_cmd,
                capture_output=True,
                text=True,
                timeout=10,
            )
            if ar.returncode != 0:
                logger.error(
                    "无法写入 FORWARD 钩子: %s, stderr=%s",
                    " ".join(add_cmd),
                    (ar.stderr or "").strip(),
                )
                return False
            logger.info("已追加 FORWARD 钩子: %s", " ".join(parts[1:]))
        return True

    def ensure_forward_hooks_with_peer(self) -> bool:
        """与 ``_ensure_forward_hooks`` 相同，供对端服务显式调用。"""
        return self._ensure_forward_hooks()

    def _ensure_input_from_vpn(self) -> bool:
        """INPUT 默认策略非 ACCEPT 时，放行从 TUN 进入且源为 VPN 地址池的入站流量。

        INPUT 为 ACCEPT 时默认已允许全部入站，无需再配规则；并尝试删除本模块曾追加的冗余规则。
        """
        if os.name == "nt":
            return True
        try:
            from app.core.config import load_config

            cidr = str(load_config().get("global_subnet") or "").strip()
        except Exception as e:
            logger.error("读取 global_subnet 失败，跳过 INPUT 钩子: %s", e)
            return False
        if not cidr:
            logger.warning("global_subnet 未配置，跳过 INPUT tun+ 放行")
            return True

        iid = self._iptables_inst_comment_value()
        parts_legacy = self._input_tun_rule_parts(cidr, legacy_no_inst=True)
        parts = self._input_tun_rule_parts(cidr, legacy_no_inst=False, instance_id=iid)

        policy = self._filter_table_chain_policy("filter", "INPUT")
        if policy == "ACCEPT":
            self._iptables_delete_rule_repeat(parts_legacy)
            self._iptables_delete_rule_repeat(parts)
            logger.debug("INPUT 默认策略为 ACCEPT，跳过追加 tun+ 规则")
            return True

        check_cmd = ["iptables", "-C"] + parts
        cr = subprocess.run(
            check_cmd,
            capture_output=True,
            text=True,
            timeout=10,
        )
        if cr.returncode == 0:
            return True
        self._iptables_delete_rule_repeat(parts_legacy)
        add_cmd = ["iptables", "-A"] + parts
        ar = subprocess.run(
            add_cmd,
            capture_output=True,
            text=True,
            timeout=10,
        )
        if ar.returncode != 0:
            logger.error(
                "无法追加 INPUT tun+ 放行: %s, stderr=%s",
                " ".join(add_cmd),
                (ar.stderr or "").strip(),
            )
            return False
        logger.info("已追加 INPUT 放行: -i tun+ -s %s inst=%s", cidr, iid)
        return True

    @staticmethod
    def _default_ipv4_egress_dev() -> str | None:
        """解析当前默认 IPv4 路由的出接口（历史兼容；中心 NAT 已不按接口区分）。"""
        try:
            r = subprocess.run(
                ["ip", "-4", "route", "show", "default"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if r.returncode != 0 or not r.stdout.strip():
                return None
            line = r.stdout.strip().splitlines()[0]
            if " dev " not in line:
                return None
            return line.split(" dev ", 1)[1].split()[0]
        except (OSError, subprocess.TimeoutExpired, IndexError) as e:
            logger.debug("解析默认路由失败: %s", e)
            return None

    @staticmethod
    def _ensure_ipv4_forward_enabled() -> None:
        """开启 IPv4 转发；无权限或非 Linux 时仅打日志。"""
        if os.name == "nt":
            return
        proc = "/proc/sys/net/ipv4/ip_forward"
        try:
            with open(proc, "r", encoding="ascii") as f:
                if f.read().strip() == "1":
                    return
            with open(proc, "w", encoding="ascii") as f:
                f.write("1")
            logger.info("已启用 net.ipv4.ip_forward=1（运行时）")
        except OSError as e:
            logger.error(
                "无法启用 ip_forward（VPN 转发依赖此项）: %s；请手动: sysctl -w net.ipv4.ip_forward=1",
                e,
            )

    def _ensure_vpn_nat_masquerade(self) -> None:
        """为全局 VPN 源地址做 MASQUERADE。

        使用 ``-s <global_subnet>`` 匹配所有目标；不指定 ``-o``，与默认路由/多网卡出向无关。
        """
        if os.name == "nt":
            return
        try:
            from app.core.config import load_config

            sys_cfg = load_config()
            cidr = str(sys_cfg.get("global_subnet") or "").strip()
        except Exception as e:
            logger.error("读取 VPN/NAT 相关配置失败，跳过 MASQUERADE: %s", e)
            return
        if not cidr:
            logger.warning(
                "global_subnet 未配置，跳过 VPN MASQUERADE；"
                "客户端经本机访问其它网段时回程可能不可达"
            )
            return
        iid = self._iptables_inst_comment_value()
        self._delete_nat_postrouting_for_local_masq(iid)
        parts = [
            "POSTROUTING",
            "-s",
            cidr,
            "-m",
            "comment",
            "--comment",
            f"ovpn-mgmt-masq inst={iid}",
            "-j",
            "MASQUERADE",
        ]
        check = ["iptables", "-t", "nat", "-C"] + parts
        cr = subprocess.run(check, capture_output=True, text=True, timeout=10)
        if cr.returncode == 0:
            return
        add = ["iptables", "-t", "nat", "-A"] + parts
        ar = subprocess.run(add, capture_output=True, text=True, timeout=10)
        if ar.returncode != 0:
            logger.error(
                "无法追加 nat MASQUERADE: %s, stderr=%s",
                " ".join(add),
                (ar.stderr or "").strip(),
            )
            return
        logger.info(
            "已追加 nat MASQUERADE: -s %s inst=%s",
            cidr,
            iid,
        )

    @staticmethod
    def _iptables_save_filter_t_bytes() -> bytes | None:
        """当前 ``filter`` 表快照，供失败时 :meth:`_iptables_restore_filter_t_bytes` 回滚。失败返回 None。"""
        try:
            r = subprocess.run(
                ["iptables-save", "-t", "filter"],
                capture_output=True,
                timeout=30,
            )
        except (OSError, subprocess.TimeoutExpired) as e:
            logger.error("iptables-save 失败: %s", e)
            return None
        if r.returncode != 0:
            logger.error("iptables-save 非零: %s", (r.stderr or b"")[:1000].decode("utf-8", errors="replace"))
            return None
        return r.stdout

    @staticmethod
    def _iptables_save_chain_rule_lines(chain_name: str) -> list[str] | None:
        """仅保存指定链的 ``-A <chain> ...`` 规则行。"""
        try:
            r = subprocess.run(
                ["iptables", "-S", chain_name],
                capture_output=True,
                text=True,
                timeout=10,
            )
        except (OSError, subprocess.TimeoutExpired) as e:
            logger.error("iptables -S %s 失败: %s", chain_name, e)
            return None
        if r.returncode != 0:
            logger.error("iptables -S %s 非零: %s", chain_name, (r.stderr or "").strip())
            return None
        return [
            line.strip()
            for line in r.stdout.splitlines()
            if line.strip().startswith(f"-A {chain_name} ")
        ]

    def _iptables_restore_chain_rule_lines(self, chain_name: str, lines: list[str]) -> bool:
        """仅恢复指定链规则，不触碰其它 filter 链。"""
        try:
            subprocess.run(
                ["iptables", "-F", chain_name],
                capture_output=True,
                text=True,
                timeout=10,
            )
            for line in lines:
                try:
                    parts = shlex.split(line)
                except ValueError as exc:
                    logger.error("解析 %s 快照规则失败: %s", chain_name, exc)
                    return False
                if len(parts) < 3 or parts[0] != "-A" or parts[1] != chain_name:
                    logger.error("非法 %s 快照规则: %s", chain_name, line)
                    return False
                r = subprocess.run(
                    ["iptables", "-A", chain_name] + parts[2:],
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                if r.returncode != 0:
                    logger.error(
                        "恢复 %s 规则失败: %s, stderr=%s",
                        chain_name,
                        line,
                        (r.stderr or "").strip(),
                    )
                    return False
        except (OSError, subprocess.TimeoutExpired) as e:
            logger.error("恢复 %s 链异常: %s", chain_name, e)
            return False
        return True

    @staticmethod
    def _iptables_restore_filter_t_bytes(data: bytes) -> bool:
        """用快照恢复 ``filter`` 表。成功返回 True。"""
        try:
            r = subprocess.run(
                ["iptables-restore"],
                input=data,
                capture_output=True,
                timeout=60,
            )
        except (OSError, subprocess.TimeoutExpired) as e:
            logger.error("iptables-restore(回滚) 执行失败: %s", e)
            return False
        if r.returncode != 0:
            emsg = (r.stderr or b"")[:2000].decode("utf-8", errors="replace")
            logger.error("iptables-restore(回滚) 失败: %s", emsg)
            return False
        return True

    def _flush_chain(self) -> None:
        """清空 VPN_FORWARD 链，解除 iptables 对 ipset 的引用。"""
        try:
            subprocess.run(
                ["iptables", "-F", CHAIN_NAME],
                capture_output=True, text=True, timeout=10,
            )
            logger.debug("已 flush 链 %s", CHAIN_NAME)
        except (OSError, subprocess.TimeoutExpired) as e:
            logger.warning("flush 链 %s 失败（链可能不存在）: %s", CHAIN_NAME, e)

    @staticmethod
    def _peer_lan_ipset_name(peer_id: str) -> str:
        """对端内网 CIDR 集合名（hash:net），与防火墙 JSON ipset 前缀隔离。"""
        h = hashlib.md5(f"peer_lan_{peer_id}".encode("utf-8")).hexdigest()[:20]
        return f"{IPSET_PREFIX_PEER}l{h}"

    def sync_peer_vpn_center_rule(self, peer_id: str, lan_cidrs: list[str]) -> bool:
        """已由 ``FirewallRuleService`` 合并写入 ``VPN_FORWARD``；此处只刷新该链。"""
        if os.name == "nt":
            return True
        from app.services.firewall.rule_service import FirewallRuleService

        FirewallRuleService().refresh_vpn_forward_only()
        return True

    def remove_peer_vpn_center_rules(self, peer_id: str) -> None:
        """已由 ``VPN_FORWARD`` 刷新覆盖；保留接口供调用方触发。"""
        if os.name == "nt":
            return
        from app.services.firewall.rule_service import FirewallRuleService

        FirewallRuleService().refresh_vpn_forward_only()
        logger.info("已请求刷新 VPN_FORWARD（remove_peer_vpn_center_rules peer=%s）", peer_id)

    def _cleanup_managed_ipsets(self) -> None:
        """删除本模块创建的 ipset 集合。"""
        try:
            result = subprocess.run(
                ["ipset", "list", "-n"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode != 0:
                return
            for name in result.stdout.splitlines():
                if name.startswith(IPSET_PREFIX):
                    subprocess.run(
                        ["ipset", "destroy", name],
                        capture_output=True,
                        text=True,
                        timeout=5,
                    )
        except (OSError, subprocess.TimeoutExpired) as e:
            logger.warning("清理 ipset 集合失败: %s", e)

    def _ipset_ensure_add_multi(self, set_name: str, ips: list[str]) -> None:
        """创建 hash:net 集合并写入多条 IP/CIDR。"""
        subprocess.run(
            ["ipset", "create", set_name, "hash:net", "family", "inet", "-exist"],
            capture_output=True, text=True, timeout=10,
        )
        subprocess.run(
            ["ipset", "flush", set_name],
            capture_output=True, text=True, timeout=10,
        )
        for ip in ips:
            # 单个 IP 需要加 /32 后缀
            entry = ip if "/" in ip else f"{ip}/32"
            add_result = subprocess.run(
                ["ipset", "add", set_name, entry],
                capture_output=True, text=True, timeout=10,
            )
            if add_result.returncode != 0:
                raise RuntimeError(f"ipset add 失败: {set_name} <- {entry}: {add_result.stderr}")

    def _generate_rules_text(self, rules: list[FirewallRule]) -> str:
        """生成 iptables-save 格式的规则文本。"""
        lines: list[str] = []
        lines.append("*filter")
        lines.append(f":{CHAIN_NAME} - [0:0]")

        for rule in rules:
            if not rule.enabled:
                continue
            if rule.deployment_target != "center":
                continue
            for line in self._expand_rule_lines(rule):
                lines.append(line)

        lines.append("COMMIT")
        lines.append("")
        return "\n".join(lines)

    def _generate_merged_rules_text(self, merged: list[tuple[int, str, object]]) -> str:
        """按 ``merged`` 顺序生成 ``VPN_FORWARD`` 的 iptables-save 片段。"""
        lines: list[str] = []
        lines.append("*filter")
        lines.append(f":{CHAIN_NAME} - [0:0]")
        for _pri, kind, payload in merged:
            if kind == "j":
                rule = payload
                assert isinstance(rule, FirewallRule)
                if not rule.enabled or rule.deployment_target != "center":
                    continue
                for line in self._expand_rule_lines(rule):
                    lines.append(line)
            else:
                pid, cln, extra = payload  # type: ignore[misc]
                lines.extend(
                    self._expand_center_peer_forward_lines(
                        str(pid), list(cln), extra=extra if isinstance(extra, dict) else {}
                    )
                )
        lines.append("COMMIT")
        lines.append("")
        return "\n".join(lines)

    def _expand_center_peer_forward_lines(
        self,
        peer_id: str,
        lan_cidrs: list[str],
        *,
        extra: Optional[dict] = None,
    ) -> list[str]:
        """中心侧半幅：放行源为对端内网的转发；可选目标/端口/协议/注释与 JSON 中心规则同构。"""
        ex = extra if isinstance(extra, dict) else {}
        pid = str(peer_id or "").strip()
        cleaned = [str(c).strip() for c in lan_cidrs if str(c).strip()]
        if not cleaned:
            return []
        c_raw = f"{self.CENTER_PEER_COMMENT_TAG} peer={pid}"
        c_default = c_raw[:256].replace('"', "'")
        udesc = str(ex.get("rule_description") or "").strip()
        final_comment = (udesc[:256].replace('"', "'")) if udesc else c_default
        dest_ip = str(ex.get("dest_ip") or "").strip()
        dest_port = str(ex.get("dest_port") or "").strip()
        protocol = str(ex.get("protocol") or "all").strip().lower()
        if protocol == "any":
            protocol = "all"
        has_l4_match = bool(dest_ip) or bool(dest_port) or (protocol not in ("all", ""))
        if not has_l4_match:
            out: list[str] = []
            if self._has_multi_specific_ips(cleaned):
                set_name = self._peer_lan_ipset_name(pid)
                line = (
                    f"-A {CHAIN_NAME} -m set --match-set {set_name} src "
                    f'-m comment --comment "{final_comment}" -j ACCEPT'
                )
                out.append(line)
            else:
                for tok in cleaned:
                    line = (
                        f"-A {CHAIN_NAME} -s {tok} "
                        f'-m comment --comment "{final_comment}" -j ACCEPT'
                    )
                    out.append(line)
            return out
        rule_id = f"ce-peer-{pid}"
        try:
            fr = FirewallRule(
                id=rule_id,
                owner_type="group",
                owner_id="_",
                action="accept",
                priority=1,
                deployment_target="center",
                source_subnet="0.0.0.0/0",
                dest_ip=dest_ip or None,
                dest_port=dest_port or None,
                protocol=protocol,
                description=final_comment,
            )
        except ValidationError as exc:
            logger.error("对端中心放行目标/端口与协议不合法 peer=%s: %s", pid, exc)
            raise RuntimeError(f"对端 {pid[:8]} 中心侧匹配条件不合法: {exc}") from exc
        pvs = self._protocol_port_variants(fr)
        dest_tokens = [ip.strip() for ip in str(fr.dest_ip or "").split(",") if ip.strip()]
        use_dest_ipset = self._has_multi_specific_ips(dest_tokens)
        if not dest_tokens:
            dest_loop: list[str | None] = [None]
        elif use_dest_ipset:
            dest_loop = [None]
        else:
            dest_loop = list(dest_tokens)
        out2: list[str] = []
        if self._has_multi_specific_ips(cleaned):
            set_src = self._peer_lan_ipset_name(pid)
            for dest in dest_loop:
                for pv in pvs:
                    parts = [f"-A {CHAIN_NAME}", f"-m set --match-set {set_src} src"]
                    if use_dest_ipset:
                        parts.append(f"-m set --match-set {self._rule_ipset_name(rule_id, 'dst')} dst")
                    elif dest:
                        parts.append(f"-d {dest}")
                    if pv.get("proto"):
                        parts.append(f"-p {pv['proto']}")
                    if pv.get("multiport"):
                        parts.append(f"-m multiport --dports {pv['multiport']}")
                    elif pv.get("dport"):
                        parts.append(f"--dport {pv['dport']}")
                    if fr.description:
                        parts.append(f'-m comment --comment "{final_comment}"')
                    parts.append("-j ACCEPT")
                    out2.append(" ".join(parts))
        else:
            for tok in cleaned:
                for dest in dest_loop:
                    for pv in pvs:
                        parts = [f"-A {CHAIN_NAME}", f"-s {tok}"]
                        if use_dest_ipset:
                            parts.append(
                                f"-m set --match-set {self._rule_ipset_name(rule_id, 'dst')} dst"
                            )
                        elif dest:
                            parts.append(f"-d {dest}")
                        if pv.get("proto"):
                            parts.append(f"-p {pv['proto']}")
                        if pv.get("multiport"):
                            parts.append(f"-m multiport --dports {pv['multiport']}")
                        elif pv.get("dport"):
                            parts.append(f"--dport {pv['dport']}")
                        if fr.description:
                            parts.append(f'-m comment --comment "{final_comment}"')
                        parts.append("-j ACCEPT")
                        out2.append(" ".join(parts))
        return out2

    def _expand_rule_lines(self, rule: FirewallRule) -> list[str]:
        """将单条业务规则展开为一条或多条 iptables 行。

        源/目标地址策略：
        - 任一侧出现“多个具体 IP（非 CIDR）”→ 该侧使用 ipset + ``-m set --match-set``
        - 其它情况（CIDR/单 IP）→ 直接使用 ``-s/-d``（按列表展开）

        仅当 ``rule.description`` 非空时附加 ``-m comment``（无默认注释）。
        """
        action_map = {"accept": "ACCEPT", "drop": "DROP", "reject": "REJECT"}
        target = action_map.get(rule.action, "DROP")

        src_tokens = [str(ip).strip() for ip in (rule.source_ips or []) if str(ip).strip()]
        dest_tokens = [ip.strip() for ip in str(rule.dest_ip or "").split(",") if ip.strip()]
        use_src_ipset = self._has_multi_specific_ips(src_tokens)
        use_dest_ipset = self._has_multi_specific_ips(dest_tokens)

        src_terms: list[str | None]
        if use_src_ipset:
            src_terms = [None]
        elif src_tokens:
            src_terms = src_tokens
        elif rule.source_subnet:
            src_terms = [rule.source_subnet]
        else:
            src_terms = [None]

        dest_terms: list[str | None]
        if use_dest_ipset:
            dest_terms = [None]
        elif dest_tokens:
            dest_terms = dest_tokens
        else:
            dest_terms = [None]

        proto_variants = self._protocol_port_variants(rule)
        out: list[str] = []
        for src in src_terms:
            for dest in dest_terms:
                for pv in proto_variants:
                    parts = [f"-A {CHAIN_NAME}"]
                    if use_src_ipset:
                        parts.append(f"-m set --match-set {self._rule_ipset_name(rule.id, 'src')} src")
                    elif src:
                        parts.append(f"-s {src}")
                    if use_dest_ipset:
                        parts.append(f"-m set --match-set {self._rule_ipset_name(rule.id, 'dst')} dst")
                    elif dest:
                        parts.append(f"-d {dest}")
                    if pv["proto"]:
                        parts.append(f"-p {pv['proto']}")
                    if pv.get("multiport"):
                        parts.append(f"-m multiport --dports {pv['multiport']}")
                    elif pv.get("dport"):
                        parts.append(f"--dport {pv['dport']}")
                    if rule.description:
                        comment = rule.description[:256].replace('"', "'")
                        parts.append(f'-m comment --comment "{comment}"')
                    parts.append(f"-j {target}")
                    out.append(" ".join(parts))
        return out

    @staticmethod
    def _protocol_port_variants(rule: FirewallRule) -> list[dict]:
        """生成协议与端口组合。

        协议为 all 时不指定 -p（iptables 默认匹配所有协议）。
        有端口时需要指定具体协议（tcp/udp），all 会展开为 tcp+udp 两条。
        """
        dest_port = (rule.dest_port or "").strip()
        proto = (rule.protocol or "all").strip().lower()

        # 兼容旧数据中的 "any"
        if proto == "any":
            proto = "all"

        if not dest_port:
            if proto == "all":
                return [{"proto": None, "dport": None, "multiport": None}]
            return [{"proto": proto, "dport": None, "multiport": None}]

        if "," in dest_port:
            ports = ",".join(p.strip() for p in dest_port.split(",") if p.strip())
            if proto == "all":
                return [
                    {"proto": "tcp", "dport": None, "multiport": ports},
                    {"proto": "udp", "dport": None, "multiport": ports},
                ]
            return [{"proto": proto, "dport": None, "multiport": ports}]

        if ":" in dest_port and dest_port.count(":") == 1:
            if proto == "all":
                return [
                    {"proto": "tcp", "dport": dest_port, "multiport": None},
                    {"proto": "udp", "dport": dest_port, "multiport": None},
                ]
            return [{"proto": proto, "dport": dest_port, "multiport": None}]

        if proto == "all":
            return [
                {"proto": "tcp", "dport": dest_port, "multiport": None},
                {"proto": "udp", "dport": dest_port, "multiport": None},
            ]
        return [{"proto": proto, "dport": dest_port, "multiport": None}]

    def export_rules(self) -> str:
        """导出当前 iptables 规则为 JSON 格式。"""
        try:
            result = subprocess.run(
                ["iptables-save", "-t", "filter"],
                capture_output=True,
                text=True,
                check=True,
                timeout=10,
            )
            vpn_lines = []
            for line in result.stdout.splitlines():
                if CHAIN_NAME in line or line.startswith("*") or line == "COMMIT":
                    vpn_lines.append(line)

            export_data = {
                "chain": CHAIN_NAME,
                "rules_text": "\n".join(vpn_lines),
            }
            return json.dumps(export_data, ensure_ascii=False, indent=2)
        except subprocess.CalledProcessError as e:
            logger.error("导出 iptables 规则失败: %s", e.stderr)
            return json.dumps({"error": str(e)}, ensure_ascii=False)

    def import_rules(self, rules_json: str) -> bool:
        """从 JSON 导入并恢复 iptables 规则。"""
        try:
            data = json.loads(rules_json)
        except json.JSONDecodeError as e:
            logger.error("导入规则 JSON 解析失败: %s", e)
            return False

        rules_text = data.get("rules_text", "")
        if not rules_text:
            logger.error("导入数据中缺少 rules_text 字段")
            return False

        fd, tmp_path = tempfile.mkstemp(prefix="iptables_import_", suffix=".rules")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(rules_text + "\n")

            result = subprocess.run(
                ["iptables-restore", "--noflush", tmp_path],
                capture_output=True,
                text=True,
                check=False,
                timeout=10,
            )
            if result.returncode != 0:
                logger.error("iptables-restore 导入失败: %s", result.stderr)
                return False

            if not self._ensure_forward_hooks():
                return False

            if not self._ensure_input_from_vpn():
                return False

            self._ensure_ipv4_forward_enabled()
            self._ensure_vpn_nat_masquerade()

            logger.info("iptables 规则已从 JSON 导入恢复")
            return True
        except (subprocess.TimeoutExpired, OSError) as e:
            logger.error("iptables-restore 导入异常: %s", e)
            return False
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
