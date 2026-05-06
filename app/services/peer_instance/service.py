# -*- coding: utf-8 -*-
"""对端站点实例：持久化、CCD iroute、中心侧 VPN_FORWARD / SSH 对端 VPN_PEER_* iptables 同步"""
from __future__ import annotations

import logging
import os
import uuid
from datetime import datetime
from pathlib import Path

from app.core.constants import CCD_DIR, OVPN_PROFILES_DIR, PEERS_DIR
from app.models.peer_instance import PeerInstance
from app.services.firewall.iptables_mgr import IptablesManager
from app.services.peer_instance.ccd_merge import (
    merge_mesh_peer_push_routes_into_ccd,
    merge_peer_block_into_ccd,
    sorted_unique_ipv4_cidrs,
    strip_peer_block_from_ccd,
)
from app.utils.file_lock import read_json, write_json_atomic
from app.utils.posix_data_perms import fix_path_for_openvpn_shared_data

logger = logging.getLogger(__name__)
peer_remote_log = logging.getLogger("peer.remote")


def _vpn_forward_wire_snapshot(row: dict) -> tuple:
    """用于判断中心 ``VPN_FORWARD``（对端内网放行）是否要重算的指纹；与 ``FirewallRuleService._collect_center_peers`` 字段一致。"""
    raw_lan = [str(x).strip() for x in (row.get("lan_cidrs") or []) if str(x).strip()]
    lan_t = tuple(sorted_unique_ipv4_cidrs(raw_lan))
    return (
        lan_t,
        bool(row.get("center_forward_enabled", True)),
        int(row.get("center_forward_priority", 500_000)),
        str(row.get("center_forward_protocol") or "all").strip().lower() or "all",
        str(row.get("center_forward_dest_ip") or "").strip(),
        str(row.get("center_forward_dest_port") or "").strip(),
        str(row.get("center_forward_rule_description") or "").strip(),
    )


def _mesh_push_wire_snapshot(row: dict) -> tuple:
    """mesh 向各用户 CCD 下发 push route 时对单条对端实例起作用的指纹：后方 LAN + 路由可见组。"""
    raw_lan = [str(x).strip() for x in (row.get("lan_cidrs") or []) if str(x).strip()]
    lan_t = tuple(sorted_unique_ipv4_cidrs(raw_lan))
    mugs = tuple(sorted(str(x).strip() for x in (row.get("mesh_route_visible_group_ids") or []) if str(x).strip()))
    return (lan_t, mugs)


def _ccd_iroute_wire_snapshot(row: dict) -> tuple:
    """绑定用户 CCD 中对端 iroute 块内容相关指纹。"""
    raw_lan = [str(x).strip() for x in (row.get("lan_cidrs") or []) if str(x).strip()]
    lan_t = tuple(sorted_unique_ipv4_cidrs(raw_lan))
    u = str(row.get("bound_username") or "").strip()
    return (u, lan_t)


def mesh_lan_cidrs_for_user_group(peers_rows: list[dict], user_group_id: str) -> list[str]:
    """根据对端上的 mesh 可见组配置，计算某用户组应下发的 LAN CIDR 并集（IPv4 规范化）。

    规则：某对端 ``lan_cidrs`` 非空时，若 ``mesh_route_visible_group_ids`` 为空则对所有组用户下发；
    若非空则仅当 ``user_group_id`` 在该列表中时才下发该对端的 LAN。
    """
    ug = str(user_group_id or "").strip()
    acc: list[str] = []
    for row in peers_rows:
        lan = list(row.get("lan_cidrs") or [])
        if not lan:
            continue
        visible = [str(x).strip() for x in (row.get("mesh_route_visible_group_ids") or []) if str(x).strip()]
        if not visible:
            acc.extend(lan)
        elif ug and ug in visible:
            acc.extend(lan)
    return sorted_unique_ipv4_cidrs(acc)


class PeerService:
    """对端实例 CRUD；绑定用户须已存在且已有 CCD。"""

    def __init__(self) -> None:
        PEERS_DIR.mkdir(parents=True, exist_ok=True)

    def list_peer_lan_firewall_presets_for_center_form(self) -> list[dict]:
        """供中心规则表单下拉选择对端内网 CIDR，列出所有已配置 LAN 的对端。"""
        out: list[dict] = []
        for row in self.list_all():
            pid = str(row.get("id") or "")
            if not pid:
                continue
            pname = str(row.get("name") or pid)
            for c in row.get("lan_cidrs") or []:
                cidr = str(c).strip()
                if not cidr:
                    continue
                key = f"{pid}::{cidr}"
                out.append(
                    {
                        "key": key,
                        "label": f"{cidr}（对端：{pname}）",
                        "cidr": cidr,
                        "peer_id": pid,
                        "peer_name": pname,
                    }
                )
        return out

    def export_peer_manual_markdown(self, peer_id: str) -> str:
        """生成对端站点手动部署说明 Markdown（中文）。

        Raises:
            ValueError: 对端不存在
        """
        from app.core.config import load_config
        from app.services.peer_instance.peer_manual_md import build_peer_site_manual_markdown

        row = self.get(peer_id)
        if not row:
            raise ValueError(f"对端实例不存在: {peer_id}")
        cfg = load_config()
        gs = str(cfg.get("global_subnet") or "").strip()
        return build_peer_site_manual_markdown(
            peer_name=str(row.get("name") or peer_id),
            peer_id=str(row.get("id") or peer_id),
            bound_username=str(row.get("bound_username") or ""),
            lan_cidrs=list(row.get("lan_cidrs") or []),
            global_subnet=gs,
            masquerade_on_peer=bool(row.get("masquerade_on_peer")),
        )

    def export_peer_manual_context(self, peer_id: str) -> dict:
        """生成对端站点部署说明页面所需结构化数据。

        Args:
            peer_id: 对端实例 ID。

        Returns:
            包含 overview、highlights、steps、commands 的字典。

        Raises:
            ValueError: 对端实例不存在。
        """
        from app.core.config import load_config
        from app.services.peer_instance.peer_manual_md import build_peer_site_manual_context

        row = self.get(peer_id)
        if not row:
            raise ValueError(f"对端实例不存在: {peer_id}")
        cfg = load_config()
        gs = str(cfg.get("global_subnet") or "").strip()
        return build_peer_site_manual_context(
            peer_name=str(row.get("name") or peer_id),
            peer_id=str(row.get("id") or peer_id),
            bound_username=str(row.get("bound_username") or ""),
            lan_cidrs=list(row.get("lan_cidrs") or []),
            global_subnet=gs,
            masquerade_on_peer=bool(row.get("masquerade_on_peer")),
        )

    def probe_openvpn_via_ssh(
        self,
        peer_id: str,
        *,
        ssh_username: str | None = None,
        ssh_password: str | None = None,
        ssh_private_key: str | None = None,
        ssh_private_key_passphrase: str | None = None,
        ssh_key_path: str | None = None,
        ssh_openvpn_binary: str | None = None,
    ) -> dict:
        """从已保存的对端读取 ssh_host/port 与落库凭据，经 SSH 检测远端 OpenVPN 客户端（无 PKI）。

        未传入的认证参数使用对端 JSON 中已存字段；显式传入则覆盖。
        ``ssh_openvpn_binary`` 显式传入（含空字符串）时覆盖落库的 ``ssh_openvpn_binary``；为 ``None`` 时仅用落库值。
        """
        from app.services.peer_instance.remote_openvpn import detect_openvpn_via_ssh
        from app.core.config import load_config
        from app.services.peer_instance.peer_ssh_connect import effective_ssh_private_key_for_peer

        row = self.get(peer_id)
        if not row:
            raise ValueError(f"对端实例不存在: {peer_id}")
        host = str(row.get("ssh_host") or "").strip()
        port = int(row.get("ssh_port") or 22)
        if not host:
            raise ValueError("请先在对端配置中填写 SSH 主机")
        user = str(ssh_username).strip() if ssh_username is not None else str(row.get("ssh_username") or "").strip()
        if not user:
            raise ValueError("请先在对端配置中填写 SSH 用户名或传入 ssh_username")
        pw = ssh_password if ssh_password is not None else str(row.get("ssh_password") or "")
        if ssh_private_key is not None:
            pem = str(ssh_private_key or "").strip()
            pp = str(ssh_private_key_passphrase or "").strip() or None
        else:
            pem, pp = effective_ssh_private_key_for_peer(row, load_config())
        if ssh_openvpn_binary is not None:
            ob = str(ssh_openvpn_binary).strip() or None
        else:
            ob = str(row.get("ssh_openvpn_binary") or "").strip() or None
        return detect_openvpn_via_ssh(
            host,
            port,
            user,
            password=pw or None,
            private_key_text=pem or None,
            private_key_passphrase=pp or None,
            key_filename=ssh_key_path,
            openvpn_binary=ob,
        )

    def deploy_peer_site_firewall_via_ssh(
        self,
        peer_id: str,
        *,
        masquerade_on_peer: bool | None = None,
        force: bool = False,
    ) -> dict:
        """经 SSH 在对端写入 design §3 宽松 FORWARD（global_subnet 为源）及可选 MASQUERADE。

        须已配置 ``global_subnet``、SSH 主机/用户/凭据；远端需 root 或 **无密码 sudo**（``sudo -n``）。

        Raises:
            ValueError: 对端不存在或配置不全
            RuntimeError: SSH / iptables 失败
        """
        from app.core.config import load_config
        from app.services.peer_instance.remote_peer_iptables import apply_peer_site_iptables_via_ssh

        row = self.get(peer_id)
        if not row:
            raise ValueError(f"对端实例不存在: {peer_id}")
        if masquerade_on_peer is not None:
            row = dict(row)
            row["masquerade_on_peer"] = bool(masquerade_on_peer)
        cfg = load_config()
        gs = str(cfg.get("global_subnet") or "").strip()
        return apply_peer_site_iptables_via_ssh(row, gs, force=force)

    def fetch_remote_peer_filter_chain_snapshot(self, peer_id: str) -> dict:
        """经 SSH 拉取对端本 peer 的 ``VPN_PEER_*`` 链与 FORWARD 引用行（只读）。"""
        from app.services.peer_instance.remote_peer_iptables import fetch_peer_filter_chain_snapshot_via_ssh

        row = self.get(peer_id)
        if not row:
            raise ValueError(f"对端实例不存在: {peer_id}")
        return fetch_peer_filter_chain_snapshot_via_ssh(row)

    def apply_remote_peer_filter_chain_rests(self, peer_id: str, rests: list[str]) -> None:
        """经 SSH 按序回写对端本 peer 的 filter 用户链（仅该链，空列表则清链）。"""
        from app.services.peer_instance.remote_peer_iptables import replace_peer_filter_chain_rests_via_ssh

        row = self.get(peer_id)
        if not row:
            raise ValueError(f"对端实例不存在: {peer_id}")
        replace_peer_filter_chain_rests_via_ssh(row, rests)

    def deploy_peer_ovpn_via_ssh(self, peer_id: str, *, remote_path: str | None = None) -> dict:
        """经 SSH 将本地 ``OVPN_PROFILES_DIR/<绑定用户>.ovpn`` 上传到对端（须无密码 sudo 完成 install）。

        完整「自动部署」（安装 openvpn 包、systemd 等）仍未实现，见 ``tasks.md``。

        Raises:
            ValueError: 对端不存在或本地无 .ovpn
            RuntimeError: SSH/SFTP/sudo 失败
        """
        from app.services.peer_instance.remote_peer_ovpn import upload_bound_user_ovpn_via_ssh

        row = self.get(peer_id)
        if not row:
            raise ValueError(f"对端实例不存在: {peer_id}")
        return upload_bound_user_ovpn_via_ssh(row, remote_path=remote_path)

    def deploy_peer_ovpn_from_local_path_via_ssh(
        self, peer_id: str, local_path: str, *, remote_path: str | None = None
    ) -> dict:
        """从本机任意路径经 SSH 上传 .ovpn（非中心 OVPN_PROFILES_DIR 成品时使用）。"""
        from app.services.peer_instance.remote_peer_ovpn import upload_custom_ovpn_file_via_ssh

        row = self.get(peer_id)
        if not row:
            raise ValueError(f"对端实例不存在: {peer_id}")
        return upload_custom_ovpn_file_via_ssh(row, local_path, remote_path=remote_path)

    def install_openvpn_on_peer_via_ssh(self, peer_id: str) -> dict:
        """经 SSH 在对端安装 OpenVPN：Debian 系同初始化向导官方源；RHEL 系 dnf/yum 安装包。

        完整行为见 ``remote_peer_install.install_openvpn_on_peer_via_ssh``。

        Raises:
            ValueError: 对端不存在
            RuntimeError: SSH 或安装失败
        """
        from app.services.peer_instance.remote_peer_install import install_openvpn_on_peer_via_ssh

        row = self.get(peer_id)
        if not row:
            raise ValueError(f"对端实例不存在: {peer_id}")
        return install_openvpn_on_peer_via_ssh(row)

    def fetch_peer_client_service_status_via_ssh(self, peer_id: str) -> dict:
        """经 SSH 查询对端 ``openvpn-client`` systemd 服务状态。"""
        from app.services.peer_instance.remote_peer_ovpn import fetch_openvpn_client_service_status_via_ssh

        row = self.get(peer_id)
        if not row:
            raise ValueError(f"对端实例不存在: {peer_id}")
        return fetch_openvpn_client_service_status_via_ssh(row)

    def control_peer_client_service_via_ssh(self, peer_id: str, action: str) -> dict:
        """经 SSH 控制对端 ``openvpn-client`` systemd 服务。"""
        from app.services.peer_instance.remote_peer_ovpn import control_openvpn_client_service_via_ssh

        row = self.get(peer_id)
        if not row:
            raise ValueError(f"对端实例不存在: {peer_id}")
        return control_openvpn_client_service_via_ssh(row, action)

    def fetch_peer_client_service_logs_via_ssh(self, peer_id: str, *, lines: int = 200) -> dict:
        """经 SSH 拉取对端 ``openvpn-client`` 文件日志。"""
        from app.services.peer_instance.remote_peer_ovpn import fetch_openvpn_client_service_logs_via_ssh

        row = self.get(peer_id)
        if not row:
            raise ValueError(f"对端实例不存在: {peer_id}")
        return fetch_openvpn_client_service_logs_via_ssh(row, lines=lines)

    def deploy_peer_client_systemd_via_ssh(self, peer_id: str, *, config_path: str | None = None) -> dict:
        """经 SSH 启用对端 ``openvpn-client`` systemd 服务。"""
        from app.services.peer_instance.remote_peer_ovpn import deploy_openvpn_client_systemd_via_ssh

        row = self.get(peer_id)
        if not row:
            raise ValueError(f"对端实例不存在: {peer_id}")
        return deploy_openvpn_client_systemd_via_ssh(row, config_path=config_path)

    def ensure_openvpn_on_peer_via_ssh(self, peer_id: str) -> dict:
        """先经 SSH 探测 OpenVPN；已达标则直接返回，未达标才安装并做首次配置。

        配置覆盖由“配置推送”入口负责；安装入口不覆盖已安装对端的配置。

        Raises:
            ValueError: 对端不存在或 SSH 配置不全（与 ``probe_openvpn_via_ssh`` 一致）
            RuntimeError: SSH 连接/认证失败或安装失败
        """
        from app.services.peer_instance.remote_peer_install import install_openvpn_on_peer_via_ssh

        row = self.get(peer_id)
        if not row:
            raise ValueError(f"对端实例不存在: {peer_id}")
        from app.services.peer_instance.remote_peer_ovpn import (
            deploy_openvpn_client_systemd_via_ssh,
            upload_bound_user_ovpn_via_ssh,
        )

        probe = self.probe_openvpn_via_ssh(peer_id)
        if not probe.get("connected"):
            err = str(probe.get("ssh_error") or "SSH 连接失败")
            peer_remote_log.error("对端 OpenVPN 探测失败 peer_id=%s: %s", peer_id, err)
            raise RuntimeError(err)
        skipped_install = bool(probe.get("installed")) and bool(probe.get("meets_requirement"))
        install_result = None
        if skipped_install:
            peer_remote_log.info(
                "对端 OpenVPN 已满足要求，跳过安装 peer_id=%s path=%s version=%s",
                peer_id,
                probe.get("path"),
                probe.get("version"),
            )
            return {
                "ok": True,
                "skipped_install": True,
                "probe": probe,
                "install": None,
                "ovpn_push": None,
                "systemd_client": None,
                "peer_firewall": None,
            }
        peer_remote_log.info(
            "对端将执行 OpenVPN 安装 peer_id=%s installed=%s meets_requirement=%s",
            peer_id,
            probe.get("installed"),
            probe.get("meets_requirement"),
        )
        install_result = install_openvpn_on_peer_via_ssh(row)
        after_probe = self.probe_openvpn_via_ssh(peer_id)
        if not (after_probe.get("installed") and after_probe.get("meets_requirement")):
            version = after_probe.get("version") or "unknown"
            path = after_probe.get("path") or "unknown"
            raise RuntimeError(
                f"对端 OpenVPN 安装后版本仍不满足要求: version={version} path={path}，"
                "当前客户端配置使用 tls-crypt-v2，请安装支持该能力的 OpenVPN 版本。"
            )
        probe = after_probe
        ovpn_push: dict | None = None
        systemd_client: dict | None = None
        uname = str(row.get("bound_username") or "").strip()
        if uname:
            local_ovpn = OVPN_PROFILES_DIR / f"{uname}.ovpn"
            if local_ovpn.is_file():
                try:
                    ovpn_push = upload_bound_user_ovpn_via_ssh(row, remote_path=None)
                    peer_remote_log.info(
                        "安装后已从中心推送 .ovpn peer_id=%s remote=%s",
                        peer_id,
                        ovpn_push.get("remote_path"),
                    )
                except (ValueError, RuntimeError) as exc:
                    peer_remote_log.warning(
                        "安装后自动推送 .ovpn 失败 peer_id=%s: %s",
                        peer_id,
                        exc,
                    )
                    ovpn_push = {"ok": False, "error": str(exc)}
            else:
                peer_remote_log.info(
                    "安装完成但中心无该用户 .ovpn，跳过自动推送 peer_id=%s path=%s",
                    peer_id,
                    local_ovpn,
                )
        if isinstance(ovpn_push, dict) and ovpn_push.get("ok"):
            try:
                systemd_client = deploy_openvpn_client_systemd_via_ssh(row, config_path=None)
            except Exception as exc:
                peer_remote_log.warning(
                    "安装后启用 OpenVPN client systemd 服务失败 peer_id=%s: %s",
                    peer_id,
                    exc,
                )
                systemd_client = {"ok": False, "error": str(exc)}

        peer_firewall: dict | None = None
        try:
            peer_firewall = self.deploy_peer_site_firewall_via_ssh(peer_id)
            peer_remote_log.info(
                "安装后已自动下发对端 iptables（global_subnet 入站放行）peer_id=%s",
                peer_id,
            )
        except Exception as exc:
            peer_remote_log.warning("安装后自动下发对端 iptables 失败 peer_id=%s: %s", peer_id, exc)
            peer_firewall = {"ok": False, "error": str(exc)}

        return {
            "ok": True,
            "skipped_install": skipped_install,
            "probe": probe,
            "install": install_result,
            "ovpn_push": ovpn_push,
            "systemd_client": systemd_client,
            "peer_firewall": peer_firewall,
        }

    def _path(self, peer_id: str) -> Path:
        return PEERS_DIR / f"{peer_id}.json"

    def list_all(self) -> list[dict]:
        rows: list[dict] = []
        for p in sorted(PEERS_DIR.glob("*.json")):
            data = read_json(p)
            if data:
                rows.append(data)
        return rows

    def get(self, peer_id: str) -> dict | None:
        return read_json(self._path(peer_id))

    def list_bound_usernames(self, *, exclude_peer_id: str | None = None) -> set[str]:
        """列出已被某一「对端实例」占用的绑定用户名。

        Args:
            exclude_peer_id: 若指定，则在统计时跳过该实例（用于编辑对端时将当前绑定用户在 UI 内仍可选）。

        Returns:
            已从 ``bound_username`` 归一化（strip）的用户名集合。
        """
        ex = str(exclude_peer_id or "").strip()
        out: set[str] = set()
        for p in self.list_all():
            pid = str(p.get("id") or "").strip()
            if ex and pid == ex:
                continue
            u = str(p.get("bound_username") or "").strip()
            if u:
                out.add(u)
        return out

    def _ensure_bound_username_unique(self, username: str, *, exclude_peer_id: str | None) -> None:
        """同一 VPN 用户名（CN/CCD）仅允许被一个对端实例绑定；冲突时抛出 ``ValueError``。"""
        u = str(username or "").strip()
        if not u:
            return
        ex = str(exclude_peer_id or "").strip()
        for p in self.list_all():
            pid = str(p.get("id") or "").strip()
            if ex and pid == ex:
                continue
            if str(p.get("bound_username") or "").strip() == u:
                logger.info(
                    "拒绝绑定：用户已被其它对端占用 user=%s other_peer=%s",
                    u,
                    pid[:8] if pid else "?",
                )
                raise ValueError(f"用户「{u}」已被其它对端实例绑定，一个用户只能绑定一个对端实例")

    def create(self, data: dict) -> dict:
        if "id" not in data or not str(data.get("id", "")).strip():
            data["id"] = str(uuid.uuid4())
        peer = PeerInstance(**data)
        self._ensure_bound_username_unique(peer.bound_username, exclude_peer_id=None)
        write_json_atomic(self._path(peer.id), peer.model_dump())
        self._apply_ccd_and_vpn_forward(peer)
        self.sync_all_mesh_push_routes_in_ccd()
        logger.info("已创建对端实例 %s 绑定用户 %s", peer.id, peer.bound_username)
        return peer.model_dump()

    def patch_center_forward_priority(self, peer_id: str, priority: int) -> None:
        """仅更新 ``center_forward_priority``，不触发 CCD/SSH；由防火墙全局排序批量调用。"""
        cur = self.get(peer_id)
        if not cur:
            raise ValueError(f"对端实例不存在: {peer_id}")
        merged = {
            **cur,
            "center_forward_priority": int(priority),
            "updated_at": datetime.now().isoformat(),
        }
        peer = PeerInstance(**merged)
        write_json_atomic(self._path(peer.id), peer.model_dump())

    def patch_center_forward_enabled(self, peer_id: str, enabled: bool) -> None:
        """仅更新 ``center_forward_enabled`` 并刷新中心 ``VPN_FORWARD``；不改 CCD/对端 SSH。"""
        cur = self.get(peer_id)
        if not cur:
            raise ValueError(f"对端实例不存在: {peer_id}")
        merged = {
            **cur,
            "center_forward_enabled": bool(enabled),
            "updated_at": datetime.now().isoformat(),
        }
        peer = PeerInstance(**merged)
        write_json_atomic(self._path(peer.id), peer.model_dump())
        if os.name != "nt":
            from app.services.firewall.rule_service import FirewallRuleService

            FirewallRuleService().refresh_vpn_forward_only()

    def update(self, peer_id: str, data: dict, *, refresh_center_iptables: bool = True) -> dict:
        cur = self.get(peer_id)
        if not cur:
            raise ValueError(f"对端实例不存在: {peer_id}")
        merged = {**cur, **data, "id": peer_id, "updated_at": datetime.now().isoformat()}
        peer = PeerInstance(**merged)
        self._ensure_bound_username_unique(peer.bound_username, exclude_peer_id=peer_id)
        dumped = peer.model_dump()
        need_fw = _vpn_forward_wire_snapshot(cur) != _vpn_forward_wire_snapshot(dumped)
        need_mesh = _mesh_push_wire_snapshot(cur) != _mesh_push_wire_snapshot(dumped)
        need_ccd = _ccd_iroute_wire_snapshot(cur) != _ccd_iroute_wire_snapshot(dumped)

        write_json_atomic(self._path(peer.id), dumped)

        if os.name != "nt":
            from app.services.firewall.rule_service import FirewallRuleService

            if refresh_center_iptables:
                if need_fw:
                    FirewallRuleService().refresh_vpn_forward_only()
                else:
                    logger.info("对端保存无 VPN_FORWARD（中心对端策略）字段变更，跳过 iptables peer=%s", peer.id[:8])
            else:
                logger.info("已跳过中心 VPN_FORWARD 刷新，调用方将统一刷新 peer=%s", peer.id[:8])
        else:
            logger.info("Windows 下跳过本机 iptables 重建，对端已写盘 peer=%s", peer.id[:8])

        if need_ccd:
            self._merge_ccd(peer.bound_username, peer.id, peer.lan_cidrs)
        else:
            logger.info("对端保存无绑定用户/CIDR(iroute)变更，跳过 CCD peer 块 peer=%s", peer.id[:8])

        if need_mesh:
            self.sync_all_mesh_push_routes_in_ccd()
        else:
            logger.info(
                "对端保存无 LAN / mesh 可见组 变更，跳过全量 mesh CCD peer=%s",
                peer.id[:8],
            )
        logger.debug("已保存对端实例元数据 peer=%s", peer.id)
        return dumped


    def delete(self, peer_id: str) -> None:
        cur = self.get(peer_id)
        if not cur:
            raise ValueError(f"对端实例不存在: {peer_id}")
        username = str(cur.get("bound_username") or "")
        self._strip_ccd(username, peer_id)
        if (
            str(cur.get("ssh_host") or "").strip()
            and str(cur.get("ssh_username") or "").strip()
            and (
                str(cur.get("ssh_password") or "").strip()
                or str(cur.get("ssh_private_key") or "").strip()
            )
        ):
            try:
                from app.services.peer_instance.remote_peer_iptables import (
                    remove_peer_site_iptables_via_ssh,
                )

                remove_peer_site_iptables_via_ssh(cur)
            except Exception as exc:
                logger.warning(
                    "删除对端时远端 iptables 清理失败（可 SSH 手工按注释 peer=%s 清理）: %s",
                    peer_id,
                    exc,
                )
        self._path(peer_id).unlink(missing_ok=True)
        if os.name != "nt":
            from app.services.firewall.rule_service import FirewallRuleService

            FirewallRuleService().refresh_vpn_forward_only()
        self.sync_all_mesh_push_routes_in_ccd()
        logger.info("已删除对端实例 %s", peer_id)

    def sync_all_center_iptables(self) -> None:
        """项目启动/恢复专用：确保中心侧 FORWARD 固定钩子存在（幂等）。"""
        if os.name == "nt":
            return
        mgr = IptablesManager()
        if not mgr.ensure_forward_hooks_with_peer():
            msg = "FORWARD 钩子写入失败"
            logger.error(msg)
            raise RuntimeError(msg)

    def sync_all_mesh_push_routes_in_ccd(self) -> None:
        """按当前磁盘上全部对端实例，向各活跃用户 CCD 重写 mesh push route 块。

        每个用户收到的 CIDR 并集由 ``mesh_route_visible_group_ids`` 决定：某对端列表为空则
        该对端 LAN 对所有组用户下发；非空则仅对 ``group_id`` 命中的用户下发。

        无匹配 CIDR 时移除该块。任一 CCD 写入失败则抛出 RuntimeError。

        Raises:
            RuntimeError: 至少一个用户的 CCD 更新失败
        """
        from app.services.group.crud import GroupService
        from app.services.user.crud import UserService

        peers_snapshot = self.list_all()
        failed: list[str] = []
        group_names = {
            str(g.get("id") or "").strip(): str(g.get("name") or g.get("id") or "未命名组")
            for g in GroupService().list_all()
            if str(g.get("id") or "").strip()
        }
        stats: dict[str, dict[str, int]] = {}

        def group_stat(group_id: str) -> dict[str, int]:
            key = group_id or "未分组"
            if key not in stats:
                stats[key] = {"updated": 0, "unchanged": 0, "no_ccd": 0, "failed": 0}
            return stats[key]

        for u in UserService().list_all():
            if u.get("status") != "active":
                continue
            uname = str(u.get("username") or "").strip()
            if not uname:
                continue
            gid = str(u.get("group_id") or "").strip()
            st = group_stat(gid)
            path = CCD_DIR / uname
            if not path.is_file():
                st["no_ccd"] += 1
                continue
            cidrs = mesh_lan_cidrs_for_user_group(peers_snapshot, gid)
            try:
                text = path.read_text(encoding="utf-8")
                new_text = merge_mesh_peer_push_routes_into_ccd(text, cidrs)
                if new_text == text:
                    st["unchanged"] += 1
                    continue
                path.write_text(new_text, encoding="utf-8")
                fix_path_for_openvpn_shared_data(path)
                st["updated"] += 1
            except OSError as exc:
                logger.error("mesh push 读写 CCD 失败 user=%s: %s", uname, exc)
                st["failed"] += 1
                failed.append(uname)
        for gid, st in sorted(stats.items(), key=lambda item: group_names.get(item[0], item[0])):
            group_label = group_names.get(gid, gid)
            logger.info(
                "已同步 %s，已同步用户: %s，未变化: %s，无 CCD: %s，失败: %s",
                group_label,
                st["updated"],
                st["unchanged"],
                st["no_ccd"],
                st["failed"],
            )
        if failed:
            raise RuntimeError("mesh push 路由写入 CCD 失败: " + ", ".join(failed))

    def _apply_ccd_and_vpn_forward(
        self, peer: PeerInstance, *, refresh_center_iptables: bool = True
    ) -> None:
        # 对端「中心侧放行」的 VPN_FORWARD 来自对端 JSON（lan_cidrs 等），与 CCD 无依赖。
        # 须先于 _merge_ccd 重建，否则 CCD 异常会导致本机 iptables 未更新、与已保存的 JSON 不一致。
        if os.name != "nt" and refresh_center_iptables:
            from app.services.firewall.rule_service import FirewallRuleService

            FirewallRuleService().refresh_vpn_forward_only()
        elif os.name != "nt":
            logger.info("已跳过中心 VPN_FORWARD 刷新，调用方将统一刷新 peer=%s", peer.id[:8])
        else:
            logger.info("Windows 下跳过本机 iptables 重建，对端已写盘 peer=%s", peer.id[:8])
        self._merge_ccd(peer.bound_username, peer.id, peer.lan_cidrs)

    def _merge_ccd(self, username: str, peer_id: str, lan_cidrs: list[str]) -> None:
        ccd_file = CCD_DIR / username
        if not ccd_file.is_file():
            raise ValueError(f"用户 {username} 的 CCD 不存在，请先创建用户并分配固定 IP")
        text = ccd_file.read_text(encoding="utf-8")
        new_text = merge_peer_block_into_ccd(text, peer_id, lan_cidrs)
        ccd_file.write_text(new_text, encoding="utf-8")
        fix_path_for_openvpn_shared_data(ccd_file)

    def _strip_ccd(self, username: str, peer_id: str) -> None:
        ccd_file = CCD_DIR / username
        if not ccd_file.is_file():
            return
        text = ccd_file.read_text(encoding="utf-8")
        ccd_file.write_text(strip_peer_block_from_ccd(text, peer_id), encoding="utf-8")
        fix_path_for_openvpn_shared_data(ccd_file)
