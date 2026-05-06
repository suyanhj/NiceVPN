# -*- coding: utf-8 -*-
"""经 SSH 将客户端 .ovpn 上传到对端（中心成品路径或本机任意路径）。"""
from __future__ import annotations

import io
import logging
import shlex
import uuid
from pathlib import Path, PurePosixPath

from app.core.constants import LOG_RETENTION_DAYS, OVPN_PROFILES_DIR
from app.services.peer_instance.peer_ssh_connect import connect_peer_ssh_client_from_row

logger = logging.getLogger("peer.remote")


def _exec_ssh(client, command: str, *, timeout: int) -> tuple[str, str, int]:
    _stdin, stdout, stderr = client.exec_command(command, timeout=timeout)
    out_b = stdout.read()
    err_b = stderr.read()
    code = stdout.channel.recv_exit_status()
    return out_b.decode("utf-8", errors="replace"), err_b.decode("utf-8", errors="replace"), code


OFFICIAL_OPENVPN_CLIENT_TEMPLATE = "openvpn-client@.service"
OFFICIAL_OPENVPN_CLIENT_INSTANCE = "client"
OFFICIAL_OPENVPN_CLIENT_SERVICE = f"openvpn-client@{OFFICIAL_OPENVPN_CLIENT_INSTANCE}.service"
OFFICIAL_OPENVPN_CLIENT_CONFIG_PATH = f"/etc/openvpn/client/{OFFICIAL_OPENVPN_CLIENT_INSTANCE}.conf"
# 对端客户端统一落盘路径；与官方 openvpn-client@client.service 默认配置路径一致
REMOTE_CLIENT_OVPN_PATH = OFFICIAL_OPENVPN_CLIENT_CONFIG_PATH
FALLBACK_OPENVPN_CLIENT_SYSTEMD_UNIT = "openvpn-client.service"
_PEER_CLIENT_SERVICE_ACTIONS = {"start", "stop", "restart", "enable", "disable"}
PEER_REMOTE_CLIENT_LOG_DIR = "/etc/openvpn/log"
PEER_REMOTE_CLIENT_LOG_PATH = f"{PEER_REMOTE_CLIENT_LOG_DIR}/client.log"
PEER_REMOTE_CLIENT_STATUS_PATH = f"{PEER_REMOTE_CLIENT_LOG_DIR}/client-status.log"


def default_remote_ovpn_path(bound_username: str) -> str:
    """远端客户端配置默认路径（固定为 ``REMOTE_CLIENT_OVPN_PATH``）。

    ``bound_username`` 仍参与校验（须已绑定用户），与路径无关。
    """
    u = str(bound_username or "").strip()
    if not u:
        raise ValueError("bound_username 为空")
    return REMOTE_CLIENT_OVPN_PATH


def _detect_sudo_prefix_ovpn(client, *, timeout: int = 15) -> str:
    """返回 ``sudo -n `` 或 ``''``（已为 root）。"""
    _, _, c = _exec_ssh(client, "sudo -n true 2>/dev/null", timeout=timeout)
    if c == 0:
        return "sudo -n "
    out, _, c2 = _exec_ssh(client, "id -u", timeout=timeout)
    if c2 == 0 and out.strip() == "0":
        return ""
    raise RuntimeError("远端既不是 root，也无法无密码 sudo（sudo -n），无法写入 systemd")


def _resolve_openvpn_binary_remote(row: dict, client, sp: str, *, timeout: int = 30) -> str:
    pref = str(row.get("ssh_openvpn_binary") or "").strip()
    if pref:
        _, _, c = _exec_ssh(client, f"test -x {shlex.quote(pref)}", timeout=timeout)
        if c == 0:
            return pref
    out, _, c = _exec_ssh(client, f"{sp}bash -lc 'command -v openvpn || true'", timeout=timeout)
    line = (out or "").strip().splitlines()
    if line and line[0].strip():
        return line[0].strip()
    return "/usr/sbin/openvpn"


def _render_remote_peer_client_config(content: str) -> str:
    """为对端节点配置注入 OpenVPN 文件日志路径，不修改中心 .ovpn 成品。"""
    lines = str(content or "").replace("\r\n", "\n").replace("\r", "\n").splitlines()
    filtered = [
        line
        for line in lines
        if not line.strip().startswith(("log ", "log-append ", "status "))
    ]
    insert_at = len(filtered)
    for idx, line in enumerate(filtered):
        if line.strip().startswith("<"):
            insert_at = idx
            break
    logging_lines = [
        "",
        "# 对端节点文件日志（仅 SSH 下发到对端时注入）",
        f"log-append {PEER_REMOTE_CLIENT_LOG_PATH}",
        f"status {PEER_REMOTE_CLIENT_STATUS_PATH} 30",
    ]
    return "\n".join(filtered[:insert_at] + logging_lines + filtered[insert_at:]).rstrip() + "\n"


def _install_client_log_systemd_override(client, sp: str, service: str, *, timeout: int) -> None:
    """安装 client service drop-in 与 logrotate，允许写入并滚动 /etc/openvpn/log。"""
    override_dir = (
        "/etc/systemd/system/openvpn-client@.service.d"
        if service == OFFICIAL_OPENVPN_CLIENT_SERVICE
        else f"/etc/systemd/system/{service}.d"
    )
    override_path = f"{override_dir}/10-ovpn-mgmt-log.conf"
    content = "[Service]\nReadWritePaths=/etc/openvpn/log\n"
    rotate_conf = (
        f"{PEER_REMOTE_CLIENT_LOG_DIR}/client.log {{\n"
        "    daily\n"
        f"    rotate {int(LOG_RETENTION_DAYS)}\n"
        "    missingok\n"
        "    notifempty\n"
        "    compress\n"
        "    copytruncate\n"
        "}\n"
    )
    rotate_path = "/etc/logrotate.d/ovpn-mgmt-openvpn-client"
    cmd = (
        f"{sp}mkdir -p {shlex.quote(PEER_REMOTE_CLIENT_LOG_DIR)} {shlex.quote(override_dir)} && "
        f"printf %s {shlex.quote(content)} | {sp}tee {shlex.quote(override_path)} >/dev/null && "
        f"if command -v logrotate >/dev/null 2>&1; then "
        f"printf %s {shlex.quote(rotate_conf)} | {sp}tee {shlex.quote(rotate_path)} >/dev/null; "
        f"fi && "
        f"{sp}systemctl daemon-reload"
    )
    _, err, code = _exec_ssh(client, cmd, timeout=timeout)
    if code != 0:
        raise RuntimeError(f"写入 OpenVPN client 日志 drop-in 失败: {err.strip()}")


def _resolve_openvpn_client_service_remote(client, *, timeout: int = 30) -> dict[str, str | bool]:
    """解析对端 OpenVPN client systemd 服务名，优先官方模板。"""
    _, _, official = _exec_ssh(
        client,
        f"systemctl cat {OFFICIAL_OPENVPN_CLIENT_TEMPLATE} >/dev/null 2>&1",
        timeout=timeout,
    )
    if official == 0:
        return {
            "exists": True,
            "unit_source": "official",
            "service": OFFICIAL_OPENVPN_CLIENT_SERVICE,
            "template": OFFICIAL_OPENVPN_CLIENT_TEMPLATE,
            "config_path": OFFICIAL_OPENVPN_CLIENT_CONFIG_PATH,
            "log_path": PEER_REMOTE_CLIENT_LOG_PATH,
            "status_path": PEER_REMOTE_CLIENT_STATUS_PATH,
        }
    _, _, fallback = _exec_ssh(
        client,
        f"systemctl cat {FALLBACK_OPENVPN_CLIENT_SYSTEMD_UNIT} >/dev/null 2>&1",
        timeout=timeout,
    )
    if fallback == 0:
        return {
            "exists": True,
            "unit_source": "generated",
            "service": FALLBACK_OPENVPN_CLIENT_SYSTEMD_UNIT,
            "config_path": REMOTE_CLIENT_OVPN_PATH,
            "log_path": PEER_REMOTE_CLIENT_LOG_PATH,
            "status_path": PEER_REMOTE_CLIENT_STATUS_PATH,
        }
    return {
        "exists": False,
        "unit_source": "",
        "service": "",
        "config_path": REMOTE_CLIENT_OVPN_PATH,
        "log_path": PEER_REMOTE_CLIENT_LOG_PATH,
        "status_path": PEER_REMOTE_CLIENT_STATUS_PATH,
    }


def _parse_systemctl_show(text: str) -> dict[str, str]:
    """解析 ``systemctl show`` 的 key=value 输出。"""
    out: dict[str, str] = {}
    for raw in str(text or "").splitlines():
        if "=" not in raw:
            continue
        key, value = raw.split("=", 1)
        out[key.strip()] = value.strip()
    return out


def fetch_openvpn_client_service_status_via_ssh(
    row: dict,
    *,
    connect_timeout: int = 25,
    exec_timeout: int = 60,
) -> dict[str, str | bool]:
    """查询对端 OpenVPN client systemd 服务状态。"""
    client = connect_peer_ssh_client_from_row(row, connect_timeout=connect_timeout)
    try:
        resolved = _resolve_openvpn_client_service_remote(client, timeout=exec_timeout)
        if not resolved.get("exists"):
            return {"ok": True, **resolved, "active_state": "not-found", "sub_state": "not-found"}
        service = str(resolved["service"])
        out, err, code = _exec_ssh(
            client,
            f"systemctl show {shlex.quote(service)} "
            "--property=LoadState,ActiveState,SubState,UnitFileState --no-page",
            timeout=exec_timeout,
        )
        if code != 0:
            raise RuntimeError(f"查询对端 OpenVPN client 服务状态失败: {err.strip()}")
        props = _parse_systemctl_show(out)
        active, _, _ = _exec_ssh(client, f"systemctl is-active {shlex.quote(service)} 2>/dev/null || true", timeout=15)
        enabled, _, _ = _exec_ssh(client, f"systemctl is-enabled {shlex.quote(service)} 2>/dev/null || true", timeout=15)
        _, _, config_exists = _exec_ssh(
            client,
            f"test -f {shlex.quote(str(resolved.get('config_path') or REMOTE_CLIENT_OVPN_PATH))}",
            timeout=15,
        )
        return {
            "ok": True,
            **resolved,
            "load_state": props.get("LoadState", ""),
            "active_state": props.get("ActiveState", active.strip()),
            "sub_state": props.get("SubState", ""),
            "unit_file_state": props.get("UnitFileState", enabled.strip()),
            "is_active": active.strip(),
            "is_enabled": enabled.strip(),
            "config_exists": config_exists == 0,
        }
    finally:
        client.close()


def control_openvpn_client_service_via_ssh(
    row: dict,
    action: str,
    *,
    connect_timeout: int = 25,
    exec_timeout: int = 120,
) -> dict[str, str | bool]:
    """经 SSH 控制对端 OpenVPN client systemd 服务。"""
    act = str(action or "").strip().lower()
    if act not in _PEER_CLIENT_SERVICE_ACTIONS:
        raise ValueError(f"不支持的服务操作: {action}")
    client = connect_peer_ssh_client_from_row(row, connect_timeout=connect_timeout)
    try:
        resolved = _resolve_openvpn_client_service_remote(client, timeout=exec_timeout)
        if not resolved.get("exists"):
            raise RuntimeError("对端未发现 openvpn client systemd 服务，请先执行安装")
        sp = _detect_sudo_prefix_ovpn(client, timeout=15)
        service = str(resolved["service"])
        _, err, code = _exec_ssh(client, f"{sp}systemctl {act} {shlex.quote(service)}", timeout=exec_timeout)
        if code != 0:
            raise RuntimeError(f"对端 systemctl {act} 失败: {err.strip()}")
        logger.info("已执行对端 OpenVPN client 服务操作 action=%s service=%s", act, service)
        return {"ok": True, "action": act, **resolved}
    finally:
        client.close()


def fetch_openvpn_client_service_logs_via_ssh(
    row: dict,
    *,
    lines: int = 200,
    connect_timeout: int = 25,
    exec_timeout: int = 120,
) -> dict[str, str | bool | int]:
    """经 SSH 拉取对端 OpenVPN client 文件日志。"""
    n = max(1, min(int(lines), 2000))
    client = connect_peer_ssh_client_from_row(row, connect_timeout=connect_timeout)
    try:
        resolved = _resolve_openvpn_client_service_remote(client, timeout=exec_timeout)
        if not resolved.get("exists"):
            raise RuntimeError("对端未发现 openvpn client systemd 服务，请先执行安装")
        log_path = str(resolved.get("log_path") or PEER_REMOTE_CLIENT_LOG_PATH)
        out, err, code = _exec_ssh(
            client,
            f"tail -n {n} {shlex.quote(log_path)} 2>/dev/null",
            timeout=exec_timeout,
        )
        if code != 0:
            sp = _detect_sudo_prefix_ovpn(client, timeout=15)
            out, err, code = _exec_ssh(
                client,
                f"{sp}tail -n {n} {shlex.quote(log_path)}",
                timeout=exec_timeout,
            )
        if code != 0:
            raise RuntimeError(f"读取对端 OpenVPN client 文件日志失败: {err.strip()}")
        return {"ok": True, "lines": n, "log": out, **resolved}
    finally:
        client.close()


def deploy_openvpn_client_systemd_via_ssh(
    row: dict,
    *,
    config_path: str | None = None,
    connect_timeout: int = 25,
    exec_timeout: int = 120,
) -> dict[str, str | bool]:
    """优先启用发行版官方 ``openvpn-client@client.service``，无官方模板时才写入兜底 unit。

    Args:
        row: 对端 dict（SSH 字段）
        config_path: OpenVPN 配置文件远端路径，默认 ``REMOTE_CLIENT_OVPN_PATH``

    Raises:
        RuntimeError: SSH / systemctl 失败
    """
    rpath = (config_path or "").strip() or REMOTE_CLIENT_OVPN_PATH
    client = connect_peer_ssh_client_from_row(row, connect_timeout=connect_timeout)
    try:
        sp = _detect_sudo_prefix_ovpn(client, timeout=15)
        _, _, has_official_template = _exec_ssh(
            client,
            f"{sp}systemctl cat {OFFICIAL_OPENVPN_CLIENT_TEMPLATE} >/dev/null 2>&1",
            timeout=exec_timeout,
        )
        if has_official_template == 0:
            if rpath != OFFICIAL_OPENVPN_CLIENT_CONFIG_PATH:
                # 官方模板约定 /etc/openvpn/client/<实例名>.conf；自定义路径才补链接。
                prep = (
                    f"{sp}mkdir -p "
                    f"{shlex.quote(str(PurePosixPath(OFFICIAL_OPENVPN_CLIENT_CONFIG_PATH).parent))} && "
                    f"{sp}ln -sfn {shlex.quote(rpath)} {shlex.quote(OFFICIAL_OPENVPN_CLIENT_CONFIG_PATH)}"
                )
                _, err_p, cp = _exec_ssh(client, prep, timeout=exec_timeout)
                if cp != 0:
                    raise RuntimeError(f"准备官方 OpenVPN client 配置失败: {err_p.strip()}")
            _install_client_log_systemd_override(
                client,
                sp,
                OFFICIAL_OPENVPN_CLIENT_SERVICE,
                timeout=exec_timeout,
            )
            for cmd in (
                f"{sp}systemctl enable {OFFICIAL_OPENVPN_CLIENT_SERVICE}",
                f"{sp}systemctl restart {OFFICIAL_OPENVPN_CLIENT_SERVICE}",
            ):
                _, err, code = _exec_ssh(client, cmd, timeout=exec_timeout)
                if code != 0:
                    raise RuntimeError(f"systemctl 失败 ({cmd}): {err.strip()}")
            logger.info(
                "已启用发行版官方 OpenVPN client service=%s config=%s instance_config=%s",
                OFFICIAL_OPENVPN_CLIENT_SERVICE,
                rpath,
                OFFICIAL_OPENVPN_CLIENT_CONFIG_PATH,
            )
            return {
                "ok": True,
                "unit_source": "official",
                "template": OFFICIAL_OPENVPN_CLIENT_TEMPLATE,
                "service": OFFICIAL_OPENVPN_CLIENT_SERVICE,
                "config_path": rpath,
                "instance_config_path": OFFICIAL_OPENVPN_CLIENT_CONFIG_PATH,
                "log_path": PEER_REMOTE_CLIENT_LOG_PATH,
                "status_path": PEER_REMOTE_CLIENT_STATUS_PATH,
            }

        ov_bin = _resolve_openvpn_binary_remote(row, client, sp, timeout=30)
        unit = (
            "[Unit]\n"
            "Description=OpenVPN client (ovpn-mgmt)\n"
            "After=network-online.target\n"
            "Wants=network-online.target\n"
            "\n"
            "[Service]\n"
            "Type=simple\n"
            f"ExecStart={ov_bin} --config {rpath}\n"
            "Restart=on-failure\n"
            "RestartSec=5\n"
            "\n"
            "[Install]\n"
            "WantedBy=multi-user.target\n"
        )
        unit_path = f"/etc/systemd/system/{FALLBACK_OPENVPN_CLIENT_SYSTEMD_UNIT}"
        tmp_unit = f"/tmp/ovpn-mgmt-unit-{uuid.uuid4().hex[:12]}.service"
        sftp = client.open_sftp()
        try:
            sftp.putfo(io.BytesIO(unit.encode("utf-8")), tmp_unit)
        finally:
            sftp.close()
        inst = (
            f"{sp}install -m 0644 -T {shlex.quote(tmp_unit)} {shlex.quote(unit_path)} && "
            f"{sp}rm -f {shlex.quote(tmp_unit)}"
        )
        _, err_w, cw = _exec_ssh(client, inst, timeout=exec_timeout)
        if cw != 0:
            raise RuntimeError(f"写入 systemd unit 失败: {err_w.strip()}")
        logger.info("已写入远端 systemd unit path=%s openvpn=%s config=%s", unit_path, ov_bin, rpath)
        _install_client_log_systemd_override(
            client,
            sp,
            FALLBACK_OPENVPN_CLIENT_SYSTEMD_UNIT,
            timeout=exec_timeout,
        )

        for cmd in (
            f"{sp}systemctl enable {FALLBACK_OPENVPN_CLIENT_SYSTEMD_UNIT}",
            f"{sp}systemctl restart {FALLBACK_OPENVPN_CLIENT_SYSTEMD_UNIT}",
        ):
            _, err, code = _exec_ssh(client, cmd, timeout=exec_timeout)
            if code != 0:
                raise RuntimeError(f"systemctl 失败 ({cmd}): {err.strip()}")
        logger.info("已写入兜底 unit 并启动远端 %s", FALLBACK_OPENVPN_CLIENT_SYSTEMD_UNIT)
        return {
            "ok": True,
            "unit_source": "generated",
            "unit_path": unit_path,
            "openvpn_binary": ov_bin,
            "config_path": rpath,
            "service": FALLBACK_OPENVPN_CLIENT_SYSTEMD_UNIT,
            "log_path": PEER_REMOTE_CLIENT_LOG_PATH,
            "status_path": PEER_REMOTE_CLIENT_STATUS_PATH,
        }
    finally:
        client.close()


def _put_local_ovpn_to_remote(
    row: dict,
    local: Path,
    rpath: str,
    *,
    tmp_slug: str,
    connect_timeout: int = 25,
    exec_timeout: int = 120,
) -> dict[str, str | int]:
    """将本地已存在的 .ovpn 经 SFTP + 远端 sudo install 落位到 rpath。"""
    local = local.resolve()
    if not local.is_file():
        raise ValueError(f"本地文件不存在: {local}")

    parent = str(PurePosixPath(rpath).parent)
    if not parent or parent == ".":
        raise ValueError(f"非法远端路径: {rpath}")

    uname = str(row.get("bound_username") or "").strip()
    token = uuid.uuid4().hex[:12]
    tmp_remote = f"/tmp/ovpn-mgmt-peer-{token}-{tmp_slug}.ovpn"

    client = connect_peer_ssh_client_from_row(row, connect_timeout=connect_timeout)
    try:
        source_bytes = local.stat().st_size
        rendered = _render_remote_peer_client_config(local.read_text(encoding="utf-8", errors="replace"))
        rendered_bytes = rendered.encode("utf-8")
        sftp = client.open_sftp()
        try:
            sftp.putfo(io.BytesIO(rendered_bytes), tmp_remote)
        finally:
            sftp.close()
        logger.info("已 SFTP 上传至远端临时文件 %s local=%s", tmp_remote, local)

        prep = (
            f"sudo -n mkdir -p {shlex.quote(parent)} {shlex.quote(PEER_REMOTE_CLIENT_LOG_DIR)} && "
            f"sudo -n install -m 0600 -T {shlex.quote(tmp_remote)} {shlex.quote(rpath)} && "
            f"sudo -n rm -f {shlex.quote(tmp_remote)}"
        )
        _, err, code = _exec_ssh(client, prep, timeout=exec_timeout)
        if code != 0:
            raise RuntimeError(f"远端 install 失败（需 root 或 sudo -n）: {err.strip()}")

        logger.info("对端 .ovpn 已落位 remote=%s user=%s bytes=%s", rpath, uname or "—", len(rendered_bytes))
        return {
            "ok": True,
            "local_path": str(local),
            "remote_path": rpath,
            "bound_username": uname,
            "bytes": len(rendered_bytes),
            "source_bytes": source_bytes,
            "log_path": PEER_REMOTE_CLIENT_LOG_PATH,
            "status_path": PEER_REMOTE_CLIENT_STATUS_PATH,
        }
    finally:
        client.close()


def upload_bound_user_ovpn_via_ssh(
    row: dict,
    *,
    remote_path: str | None = None,
    connect_timeout: int = 25,
    exec_timeout: int = 120,
) -> dict[str, str | int]:
    """读取本地 ``OVPN_PROFILES_DIR/<绑定用户>.ovpn``，经 SFTP 暂存至远端 ``/tmp``，再用 ``sudo -n install`` 落位。

    Raises:
        ValueError: 对端或本地文件无效
        RuntimeError: SSH/SFTP/sudo 失败
    """
    uname = str(row.get("bound_username") or "").strip()
    if not uname:
        raise ValueError("对端未绑定用户，无法定位 .ovpn")
    local = OVPN_PROFILES_DIR / f"{uname}.ovpn"
    if not local.is_file():
        raise ValueError(f"本地不存在该用户的 .ovpn: {local}（请先在用户管理生成）")

    rpath = (remote_path or "").strip() or default_remote_ovpn_path(uname)
    return _put_local_ovpn_to_remote(
        row,
        local,
        rpath,
        tmp_slug=uname,
        connect_timeout=connect_timeout,
        exec_timeout=exec_timeout,
    )


def upload_custom_ovpn_file_via_ssh(
    row: dict,
    local_path: str | Path,
    *,
    remote_path: str | None = None,
    connect_timeout: int = 25,
    exec_timeout: int = 120,
) -> dict[str, str | int]:
    """从本机任意路径上传 .ovpn（手工修改后再下发等场景）。远端路径默认仍按绑定用户名落位。

    Raises:
        ValueError: 绑定用户或路径无效
        RuntimeError: SSH/SFTP/sudo 失败
    """
    uname = str(row.get("bound_username") or "").strip()
    if not uname:
        raise ValueError("对端未绑定用户，无法确定默认远端路径")
    local = Path(local_path)
    rpath = (remote_path or "").strip() or default_remote_ovpn_path(uname)
    return _put_local_ovpn_to_remote(
        row,
        local,
        rpath,
        tmp_slug=f"custom-{uname}",
        connect_timeout=connect_timeout,
        exec_timeout=exec_timeout,
    )
