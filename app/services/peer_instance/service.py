# -*- coding: utf-8 -*-
"""对端站点实例：持久化、CCD iroute、中心侧 VPN_FORWARD / SSH 对端 VPN_PEER_* iptables 同步"""
from __future__ import annotations

from contextlib import contextmanager
import functools
import logging
import os
import tempfile
import threading
import uuid
from datetime import datetime
from pathlib import Path

from app.core.constants import CCD_DIR, OVPN_PROFILES_DIR
from app.models.peer_instance import PeerInstance
from app.services.firewall.iptables_mgr import IptablesManager
from app.services.peer_instance.ccd_merge import (
    merge_mesh_peer_push_routes_into_ccd,
    merge_peer_block_into_ccd,
    sorted_unique_ipv4_cidrs,
    strip_peer_block_from_ccd,
)
from app.storage.sqlite_store import SqliteStore, get_default_store
from app.utils.posix_data_perms import fix_path_for_openvpn_shared_data

logger = logging.getLogger(__name__)
peer_remote_log = logging.getLogger("peer.remote")

_PEER_LIFECYCLE_LOCK = threading.RLock()


@contextmanager
def peer_lifecycle_lock():
    """跨 Peer 与中心防火墙写操作共享的进程内串行锁。"""
    with _PEER_LIFECYCLE_LOCK:
        yield


def _serialize_peer_lifecycle(method):
    """串行化同进程 Peer CRUD，避免并发 CCD 快照互相覆盖。"""
    @functools.wraps(method)
    def wrapped(self, *args, **kwargs):
        with peer_lifecycle_lock():
            return method(self, *args, **kwargs)
    return wrapped


def _write_ccd_bytes_atomic(path: Path, content: bytes, mode: int) -> None:
    """同目录临时文件 + replace，避免 OpenVPN 读到半写入的 CCD。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(
        prefix=f".{path.name}.nicevpn-",
        dir=str(path.parent),
    )
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temp_path, mode)
        fix_path_for_openvpn_shared_data(temp_path)
        os.replace(temp_path, path)
        try:
            dir_fd = os.open(str(path.parent), getattr(os, "O_DIRECTORY", 0) | os.O_RDONLY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        except OSError:
            pass
    finally:
        try:
            temp_path.unlink(missing_ok=True)
        except OSError:
            logger.warning("清理 CCD 临时文件失败: %s", temp_path, exc_info=True)


def _write_ccd_text_atomic(path: Path, content: str) -> None:
    if path.is_symlink():
        raise RuntimeError(f"CCD 文件为符号链接，拒绝原子替换: {path}")
    mode = path.stat().st_mode & 0o7777
    _write_ccd_bytes_atomic(path, content.encode("utf-8"), mode)


def _snapshot_ccd_files() -> dict[Path, tuple[bytes, int]]:
    """快照全部用户 CCD；Peer mesh 同步可能更新多个用户文件。"""
    if not CCD_DIR.is_dir():
        return {}
    snapshot: dict[Path, tuple[bytes, int]] = {}
    for path in sorted(CCD_DIR.iterdir()):
        if path.is_symlink():
            raise RuntimeError(f"CCD 目录包含符号链接，拒绝执行可回滚操作: {path}")
        if not path.is_file():
            continue
        info = path.stat()
        snapshot[path] = (path.read_bytes(), info.st_mode & 0o7777)
    return snapshot


def _restore_ccd_files(snapshot: dict[Path, tuple[bytes, int]]) -> None:
    """尽力恢复所有 CCD 快照；全部尝试后汇总失败，供上层记录严重错误。"""
    failures: list[tuple[Path, Exception]] = []
    for path, (content, mode) in snapshot.items():
        try:
            if path.is_file() and path.read_bytes() == content:
                continue
            _write_ccd_bytes_atomic(path, content, mode)
        except Exception as exc:
            failures.append((path, exc))
            logger.critical("恢复 CCD 快照失败 path=%s: %s", path, exc, exc_info=True)
    if failures:
        path, error = failures[0]
        raise RuntimeError(
            f"CCD 快照恢复不完整，失败文件数={len(failures)}，首个={path}"
        ) from error


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
    """mesh 下发语义指纹：后方 LAN、推送总开关及组/标签可见范围。"""
    raw_lan = [str(x).strip() for x in (row.get("lan_cidrs") or []) if str(x).strip()]
    lan_t = tuple(sorted_unique_ipv4_cidrs(raw_lan))
    mugs = tuple(sorted(str(x).strip() for x in (row.get("mesh_route_visible_group_ids") or []) if str(x).strip()))
    tags = tuple(sorted(str(x).strip() for x in (row.get("mesh_route_visible_tag_ids") or []) if str(x).strip()))
    return (lan_t, mugs, tags, bool(row.get("mesh_route_push_disabled", False)))


def _ccd_iroute_wire_snapshot(row: dict) -> tuple:
    """绑定用户 CCD 中对端 iroute 块内容相关指纹。"""
    raw_lan = [str(x).strip() for x in (row.get("lan_cidrs") or []) if str(x).strip()]
    lan_t = tuple(sorted_unique_ipv4_cidrs(raw_lan))
    u = str(row.get("bound_username") or "").strip()
    return (u, lan_t)


def mesh_lan_cidrs_for_user_group(
    peers_rows: list[dict],
    user_group_id: str,
    user_tag_ids: list[str] | set[str] | tuple[str, ...] = (),
) -> list[str]:
    """计算用户应收到的 Mesh LAN 路由。

    组与标签采用 OR 关系：命中任一已选组，或用户/所属组拥有任一已选标签即可。
    两类限制都为空时对所有活跃用户下发；标签同时包含直接用户标签和组继承标签。
    单个对端设置 mesh_route_push_disabled 时完全跳过该对端的 Mesh push，
    不影响其绑定用户 CCD iroute 与中心侧路由/防火墙。
    """
    group_id = str(user_group_id or "").strip()
    user_tags = {str(value).strip() for value in (user_tag_ids or ()) if str(value).strip()}
    acc: list[str] = []
    for row in peers_rows:
        if row.get("mesh_route_push_disabled", False):
            continue
        lan = list(row.get("lan_cidrs") or [])
        if not lan:
            continue
        visible_groups = {
            str(value).strip()
            for value in (row.get("mesh_route_visible_group_ids") or [])
            if str(value).strip()
        }
        visible_tags = {
            str(value).strip()
            for value in (row.get("mesh_route_visible_tag_ids") or [])
            if str(value).strip()
        }
        if not visible_groups and not visible_tags:
            acc.extend(lan)
            continue
        if (group_id and group_id in visible_groups) or (user_tags & visible_tags):
            acc.extend(lan)
    return sorted_unique_ipv4_cidrs(acc)



def _track_peer_remote_operation(operation: str):
    """记录单个 Peer 的远端操作结果，不将其与 SSH 连通性混为一谈。"""
    def decorate(func):
        @functools.wraps(func)
        def wrapped(self, *args, **kwargs):
            peer_id = args[0] if args else kwargs.get("peer_id", "")
            try:
                result = func(self, *args, **kwargs)
            except Exception as exc:
                self._record_peer_remote_operation(peer_id, operation, "failed", str(exc))
                raise
            if (
                operation == "OpenVPN 探测"
                and isinstance(result, dict)
                and "connected" in result
            ):
                self._record_peer_ssh_probe_result(
                    peer_id,
                    connected=bool(result.get("connected")),
                    error=str(result.get("ssh_error") or ""),
                )
            failed = (
                isinstance(result, dict)
                and (
                    result.get("ok") is False
                    or result.get("connected") is False
                )
            )
            error = ""
            if failed:
                error = str(
                    result.get("error")
                    or result.get("ssh_error")
                    or result.get("message")
                    or "远端返回失败状态"
                )
            self._record_peer_remote_operation(
                peer_id,
                operation,
                "failed" if failed else "success",
                error,
            )
            return result
        return wrapped
    return decorate


class PeerService:
    """对端实例 CRUD；绑定用户须已存在且已有 CCD。"""

    def __init__(self, store: SqliteStore | None = None) -> None:
        self.store = store or get_default_store()

    def _record_peer_ssh_probe_result(
        self, peer_id: str, *, connected: bool, error: str = "",
    ) -> None:
        """记录 SSH 探测的连通性，使用 PeerService 当前 store（支持测试/多实例注入）。"""
        updater = getattr(self.store, "update_peer_remote_status", None)
        if not callable(updater):
            return
        try:
            updater(
                str(peer_id or ""),
                ssh_status="connected" if connected else "failed",
                ssh_checked_at=datetime.now().astimezone().isoformat(timespec="seconds"),
                ssh_error=str(error or "").strip()[:500],
            )
        except Exception:
            peer_remote_log.warning(
                "记录对端 SSH 探测状态失败 peer_id=%s", peer_id, exc_info=True,
            )

    def _record_peer_remote_operation(
        self, peer_id: str, operation: str, status: str, error: str = "",
    ) -> None:
        """持久化最近一次远端操作结果；状态写入异常不改变原操作结果。"""
        updater = getattr(self.store, "update_peer_remote_status", None)
        if not callable(updater):
            return
        try:
            updater(
                str(peer_id or ""),
                last_operation=operation,
                last_operation_status=status,
                last_operation_at=datetime.now().astimezone().isoformat(timespec="seconds"),
                last_operation_error=str(error or "").strip()[:500],
            )
        except Exception:
            peer_remote_log.warning(
                "记录对端远程操作状态失败 peer_id=%s operation=%s",
                peer_id,
                operation,
                exc_info=True,
            )

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

    @_track_peer_remote_operation("OpenVPN 探测")
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

    @_track_peer_remote_operation("对端防火墙下发")
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

    @_track_peer_remote_operation("远端防火墙读取")
    def fetch_remote_peer_filter_chain_snapshot(self, peer_id: str) -> dict:
        """经 SSH 拉取对端本 peer 的 ``VPN_PEER_*`` 链与 FORWARD 引用行（只读）。"""
        from app.services.peer_instance.remote_peer_iptables import fetch_peer_filter_chain_snapshot_via_ssh

        row = self.get(peer_id)
        if not row:
            raise ValueError(f"对端实例不存在: {peer_id}")
        return fetch_peer_filter_chain_snapshot_via_ssh(row)

    @_track_peer_remote_operation("远端防火墙更新")
    def apply_remote_peer_filter_chain_rests(self, peer_id: str, rests: list[str]) -> None:
        """兼容原始规则调用；通过原子 iptables-restore 回写对端本 peer 用户链。"""
        from app.services.peer_instance.remote_peer_iptables import replace_peer_filter_chain_rests_via_ssh

        row = self.get(peer_id)
        if not row:
            raise ValueError(f"对端实例不存在: {peer_id}")
        replace_peer_filter_chain_rests_via_ssh(row, rests)

    @_track_peer_remote_operation("远端防火墙更新")
    def apply_remote_peer_filter_chain_plan(
        self, peer_id: str, rests: list[str], ipsets: list | None = None,
    ) -> None:
        """经 SSH 原子应用对端防火墙规则与标签 ipset 完整计划。"""
        from app.services.peer_instance.remote_peer_iptables import (
            replace_peer_filter_chain_plan_via_ssh,
        )

        row = self.get(peer_id)
        if not row:
            raise ValueError(f"对端实例不存在: {peer_id}")
        replace_peer_filter_chain_plan_via_ssh(row, rests, list(ipsets or []))

    @_track_peer_remote_operation("OVPN 配置推送")
    def deploy_peer_ovpn_via_ssh(self, peer_id: str, *, remote_path: str | None = None) -> dict:
        """经 SSH 将本地 ``OVPN_PROFILES_DIR/<绑定用户>.ovpn`` 上传到对端。

        远端可直接使用 root，非 root 时须支持 ``sudo -n``。

        Raises:
            ValueError: 对端不存在或本地无 .ovpn
            RuntimeError: SSH/SFTP/sudo 失败
        """
        from app.services.peer_instance.remote_peer_ovpn import upload_bound_user_ovpn_via_ssh

        row = self.get(peer_id)
        if not row:
            raise ValueError(f"对端实例不存在: {peer_id}")
        return upload_bound_user_ovpn_via_ssh(row, remote_path=remote_path)

    @_track_peer_remote_operation("自定义 OVPN 推送")
    def deploy_peer_ovpn_from_local_path_via_ssh(
        self, peer_id: str, local_path: str, *, remote_path: str | None = None
    ) -> dict:
        """从本机任意路径经 SSH 上传 .ovpn（非中心 OVPN_PROFILES_DIR 成品时使用）。"""
        from app.services.peer_instance.remote_peer_ovpn import upload_custom_ovpn_file_via_ssh

        row = self.get(peer_id)
        if not row:
            raise ValueError(f"对端实例不存在: {peer_id}")
        return upload_custom_ovpn_file_via_ssh(row, local_path, remote_path=remote_path)

    @_track_peer_remote_operation("OpenVPN 安装")
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

    @_track_peer_remote_operation("OpenVPN 服务状态读取")
    def fetch_peer_client_service_status_via_ssh(self, peer_id: str) -> dict:
        """经 SSH 查询对端 ``openvpn-client`` systemd 服务状态。"""
        from app.services.peer_instance.remote_peer_ovpn import fetch_openvpn_client_service_status_via_ssh

        row = self.get(peer_id)
        if not row:
            raise ValueError(f"对端实例不存在: {peer_id}")
        return fetch_openvpn_client_service_status_via_ssh(row)

    @_track_peer_remote_operation("OpenVPN 服务控制")
    def control_peer_client_service_via_ssh(self, peer_id: str, action: str) -> dict:
        """经 SSH 控制对端 ``openvpn-client`` systemd 服务。"""
        from app.services.peer_instance.remote_peer_ovpn import control_openvpn_client_service_via_ssh

        row = self.get(peer_id)
        if not row:
            raise ValueError(f"对端实例不存在: {peer_id}")
        return control_openvpn_client_service_via_ssh(row, action)

    @_track_peer_remote_operation("OpenVPN 日志读取")
    def fetch_peer_client_service_logs_via_ssh(self, peer_id: str, *, lines: int = 200) -> dict:
        """经 SSH 拉取对端 ``openvpn-client`` 文件日志。"""
        from app.services.peer_instance.remote_peer_ovpn import fetch_openvpn_client_service_logs_via_ssh

        row = self.get(peer_id)
        if not row:
            raise ValueError(f"对端实例不存在: {peer_id}")
        return fetch_openvpn_client_service_logs_via_ssh(row, lines=lines)

    @_track_peer_remote_operation("OpenVPN systemd 下发")
    def deploy_peer_client_systemd_via_ssh(self, peer_id: str, *, config_path: str | None = None) -> dict:
        """经 SSH 启用对端 ``openvpn-client`` systemd 服务。"""
        from app.services.peer_instance.remote_peer_ovpn import deploy_openvpn_client_systemd_via_ssh

        row = self.get(peer_id)
        if not row:
            raise ValueError(f"对端实例不存在: {peer_id}")
        return deploy_openvpn_client_systemd_via_ssh(row, config_path=config_path)

    @_track_peer_remote_operation("OpenVPN 一键装配")
    def ensure_openvpn_on_peer_via_ssh(self, peer_id: str) -> dict:
        """幂等确保对端 OpenVPN 客户端完整装配并通过最终状态校验。

        流程：
        1. SSH 探测 OpenVPN，版本不满足时才安装；
        2. 无论是否安装过，都覆盖下发绑定用户的当前 OVPN；
        3. 确保 systemd client unit 已启用并重启；
        4. 下发对端站点 iptables；
        5. 再读取 systemd/config 状态，确认装配闭环。

        已安装 OpenVPN 只会跳过“软件安装”，不会再跳过配置装配。
        """

        from app.services.peer_instance.remote_peer_install import (
            install_openvpn_on_peer_via_ssh,
        )
        from app.services.peer_instance.remote_peer_ovpn import (
            deploy_openvpn_client_systemd_via_ssh,
            fetch_openvpn_client_service_status_via_ssh,
            upload_bound_user_ovpn_via_ssh,
        )

        row = self.get(peer_id)
        if not row:
            raise ValueError(f"对端实例不存在: {peer_id}")

        probe = self.probe_openvpn_via_ssh(peer_id)
        if not probe.get("connected"):
            err = str(probe.get("ssh_error") or "SSH 连接失败")
            peer_remote_log.error(
                "对端 OpenVPN 探测失败 peer_id=%s: %s",
                peer_id,
                err,
            )
            raise RuntimeError(err)

        skipped_install = bool(probe.get("installed")) and bool(
            probe.get("meets_requirement")
        )
        install_result: dict | None = None
        if skipped_install:
            peer_remote_log.info(
                "对端 OpenVPN 已满足要求，仅跳过软件安装 peer_id=%s path=%s version=%s",
                peer_id,
                probe.get("path"),
                probe.get("version"),
            )
        else:
            peer_remote_log.info(
                "对端将执行 OpenVPN 安装 peer_id=%s installed=%s meets_requirement=%s",
                peer_id,
                probe.get("installed"),
                probe.get("meets_requirement"),
            )
            install_result = install_openvpn_on_peer_via_ssh(row)
            probe = self.probe_openvpn_via_ssh(peer_id)
            if not (probe.get("installed") and probe.get("meets_requirement")):
                version = probe.get("version") or "unknown"
                path = probe.get("path") or "unknown"
                raise RuntimeError(
                    "对端 OpenVPN 安装后版本仍不满足要求: "
                    f"version={version} path={path}，"
                    "当前客户端配置使用 tls-crypt-v2，请安装支持该能力的 OpenVPN 版本。"
                )

        try:
            ovpn_push = upload_bound_user_ovpn_via_ssh(row, remote_path=None)
        except Exception as exc:
            peer_remote_log.exception(
                "对端 OVPN 自动下发失败 peer_id=%s",
                peer_id,
            )
            raise RuntimeError(f"OpenVPN 已就绪，但 OVPN 下发失败: {exc}") from exc

        try:
            systemd_client = deploy_openvpn_client_systemd_via_ssh(
                row,
                config_path=str(ovpn_push.get("remote_path") or "").strip() or None,
            )
        except Exception as exc:
            peer_remote_log.exception(
                "对端 OpenVPN client systemd 装配失败 peer_id=%s",
                peer_id,
            )
            raise RuntimeError(
                f"OVPN 已下发，但 OpenVPN client systemd 装配失败: {exc}"
            ) from exc

        try:
            peer_firewall = self.deploy_peer_site_firewall_via_ssh(peer_id)
        except Exception as exc:
            peer_remote_log.exception(
                "对端 iptables 自动下发失败 peer_id=%s",
                peer_id,
            )
            raise RuntimeError(
                f"OpenVPN client 已装配，但对端 iptables 下发失败: {exc}"
            ) from exc
        if not isinstance(peer_firewall, dict) or not peer_firewall.get("ok"):
            raise RuntimeError(
                "OpenVPN client 已装配，但对端 iptables 未返回成功状态"
            )

        try:
            verification = fetch_openvpn_client_service_status_via_ssh(row)
        except Exception as exc:
            peer_remote_log.exception(
                "对端 OpenVPN client 最终状态校验失败 peer_id=%s",
                peer_id,
            )
            raise RuntimeError(f"装配完成但最终状态校验失败: {exc}") from exc

        service_name = str(verification.get("service") or "openvpn-client")
        active = str(
            verification.get("is_active")
            or verification.get("active_state")
            or ""
        ).strip()
        config_exists = bool(verification.get("config_exists"))
        if not verification.get("exists"):
            raise RuntimeError(
                f"装配后未发现 OpenVPN client systemd service: {service_name}"
            )
        if not config_exists:
            raise RuntimeError(
                f"装配后 OpenVPN client 配置不存在: "
                f"{verification.get('config_path') or 'unknown'}"
            )
        if active != "active":
            raise RuntimeError(
                f"装配后 OpenVPN client 未处于 active: "
                f"service={service_name} state={active or 'unknown'}"
            )

        peer_remote_log.info(
            "对端一键装配完成 peer_id=%s install_skipped=%s service=%s active=%s",
            peer_id,
            skipped_install,
            service_name,
            active,
        )
        return {
            "ok": True,
            "skipped_install": skipped_install,
            "probe": probe,
            "install": install_result,
            "ovpn_push": ovpn_push,
            "systemd_client": systemd_client,
            "peer_firewall": peer_firewall,
            "verification": verification,
        }

    def list_all(self) -> list[dict]:
        return self.store.list_peers()

    def list_page(
        self,
        *,
        offset: int = 0,
        limit: int = 50,
        search: str = "",
        sort_by: str = "created_at",
        sort_desc: bool = False,
    ) -> tuple[list[dict], int]:
        """数据库级对端分页查询。"""
        return self.store.list_peers_page(
            offset=offset,
            limit=limit,
            search=search,
            sort_by=sort_by,
            sort_desc=sort_desc,
        )

    def get(self, peer_id: str) -> dict | None:
        return self.store.get_peer(peer_id)

    def list_bound_usernames(self, *, exclude_peer_id: str | None = None) -> set[str]:
        """列出已被某一「对端实例」占用的绑定用户名。

        Args:
            exclude_peer_id: 若指定，则在统计时跳过该实例（用于编辑对端时将当前绑定用户在 UI 内仍可选）。

        Returns:
            已从 ``bound_username`` 归一化（strip）的用户名集合。
        """
        return self.store.list_bound_usernames(
            exclude_peer_id=str(exclude_peer_id or "").strip() or None
        )

    def _ensure_bound_username_unique(self, username: str, *, exclude_peer_id: str | None) -> None:
        """同一 VPN 用户名（CN/CCD）仅允许被一个对端实例绑定；冲突时抛出 ``ValueError``。"""
        u = str(username or "").strip()
        if not u:
            return
        ex = str(exclude_peer_id or "").strip() or None
        if u in self.store.list_bound_usernames(exclude_peer_id=ex):
            logger.info("拒绝绑定：用户已被其它对端占用 user=%s", u)
            raise ValueError(
                f"用户「{u}」已被其它对端实例绑定，一个用户只能绑定一个对端实例"
            )

    @_serialize_peer_lifecycle
    def create(self, data: dict) -> dict:
        payload = dict(data or {})
        if "id" not in payload or not str(payload.get("id", "")).strip():
            payload["id"] = str(uuid.uuid4())
        peer = PeerInstance(**payload)
        if self.get(peer.id):
            raise ValueError(f"对端实例 ID 已存在: {peer.id}")
        self._ensure_bound_username_unique(peer.bound_username, exclude_peer_id=None)

        # mesh push 可能修改多个用户 CCD，必须在任何写入前保存完整快照。
        ccd_snapshot = _snapshot_ccd_files()
        peer_data = peer.model_dump()
        try:
            self.store.upsert_peer(peer_data)
            self._apply_ccd_and_vpn_forward(peer)
            self.sync_all_mesh_push_routes_in_ccd()
        except Exception as exc:
            rollback_errors = self._rollback_peer_lifecycle(
                peer.id,
                None,
                ccd_snapshot,
                reason="创建对端失败",
            )
            if rollback_errors:
                raise RuntimeError(
                    "创建对端失败，且补偿回滚不完整: " + "；".join(rollback_errors)
                ) from exc
            raise

        logger.info("已创建对端实例 %s 绑定用户 %s", peer.id, peer.bound_username)
        return peer_data

    def _rollback_peer_lifecycle(
        self,
        peer_id: str,
        peer_snapshot: dict | None,
        ccd_snapshot: dict[Path, tuple[bytes, int]],
        *,
        reason: str,
    ) -> list[str]:
        """恢复 Peer SQLite、CCD、中心 VPN_FORWARD 与 mesh 派生状态。"""
        failures: list[str] = []
        try:
            if peer_snapshot is None:
                self.store.delete_peer(peer_id)
            else:
                self.store.upsert_peer(peer_snapshot)
        except Exception as exc:
            failures.append(f"SQLite: {exc}")
            logger.critical("%s：恢复 Peer SQLite 失败 peer=%s", reason, peer_id, exc_info=True)

        try:
            _restore_ccd_files(ccd_snapshot)
        except Exception as exc:
            failures.append(f"CCD: {exc}")
            logger.critical("%s：恢复 CCD 快照失败 peer=%s", reason, peer_id, exc_info=True)

        if os.name != "nt":
            try:
                from app.services.firewall.rule_service import FirewallRuleService

                FirewallRuleService().refresh_vpn_forward_only()
            except Exception as exc:
                failures.append(f"中心 VPN_FORWARD: {exc}")
                logger.critical(
                    "%s：恢复中心 VPN_FORWARD 失败 peer=%s",
                    reason,
                    peer_id,
                    exc_info=True,
                )

        try:
            self.sync_all_mesh_push_routes_in_ccd()
        except Exception as exc:
            failures.append(f"mesh CCD: {exc}")
            logger.critical("%s：恢复 mesh CCD 失败 peer=%s", reason, peer_id, exc_info=True)
        return failures

    @_serialize_peer_lifecycle
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
        self.store.upsert_peer(peer.model_dump())

    @_serialize_peer_lifecycle
    def patch_center_forward_enabled(self, peer_id: str, enabled: bool) -> None:
        """仅更新 center_forward_enabled；内核刷新失败时恢复 Peer 原始快照。"""
        cur = self.get(peer_id)
        if not cur:
            raise ValueError(f"对端实例不存在: {peer_id}")
        merged = {
            **cur,
            "center_forward_enabled": bool(enabled),
            "updated_at": datetime.now().isoformat(),
        }
        peer = PeerInstance(**merged)
        self.store.upsert_peer(peer.model_dump())
        if os.name != "nt":
            from app.services.firewall.rule_service import FirewallRuleService

            try:
                FirewallRuleService().refresh_vpn_forward_only()
            except Exception:
                try:
                    self.store.upsert_peer(cur)
                except Exception as rollback_exc:
                    logger.critical(
                        "中心对端放行状态刷新失败，且 Peer SQLite 回滚失败 peer=%s: %s",
                        peer_id,
                        rollback_exc,
                        exc_info=True,
                    )
                raise

    @_serialize_peer_lifecycle
    def update(self, peer_id: str, data: dict, *, refresh_center_iptables: bool = True) -> dict:
        cur = self.get(peer_id)
        if not cur:
            raise ValueError(f"对端实例不存在: {peer_id}")
        merged = {**cur, **data, "id": peer_id, "updated_at": datetime.now().isoformat()}
        peer = PeerInstance(**merged)
        self._ensure_bound_username_unique(peer.bound_username, exclude_peer_id=peer_id)
        dumped = peer.model_dump()
        ssh_config_fields = (
            "ssh_host",
            "ssh_port",
            "ssh_username",
            "ssh_password",
            "ssh_private_key",
            "ssh_private_key_passphrase",
        )
        ssh_config_changed = any(
            str(cur.get(field) or "") != str(dumped.get(field) or "")
            for field in ssh_config_fields
        )
        need_fw = _vpn_forward_wire_snapshot(cur) != _vpn_forward_wire_snapshot(dumped)
        need_mesh = _mesh_push_wire_snapshot(cur) != _mesh_push_wire_snapshot(dumped)
        need_ccd = _ccd_iroute_wire_snapshot(cur) != _ccd_iroute_wire_snapshot(dumped)
        ccd_snapshot = _snapshot_ccd_files() if (need_ccd or need_mesh) else {}

        self.store.upsert_peer(dumped)
        if ssh_config_changed:
            updater = getattr(self.store, "update_peer_remote_status", None)
            if callable(updater):
                try:
                    updater(
                        peer_id,
                        ssh_status="unknown",
                        ssh_checked_at="",
                        ssh_error="",
                        last_operation="",
                        last_operation_status="",
                        last_operation_at="",
                        last_operation_error="",
                    )
                except Exception:
                    peer_remote_log.warning(
                        "重置对端 SSH 状态失败 peer_id=%s", peer_id, exc_info=True,
                    )
        try:
            if os.name != "nt":
                from app.services.firewall.rule_service import FirewallRuleService

                if refresh_center_iptables and need_fw:
                    FirewallRuleService().refresh_vpn_forward_only()
                elif not refresh_center_iptables:
                    logger.info(
                        "已跳过中心 VPN_FORWARD 刷新，调用方将统一刷新 peer=%s",
                        peer.id[:8],
                    )
                else:
                    logger.info(
                        "对端保存无 VPN_FORWARD 字段变更，跳过 iptables peer=%s",
                        peer.id[:8],
                    )
            else:
                logger.info("Windows 下跳过本机 iptables 重建，对端已写盘 peer=%s", peer.id[:8])

            if need_ccd:
                old_username = str(cur.get("bound_username") or "").strip()
                new_username = str(peer.bound_username or "").strip()
                if old_username and old_username != new_username:
                    self._strip_ccd(old_username, peer.id)
                self._merge_ccd(new_username, peer.id, peer.lan_cidrs)
            else:
                logger.info(
                    "对端保存无绑定用户/CIDR(iroute)变更，跳过 CCD peer 块 peer=%s",
                    peer.id[:8],
                )

            if need_mesh:
                self.sync_all_mesh_push_routes_in_ccd()
            else:
                logger.info(
                    "对端保存无 LAN / mesh 可见组或标签变更，跳过全量 mesh CCD peer=%s",
                    peer.id[:8],
                )
        except Exception as exc:
            logger.exception("对端实例更新失败，开始恢复 SQLite 与派生状态 peer=%s", peer.id)
            rollback_errors: list[str] = []
            try:
                self.store.upsert_peer(cur)
            except Exception as rollback_exc:
                rollback_errors.append(f"SQLite: {rollback_exc}")
                logger.critical(
                    "对端更新失败，恢复 Peer SQLite 快照失败 peer=%s: %s",
                    peer.id,
                    rollback_exc,
                    exc_info=True,
                )

            if ccd_snapshot:
                try:
                    _restore_ccd_files(ccd_snapshot)
                except Exception as rollback_exc:
                    rollback_errors.append(f"CCD: {rollback_exc}")
                    logger.critical(
                        "对端更新失败，恢复 CCD 快照失败 peer=%s",
                        peer.id,
                        exc_info=True,
                    )

            if os.name != "nt" and refresh_center_iptables and need_fw:
                try:
                    from app.services.firewall.rule_service import FirewallRuleService

                    FirewallRuleService().refresh_vpn_forward_only()
                except Exception as rollback_exc:
                    rollback_errors.append(f"中心 VPN_FORWARD: {rollback_exc}")
                    logger.critical(
                        "对端更新失败，恢复中心 VPN_FORWARD 失败 peer=%s",
                        peer.id,
                        exc_info=True,
                    )

            if rollback_errors:
                logger.critical(
                    "对端更新失败且补偿不完整 peer=%s errors=%s",
                    peer.id,
                    "；".join(rollback_errors),
                )
                raise RuntimeError(
                    "对端更新失败，且补偿回滚不完整: " + "；".join(rollback_errors)
                ) from exc
            raise

        logger.debug("已保存对端实例元数据 peer=%s", peer.id)
        return dumped

    @_serialize_peer_lifecycle
    def delete(self, peer_id: str) -> None:
        cur = self.get(peer_id)
        if not cur:
            raise ValueError(f"对端实例不存在: {peer_id}")

        from app.services.peer_instance import remote_chain_cache

        remote_cache = remote_chain_cache.read_remote_chain_cache(peer_id)
        ssh_host = str(cur.get("ssh_host") or "").strip()
        ssh_username = str(cur.get("ssh_username") or "").strip()
        has_remote_state = bool(ssh_host or remote_cache)
        has_remote_auth = False
        if ssh_host and ssh_username:
            try:
                from app.core.config import load_config
                from app.services.peer_instance.peer_ssh_connect import (
                    effective_ssh_private_key_for_peer,
                )

                private_key, _ = effective_ssh_private_key_for_peer(cur, load_config())
                has_remote_auth = bool(
                    str(cur.get("ssh_password") or "").strip()
                    or str(private_key or "").strip()
                )
            except Exception:
                logger.exception("检查对端 SSH 凭据失败 peer=%s", peer_id)

        if has_remote_state and not (ssh_host and ssh_username and has_remote_auth):
            raise ValueError(
                "该对端配置过远端主机或存在远端防火墙缓存，但 SSH 主机/用户名/认证凭据不完整。"
                "为避免删除本地记录后遗留无法清理的远端规则，请先补齐 SSH 配置，或先手动清理远端规则。"
            )

        username = str(cur.get("bound_username") or "")
        ccd_snapshot = _snapshot_ccd_files()
        remote_cleanup_attempted = False
        try:
            # 本地所有可回滚步骤先完成；远端清理放在最后，避免远端已删除后本地回滚无法复原用户链。
            self._strip_ccd(username, peer_id)
            self.store.delete_peer(peer_id)

            if os.name != "nt":
                from app.services.firewall.rule_service import FirewallRuleService

                FirewallRuleService().refresh_vpn_forward_only()
            self.sync_all_mesh_push_routes_in_ccd()

            if has_remote_state:
                from app.services.peer_instance.remote_peer_iptables import (
                    remove_peer_site_iptables_via_ssh,
                )

                remote_cleanup_attempted = True
                result = remove_peer_site_iptables_via_ssh(cur)
                if not isinstance(result, dict) or not result.get("ok"):
                    raise RuntimeError("远端 iptables 清理未返回成功状态")

        except Exception as exc:
            rollback_errors = self._rollback_peer_lifecycle(
                peer_id,
                cur,
                ccd_snapshot,
                reason="删除对端失败",
            )
            detail = "本地 Peer、CCD 与中心规则已恢复"
            if remote_cleanup_attempted:
                detail += "；远端清理可能已部分执行，请检查远端防火墙后重试删除或重新下发规则"
            if rollback_errors:
                detail += "；本地补偿仍有失败: " + "；".join(rollback_errors)
                logger.critical("删除对端失败且补偿不完整 peer=%s", peer_id)
            raise RuntimeError(f"删除对端失败：{exc}。{detail}") from exc

        # 远端已确认清理成功后，才删除管理端工作副本；失败时保留缓存供人工恢复。
        remote_chain_cache.delete_remote_chain_cache(peer_id)
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

        每个用户收到的 CIDR 并集由对端的组/标签可见范围决定：组和标签采用 OR 关系，
        命中任一组或任一直接/组继承标签即可；两类限制均为空则对所有活跃用户下发。
        对端关闭 Mesh push 时，不论可见组/标签设置如何均不向其他用户下发其 LAN。
        停用用户会清除其 Mesh push 块；已删除用户不再有 CCD 文件。

        无匹配 CIDR 时移除该块。任一 CCD 写入失败则抛出 RuntimeError。

        Raises:
            RuntimeError: 至少一个用户的 CCD 更新失败
        """
        from app.services.group.crud import GroupService
        from app.services.user.crud import UserService

        peers_snapshot = self.list_all()
        failed: list[str] = []
        tag_memberships = self.store.list_tag_memberships()
        user_tag_map = tag_memberships.get("users") or {}
        group_tag_map = tag_memberships.get("groups") or {}
        group_rows = GroupService(store=self.store).list_all()
        group_names = {
            str(g.get("id") or "").strip(): str(g.get("name") or g.get("id") or "未命名组")
            for g in group_rows
            if str(g.get("id") or "").strip()
        }
        active_group_ids = {
            str(g.get("id") or "").strip()
            for g in group_rows
            if str(g.get("id") or "").strip()
            and str(g.get("status") or "active") == "active"
        }
        stats: dict[str, dict[str, int]] = {}

        def group_stat(group_id: str) -> dict[str, int]:
            key = group_id or "未分组"
            if key not in stats:
                stats[key] = {"updated": 0, "unchanged": 0, "no_ccd": 0, "failed": 0}
            return stats[key]

        for u in UserService(store=self.store).list_all():
            status = str(u.get("status") or "active")
            if status == "deleted":
                continue
            uname = str(u.get("username") or "").strip()
            if not uname:
                continue
            gid = str(u.get("group_id") or "").strip()
            st = group_stat(gid)
            effective_tags = set(user_tag_map.get(uname) or ())
            if gid in active_group_ids:
                effective_tags.update(group_tag_map.get(gid) or ())
            path = CCD_DIR / uname
            if not path.is_file():
                st["no_ccd"] += 1
                continue
            cidrs = (
                mesh_lan_cidrs_for_user_group(
                    peers_snapshot, gid, effective_tags,
                )
                if status == "active"
                else []
            )
            try:
                text = path.read_text(encoding="utf-8")
                new_text = merge_mesh_peer_push_routes_into_ccd(text, cidrs)
                if new_text == text:
                    st["unchanged"] += 1
                    continue
                _write_ccd_text_atomic(path, new_text)
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
        _write_ccd_text_atomic(ccd_file, new_text)

    def _strip_ccd(self, username: str, peer_id: str) -> None:
        ccd_file = CCD_DIR / username
        if not ccd_file.is_file():
            return
        text = ccd_file.read_text(encoding="utf-8")
        _write_ccd_text_atomic(
            ccd_file,
            strip_peer_block_from_ccd(text, peer_id),
        )
