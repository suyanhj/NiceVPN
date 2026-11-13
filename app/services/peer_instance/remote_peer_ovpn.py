# -*- coding: utf-8 -*-
"""经 SSH 将客户端 .ovpn 上传到对端（中心成品路径或本机任意路径）。"""
from __future__ import annotations

import io
import logging
import re
import shlex
import uuid
from pathlib import Path, PurePosixPath

from app.core.constants import LOG_RETENTION_DAYS, OVPN_PROFILES_DIR
from app.services.peer_instance.peer_ssh_connect import connect_peer_ssh_client_from_row
from app.services.peer_instance.remote_ssh_exec import exec_ssh as _exec_ssh

logger = logging.getLogger("peer.remote")

_PEER_SCRIPTS_DIR = Path(__file__).resolve().parent.parent.parent / "scripts" / "peer"


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


def _read_peer_script_text(name: str) -> str:
    """读取 ``app/scripts/peer/`` 下脚本或模板正文。"""
    path = _PEER_SCRIPTS_DIR / name
    if not path.is_file():
        raise FileNotFoundError(f"对端脚本不存在: {path}")
    return path.read_text(encoding="utf-8").replace("\r\n", "\n")


def _strip_peer_script_wrapper(text: str) -> str:
    """去掉入口脚本的 shebang、set -euo、SCRIPT_DIR、source 行，保留业务逻辑。"""
    out_lines: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            out_lines.append(line)
            continue
        if stripped.startswith("#!"):
            continue
        if stripped == "set -euo pipefail":
            continue
        if stripped.startswith("SCRIPT_DIR="):
            continue
        if stripped.startswith("# shellcheck"):
            continue
        if "source " in stripped and ".sh" in stripped:
            continue
        out_lines.append(line)
    return "\n".join(out_lines).strip() + "\n"


def _bundle_peer_script(script_name: str, env: dict[str, str]) -> str:
    """将对端脚本与环境变量打包为可在 SSH 上执行的 bash。"""
    lines = ["set -eux"]
    for key, value in env.items():
        lines.append(f"export {key}={shlex.quote(str(value))}")
    body = _strip_peer_script_wrapper(_read_peer_script_text(script_name))
    return "\n".join(lines) + "\n" + body


def _exec_bundled_peer_script(
    client,
    sp: str,
    script_name: str,
    env: dict[str, str],
    *,
    timeout: int,
    error_hint: str,
) -> None:
    """经 SSH 执行打包后的对端 shell 脚本。"""
    wrapped = f"{sp}bash -lc {shlex.quote(_bundle_peer_script(script_name, env))}"
    _, err, code = _exec_ssh(client, wrapped, timeout=timeout)
    if code != 0:
        raise RuntimeError(f"{error_hint}: {err.strip()}")


def _client_log_setup_env(service: str) -> dict[str, str]:
    """构造 ``openvpn-client-log-setup.sh`` 所需环境变量。"""
    return {
        "SERVICE_NAME": service,
        "CLIENT_LOG_DIR": PEER_REMOTE_CLIENT_LOG_DIR,
        "LOG_ROTATE_DAYS": str(int(LOG_RETENTION_DAYS)),
    }


def _render_fallback_unit_template() -> str:
    """读取兜底 systemd unit 模板（占位符由 shell 脚本 sed 替换）。"""
    return _read_peer_script_text("openvpn-client-fallback.service.in")


def _install_client_log_systemd_override(client, sp: str, service: str, *, timeout: int) -> None:
    """安装 client service drop-in 与 logrotate，允许写入并滚动 /etc/openvpn/log。"""
    _exec_bundled_peer_script(
        client,
        sp,
        "openvpn-client-log-setup.sh",
        _client_log_setup_env(service),
        timeout=timeout,
        error_hint="写入 OpenVPN client 日志 drop-in 失败",
    )


def _install_fallback_client_systemd_unit(
    client,
    sp: str,
    *,
    openvpn_bin: str,
    config_path: str,
    timeout: int,
) -> None:
    """写入兜底 openvpn-client.service unit 并 daemon-reload。"""
    _exec_bundled_peer_script(
        client,
        sp,
        "openvpn-client-install-fallback.sh",
        {
            "OPENVPN_BIN": openvpn_bin,
            "CONFIG_PATH": config_path,
            "SERVICE_NAME": FALLBACK_OPENVPN_CLIENT_SYSTEMD_UNIT,
            "UNIT_TEMPLATE": _render_fallback_unit_template(),
        },
        timeout=timeout,
        error_hint="写入兜底 OpenVPN client systemd unit 失败",
    )


def _parse_systemctl_list_unit_files(text: str) -> dict[str, str]:
    """解析 ``systemctl list-unit-files`` 中的 OpenVPN service 文件。"""
    result: dict[str, str] = {}
    for raw in str(text or "").splitlines():
        fields = raw.split()
        if len(fields) < 1:
            continue
        unit = fields[0].strip()
        if unit.lower().startswith("openvpn") and unit.endswith(".service"):
            result[unit] = fields[1].strip() if len(fields) > 1 else ""
    return result


def _parse_systemctl_list_units(text: str) -> dict[str, dict[str, str]]:
    """解析 ``systemctl list-units --all`` 中的 OpenVPN service 运行状态。"""
    result: dict[str, dict[str, str]] = {}
    for raw in str(text or "").splitlines():
        fields = raw.split(None, 4)
        if len(fields) < 4:
            continue
        unit = fields[0].strip()
        if not unit.lower().startswith("openvpn") or not unit.endswith(".service"):
            continue
        result[unit] = {
            "load_state": fields[1].strip(),
            "active_state": fields[2].strip(),
            "sub_state": fields[3].strip(),
        }
    return result


def _systemd_template_for_service(service: str) -> str:
    """返回实例服务对应的模板文件名。"""
    name = str(service or "").strip()
    if name.startswith("openvpn-client@") and not name.endswith("@.service"):
        return OFFICIAL_OPENVPN_CLIENT_TEMPLATE
    if name.startswith("openvpn@") and not name.endswith("@.service"):
        return "openvpn@.service"
    return ""


def _extract_openvpn_config_path(exec_start: str, service: str) -> str | None:
    """从 systemd ExecStart 中解析 --config/-c 与 --cd，兼容模板实例 %i。"""
    command = str(exec_start or "")
    config_match = re.search(
        r"(?:--config|-c)(?:=|\\s+)(?:\"([^\"]+)\"|'([^']+)'|([^\\s;}}\\]]+))",
        command,
    )
    if not config_match:
        return None
    config_path = next((part for part in config_match.groups() if part), "").strip()
    if not config_path:
        return None

    instance = ""
    if "@" in service:
        instance = service.split("@", 1)[1].split(".service", 1)[0]
    if instance:
        config_path = config_path.replace("%i", instance).replace("%I", instance)

    cd_match = re.search(
        r"--cd(?:=|\\s+)(?:\"([^\"]+)\"|'([^']+)'|([^\\s;}}\\]]+))",
        command,
    )
    cd_path = next((part for part in cd_match.groups() if part), "").strip() if cd_match else ""
    if not config_path.startswith("/") and cd_path:
        config_path = str(PurePosixPath(cd_path) / config_path)
    return config_path


def _resolve_openvpn_client_service_remote(client, *, timeout: int = 30) -> dict[str, str | bool]:
    """通过 systemctl 枚举远端 OpenVPN 服务文件及运行实例，不假定服务名固定。"""
    files_out, _, _ = _exec_ssh(
        client,
        "systemctl list-unit-files --type=service --no-legend --no-pager 2>/dev/null || true",
        timeout=timeout,
    )
    units_out, _, _ = _exec_ssh(
        client,
        "systemctl list-units --type=service --all --no-legend --no-pager 2>/dev/null || true",
        timeout=timeout,
    )
    unit_files = _parse_systemctl_list_unit_files(files_out)
    running_units = _parse_systemctl_list_units(units_out)

    candidates: list[str] = []
    for unit in sorted(set(unit_files) | set(running_units)):
        lower = unit.lower()
        if lower == OFFICIAL_OPENVPN_CLIENT_TEMPLATE or lower == "openvpn@.service":
            continue
        if lower == FALLBACK_OPENVPN_CLIENT_SYSTEMD_UNIT:
            candidates.append(unit)
        elif lower.startswith("openvpn-client@") and not lower.endswith("@.service"):
            candidates.append(unit)
        elif lower.startswith("openvpn-client") and lower.endswith(".service"):
            candidates.append(unit)
        elif lower.startswith("openvpn@") and not lower.endswith("@.service"):
            instance = lower.split("@", 1)[1].split(".service", 1)[0]
            if instance == "client" or "client" in instance:
                candidates.append(unit)

    # 有官方模板但尚未启动实例时，仍检查默认实例是否可加载；不会在此处启动它。
    if OFFICIAL_OPENVPN_CLIENT_TEMPLATE in unit_files:
        candidates.append(OFFICIAL_OPENVPN_CLIENT_SERVICE)

    candidates = list(dict.fromkeys(candidates))
    if not candidates:
        return {
            "exists": False,
            "service_file_exists": False,
            "unit_source": "",
            "service": "",
            "template": "",
            "config_path": REMOTE_CLIENT_OVPN_PATH,
            "log_path": PEER_REMOTE_CLIENT_LOG_PATH,
            "status_path": PEER_REMOTE_CLIENT_STATUS_PATH,
            "active_state": "not-found",
            "sub_state": "not-found",
        }

    def _candidate_rank(service: str) -> tuple[int, int, str]:
        active = running_units.get(service, {}).get("active_state") == "active"
        if service == OFFICIAL_OPENVPN_CLIENT_SERVICE or service.startswith("openvpn-client@"):
            rank = 0
        elif service == FALLBACK_OPENVPN_CLIENT_SYSTEMD_UNIT:
            rank = 1
        elif service.startswith("openvpn-client"):
            rank = 2
        else:
            rank = 3
        return (0 if active else 1, rank, service)

    service = sorted(candidates, key=_candidate_rank)[0]
    template = _systemd_template_for_service(service)
    if service == OFFICIAL_OPENVPN_CLIENT_SERVICE:
        template = OFFICIAL_OPENVPN_CLIENT_TEMPLATE
    unit_source = (
        "official"
        if template == OFFICIAL_OPENVPN_CLIENT_TEMPLATE
        else "generated"
        if service == FALLBACK_OPENVPN_CLIENT_SYSTEMD_UNIT
        else "discovered"
    )

    out, _, code = _exec_ssh(
        client,
        f"systemctl show {shlex.quote(service)} "
        "--property=LoadState,ActiveState,SubState,UnitFileState,FragmentPath,ExecStart --no-page",
        timeout=timeout,
    )
    props = _parse_systemctl_show(out) if code == 0 else {}
    listed = running_units.get(service, {})
    load_state = props.get("LoadState") or listed.get("load_state", "")
    active_state = props.get("ActiveState") or listed.get("active_state", "inactive")
    sub_state = props.get("SubState") or listed.get("sub_state", "")
    unit_file_state = (
        props.get("UnitFileState")
        or unit_files.get(service)
        or unit_files.get(template)
        or ""
    )
    fragment_path = props.get("FragmentPath", "")
    service_file_exists = bool(
        unit_files.get(service)
        or (template and unit_files.get(template))
        or (load_state == "loaded" and fragment_path and fragment_path != "/dev/null")
    )
    config_path = _extract_openvpn_config_path(props.get("ExecStart", ""), service)
    if not config_path:
        config_path = REMOTE_CLIENT_OVPN_PATH

    return {
        "exists": service_file_exists,
        "service_file_exists": service_file_exists,
        "unit_source": unit_source,
        "service": service,
        "template": template,
        "config_path": config_path,
        "log_path": PEER_REMOTE_CLIENT_LOG_PATH,
        "status_path": PEER_REMOTE_CLIENT_STATUS_PATH,
        "load_state": load_state,
        "active_state": active_state,
        "sub_state": sub_state,
        "unit_file_state": unit_file_state,
        "fragment_path": fragment_path,
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
    """查询对端 OpenVPN client service、配置文件和运行状态。"""
    client = connect_peer_ssh_client_from_row(row, connect_timeout=connect_timeout)
    try:
        resolved = _resolve_openvpn_client_service_remote(client, timeout=exec_timeout)
        config_path = str(resolved.get("config_path") or REMOTE_CLIENT_OVPN_PATH)
        _, _, config_code = _exec_ssh(
            client,
            f"test -f {shlex.quote(config_path)}",
            timeout=15,
        )
        config_exists = config_code == 0
        if not resolved.get("service"):
            return {
                "ok": True,
                **resolved,
                "config_exists": config_exists,
                "is_active": str(resolved.get("active_state") or "not-found"),
                "is_enabled": "",
            }

        return {
            "ok": True,
            **resolved,
            "config_exists": config_exists,
            "is_active": str(resolved.get("active_state") or ""),
            "is_enabled": str(resolved.get("unit_file_state") or ""),
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
        _install_fallback_client_systemd_unit(
            client,
            sp,
            openvpn_bin=ov_bin,
            config_path=rpath,
            timeout=exec_timeout,
        )
        logger.info(
            "已写入远端兜底 systemd unit service=%s openvpn=%s config=%s",
            FALLBACK_OPENVPN_CLIENT_SYSTEMD_UNIT,
            ov_bin,
            rpath,
        )
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
            "unit_path": f"/etc/systemd/system/{FALLBACK_OPENVPN_CLIENT_SYSTEMD_UNIT}",
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

        sp = _detect_sudo_prefix_ovpn(client, timeout=15)
        prep = (
            f"{sp}mkdir -p {shlex.quote(parent)} "
            f"{shlex.quote(PEER_REMOTE_CLIENT_LOG_DIR)} && "
            f"{sp}install -m 0600 -T {shlex.quote(tmp_remote)} "
            f"{shlex.quote(rpath)} && "
            f"{sp}rm -f {shlex.quote(tmp_remote)}"
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
        RuntimeError: SSH/SFTP/root 或 sudo -n 权限失败
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
