# -*- coding: utf-8 -*-
"""OpenVPN 本机安全升级：候选版本 staging、服务端/客户端 preflight 与原子切换。"""

from __future__ import annotations

import logging
import shutil
import socket
import subprocess
import tempfile
import threading
import time
import uuid
from pathlib import Path
from typing import Callable, Iterable

from packaging.version import Version

from app.core.config import load_config
from app.services.openvpn.detector import (
    detect_distro_family,
    detect_openvpn,
    get_distro_info,
)
from app.services.openvpn.instance import resolve_server_conf_dir
from app.services.user.ovpn_gen import generate_ovpn

logger = logging.getLogger(__name__)

_OUTPUT = Callable[[str], None]
_PREFLIGHT_SECONDS = 3.0

_OPENVPN_UPGRADE_APPLY_LOCK = threading.Lock()

_SERVER_SIDE_EFFECT_DIRECTIVES = {
    "auth-user-pass-verify",
    "client-connect",
    "client-disconnect",
    "daemon",
    "down",
    "group",
    "ifconfig-pool-persist",
    "ipchange",
    "learn-address",
    "log",
    "log-append",
    "management",
    "plugin",
    "route-up",
    "script-security",
    "status",
    "tls-crypt-v2-verify",
    "up",
    "user",
    "writepid",
}
_CLIENT_SIDE_EFFECT_DIRECTIVES = {
    "connect-retry",
    "connect-retry-max",
    "connect-timeout",
    "daemon",
    "dev",
    "down",
    "log",
    "log-append",
    "management",
    "remote",
    "resolv-retry",
    "route-up",
    "up",
    "writepid",
}
_FATAL_CONFIG_MARKERS = (
    "options error:",
    "unrecognized option",
    "cannot load",
    "cannot read",
    "error reading",
    "failed to parse",
    "cannot pre-load",
    "private key password verification failed",
    "openssl: error",
    "error: private key password verification failed",
    "exiting due to fatal error",
)
_SERVER_PREFLIGHT_SUCCESS_MARKERS = (
    "initialization sequence completed",
)
_CLIENT_PREFLIGHT_SUCCESS_MARKERS = (
    "udp link remote:",
    "udpv4 link remote:",
    "udpv6 link remote:",
    "attempting to establish tcp connection",
)


def _emit(callback: _OUTPUT | None, message: str) -> None:
    logger.info(message)
    if callback is not None:
        callback(message)


def _mirror_installer_output(callback: _OUTPUT | None = None) -> _OUTPUT:
    """把 installer 的详细输出同时镜像到独立升级日志。"""

    def sink(message: str) -> None:
        logger.info(message)
        if callback is not None:
            callback(message)

    return sink


def _run(
    cmd: list[str],
    *,
    timeout: float | None = None,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def _free_local_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _openvpn_version(binary: str | Path) -> str:
    proc = _run([str(binary), "--version"], timeout=10)
    output = (proc.stdout or proc.stderr or "").splitlines()
    first = output[0] if output else ""
    for token in first.replace("/", " ").split():
        value = token.strip().lstrip("v")
        try:
            Version(value)
        except Exception:
            continue
        if value.count(".") >= 2:
            return value
    raise RuntimeError(f"无法识别候选 OpenVPN 版本: {first or proc.stderr.strip()}")


def inspect_upgrade(on_output: _OUTPUT | None = None) -> dict:
    """仅对 NiceVPN 管理的源码版本提供原子升级；系统包交由包管理器维护。"""

    current = detect_openvpn()
    mode = str(current.get("install_mode") or "")
    _emit(
        on_output,
        f"[检测] 当前 OpenVPN: {current.get('version') or 'unknown'} "
        f"({current.get('path') or 'not-found'}; mode={mode or 'unknown'})",
    )
    if mode != "source":
        reason = (
            "当前 OpenVPN 由系统包管理器安装，请使用 apt/dnf/yum 更新；"
            "NiceVPN 不会覆盖系统包二进制。"
            if mode == "package"
            else "当前 OpenVPN 使用自定义路径，NiceVPN 不会擅自迁移或覆盖该二进制。"
        )
        return {
            "current": current,
            "release": None,
            "target_version": None,
            "upgrade_available": False,
            "reason": reason,
        }

    from app.services.openvpn.installer import resolve_latest_openvpn_release

    release = resolve_latest_openvpn_release(
        on_output=_mirror_installer_output(on_output),
    )
    target_version = str(release["tag_name"]).lstrip("v")
    current_version = str(current.get("version") or "")
    try:
        upgrade_available = not current_version or Version(target_version) > Version(current_version)
    except Exception:
        upgrade_available = True
    return {
        "current": current,
        "release": release,
        "target_version": target_version,
        "upgrade_available": upgrade_available,
        "reason": None if upgrade_available else "当前源码安装版本已经是最新版本。",
    }


def stage_upgrade_candidate(on_output: _OUTPUT | None = None) -> dict:
    """通过正式 install shell 入口把候选 OpenVPN 构建到隔离目录。"""

    current = detect_openvpn()
    if current.get("install_mode") != "source":
        raise RuntimeError(
            "仅 NiceVPN 管理的源码安装支持此升级流程；系统包请使用 apt/dnf/yum 更新，"
            "自定义路径请自行维护。"
        )
    base_version = str(current.get("version") or "")
    base_binary = str(current.get("path") or "")
    if not base_version or not Path(base_binary).is_file():
        raise RuntimeError("无法确认当前 OpenVPN 版本与 binary，拒绝创建升级候选")

    from app.services.openvpn.installer import stage_openvpn_source_build

    info = get_distro_info()
    distro = detect_distro_family(info)
    if not distro:
        raise RuntimeError("无法识别系统发行版族，不能自动升级 OpenVPN")

    version_id = str(info.get("version_id") or "")
    stage_root = Path(tempfile.mkdtemp(prefix="nicevpn-openvpn-upgrade-"))
    prefix = stage_root / "prefix"
    src_root = stage_root / "src"
    cache = stage_root / "openvpn-source.tar.gz"

    try:
        staged = stage_openvpn_source_build(
            distro,
            version_id,
            prefix=prefix,
            cache=cache,
            src_root=src_root,
            on_output=_mirror_installer_output(on_output),
        )
        version = _openvpn_version(staged["binary"])
    except Exception:
        shutil.rmtree(stage_root, ignore_errors=True)
        raise

    return {
        "stage_root": str(stage_root),
        **staged,
        "version": version,
        "base_version": base_version,
        "base_binary_realpath": str(Path(base_binary).resolve(strict=True)),
    }


def _rewrite_config(
    text: str,
    *,
    disabled_directives: set[str],
    replacements: dict[str, str],
    append_lines: Iterable[str],
) -> str:
    """只改顶层指令，inline <cert>/<key>/... 内容保持原样。"""

    output: list[str] = []
    inline_name: str | None = None
    replaced: set[str] = set()
    for raw in str(text or "").splitlines():
        stripped = raw.strip()
        if inline_name is not None:
            output.append(raw)
            if stripped.lower() == f"</{inline_name}>":
                inline_name = None
            continue

        if stripped.startswith("<") and stripped.endswith(">") and not stripped.startswith("</"):
            inline_name = stripped[1:-1].strip().lower()
            output.append(raw)
            continue

        if not stripped or stripped.startswith(("#", ";")):
            output.append(raw)
            continue

        directive = stripped.split(None, 1)[0].lower()
        if directive in replacements:
            if directive not in replaced:
                output.append(replacements[directive])
                replaced.add(directive)
            else:
                output.append(f"# NiceVPN preflight disabled duplicate: {raw}")
            continue
        if directive in disabled_directives:
            output.append(f"# NiceVPN preflight disabled: {raw}")
            continue
        output.append(raw)

    for directive, line in replacements.items():
        if directive not in replaced:
            output.append(line)
    output.extend(append_lines)
    return "\n".join(output).rstrip() + "\n"


def _preflight_output_state(
    output: str,
    success_markers: tuple[str, ...],
) -> tuple[bool, bool]:
    """返回 (命中成功阶段, 出现 fatal)；fatal 永远优先于成功。"""

    lowered = str(output or "").casefold()
    fatal = any(marker in lowered for marker in _FATAL_CONFIG_MARKERS)
    success = any(marker in lowered for marker in success_markers)
    return success, fatal


def _run_foreground_preflight(
    binary: str,
    config_path: Path,
    *,
    success_markers: tuple[str, ...],
    seconds: float = _PREFLIGHT_SECONDS,
) -> tuple[bool, str, bool]:
    """运行到明确成功阶段；必须命中 success marker，不能仅凭进程存活判定。"""

    log_path = config_path.with_suffix(config_path.suffix + ".log")
    reached_success = False
    fatal_before_success = False

    with log_path.open("w", encoding="utf-8") as log_file:
        proc = subprocess.Popen(
            [binary, "--config", str(config_path)],
            cwd=str(config_path.parent),
            stdin=subprocess.DEVNULL,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            text=True,
        )
        deadline = time.monotonic() + max(0.5, float(seconds))
        while time.monotonic() < deadline:
            try:
                output = log_path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                output = ""
            reached_success, fatal_before_success = _preflight_output_state(
                output,
                success_markers,
            )
            if fatal_before_success:
                break

            if reached_success:
                break

            if proc.poll() is not None:
                break
            time.sleep(0.05)

        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=2)

    output = log_path.read_text(encoding="utf-8", errors="replace") if log_path.exists() else ""
    _, fatal_after_stop = _preflight_output_state(output, success_markers)
    fatal = fatal_before_success or fatal_after_stop
    return reached_success and not fatal, output, reached_success


def preflight_server_config(binary: str, conf_path: str | Path) -> dict:
    """用候选 binary 真正初始化一次 server.conf，所有副作用写入临时目录。"""

    source = Path(conf_path)
    if not source.is_file():
        return {"ok": False, "path": str(source), "error": "配置文件不存在"}

    with tempfile.TemporaryDirectory(prefix="nicevpn-server-preflight-") as td:
        root = Path(td)
        port = _free_local_port()
        tun_name = "tunpf" + uuid.uuid4().hex[:8]
        candidate = root / source.name
        text = _rewrite_config(
            source.read_text(encoding="utf-8"),
            disabled_directives=_SERVER_SIDE_EFFECT_DIRECTIVES | {"dev", "local", "port"},
            replacements={},
            append_lines=[
                f"dev {tun_name}",
                "dev-type tun",
                "local 127.0.0.1",
                f"port {port}",
                "ifconfig-noexec",
                "route-noexec",
                "disable-dco",
                "script-security 0",
                f"status {root / 'status.log'} 1",
                "verb 4",
            ],
        )
        candidate.write_text(text, encoding="utf-8")
        ok, output, reached_success = _run_foreground_preflight(
            binary,
            candidate,
            success_markers=_SERVER_PREFLIGHT_SUCCESS_MARKERS,
        )
        return {
            "ok": ok,
            "path": str(source),
            "reached_success_marker": reached_success,
            "log_tail": "\n".join(output.splitlines()[-30:]),
            "error": None if ok else "候选 OpenVPN 无法完成服务端配置初始化",
        }


def _generate_ephemeral_client_profile(binary: str, root: Path) -> Path:
    """生成一次性客户端配置，不读取现有用户 .ovpn，也不写入 PKI/SQLite。"""

    openssl = shutil.which("openssl")
    if not openssl:
        raise RuntimeError("系统缺少 openssl，无法生成一次性客户端兼容性配置")

    cert = root / "client.crt"
    key = root / "client.key"
    proc = _run([
        openssl,
        "req",
        "-x509",
        "-newkey",
        "rsa:2048",
        "-nodes",
        "-days",
        "1",
        "-subj",
        "/CN=nicevpn-preflight",
        "-keyout",
        str(key),
        "-out",
        str(cert),
    ], timeout=30)
    if proc.returncode != 0:
        raise RuntimeError(f"生成一次性客户端证书失败: {proc.stderr.strip()}")

    cfg = load_config()
    pki_dir = Path(str(cfg.get("pki_dir") or "/etc/openvpn/pki"))
    existing_server_key = pki_dir.parent / "tc2-server.key"
    tc2_server = existing_server_key if existing_server_key.is_file() else root / "tc2-server.key"
    if not tc2_server.is_file():
        proc = _run([binary, "--genkey", "tls-crypt-v2-server", str(tc2_server)], timeout=15)
        if proc.returncode != 0:
            raise RuntimeError(f"生成一次性 tls-crypt-v2 server key 失败: {proc.stderr.strip()}")

    tc2_client = root / "tc2-client.key"
    proc = _run([
        binary,
        "--tls-crypt-v2",
        str(tc2_server),
        "--genkey",
        "tls-crypt-v2-client",
        str(tc2_client),
    ], timeout=15)
    if proc.returncode != 0:
        raise RuntimeError(f"候选 OpenVPN 无法生成 tls-crypt-v2 client key: {proc.stderr.strip()}")

    content = generate_ovpn(
        "nicevpn-preflight",
        "10.255.255.0/24",
        {
            "server_ip": "127.0.0.1",
            "port": 9,
            "proto": "udp",
            "ca_cert": cert.read_text(encoding="utf-8"),
            "user_cert": cert.read_text(encoding="utf-8"),
            "user_key": key.read_text(encoding="utf-8"),
            "tc2_client_key": tc2_client.read_text(encoding="utf-8"),
        },
    )
    profile = root / "nicevpn-preflight.ovpn"
    profile.write_text(content, encoding="utf-8")
    return profile


def preflight_client_profile(binary: str) -> dict:
    """始终生成一次性客户端 .ovpn，避免设备绑定/用户状态污染版本兼容性判断。"""

    with tempfile.TemporaryDirectory(prefix="nicevpn-client-preflight-") as td:
        root = Path(td)
        source = _generate_ephemeral_client_profile(binary, root)
        if not source.is_file():
            return {"ok": False, "error": f"临时客户端配置不存在: {source}"}

        original = source.read_text(encoding="utf-8")
        port = _free_local_port()
        tun_name = "tunpc" + uuid.uuid4().hex[:8]
        candidate = root / "client.ovpn"
        text = _rewrite_config(
            original,
            disabled_directives=_CLIENT_SIDE_EFFECT_DIRECTIVES,
            replacements={},
            append_lines=[
                f"dev {tun_name}",
                "dev-type tun",
                f"remote 127.0.0.1 {port}",
                "resolv-retry 0",
                "connect-retry 1 1",
                "connect-retry-max 1",
                "connect-timeout 1",
                "tls-exit",
                "ifconfig-noexec",
                "route-noexec",
                "disable-dco",
                "verb 4",
            ],
        )
        candidate.write_text(text, encoding="utf-8")
        ok, output, reached_success = _run_foreground_preflight(
            binary,
            candidate,
            success_markers=_CLIENT_PREFLIGHT_SUCCESS_MARKERS,
        )

        return {
            "ok": ok,
            "profile": str(source),
            "generated": True,
            "source": "temporary",
            "reached_success_marker": reached_success,
            "log_tail": "\n".join(output.splitlines()[-30:]),
            "error": None if ok else "候选 OpenVPN 无法解析/初始化临时客户端配置",
        }


def preflight_upgrade_candidate(stage: dict, on_output: _OUTPUT | None = None) -> dict:
    """候选版本必须通过全部 server.conf + 代表性客户端 .ovpn。"""

    binary = str(stage.get("binary") or "")
    if not Path(binary).is_file():
        raise RuntimeError("候选 OpenVPN binary 不存在")

    cfg = load_config()
    conf_dir = resolve_server_conf_dir(str(cfg.get("openvpn_conf_dir") or "/etc/openvpn"))
    server_paths = sorted(conf_dir.glob("*.conf"))
    if not server_paths:
        raise RuntimeError(f"未找到 OpenVPN 服务端配置: {conf_dir}")

    server_results = []
    for path in server_paths:
        _emit(on_output, f"[Preflight] 服务端配置: {path.name}")
        result = preflight_server_config(binary, path)
        server_results.append(result)
        log_tail = str(result.get("log_tail") or "").strip()
        if log_tail:
            _emit(
                on_output,
                f"[Preflight][server:{path.name}] OpenVPN 输出尾部:\n{log_tail}",
            )
        if not result["ok"]:
            return {
                "ok": False,
                "server_results": server_results,
                "client_result": None,
                "error": f"服务端配置不兼容: {path.name}",
            }

    _emit(on_output, "[Preflight] 生成并验证一次性客户端 .ovpn")
    client_result = preflight_client_profile(binary)
    client_log_tail = str(client_result.get("log_tail") or "").strip()
    if client_log_tail:
        source_label = "temporary"
        _emit(
            on_output,
            f"[Preflight][client:{source_label}] OpenVPN 输出尾部:\n{client_log_tail}",
        )
    ok = bool(client_result.get("ok"))
    return {
        "ok": ok,
        "server_results": server_results,
        "client_result": client_result,
        "error": None if ok else str(client_result.get("error") or "客户端配置不兼容"),
    }


def cleanup_staged_candidate(stage: dict | None) -> None:
    root = Path(str((stage or {}).get("stage_root") or ""))
    if root.is_dir() and root.name.startswith("nicevpn-openvpn-upgrade-"):
        shutil.rmtree(root, ignore_errors=True)


def apply_staged_upgrade(stage: dict, on_output: _OUTPUT | None = None) -> dict:
    """串行执行候选预检与 Shell release 切换。"""

    if not _OPENVPN_UPGRADE_APPLY_LOCK.acquire(blocking=False):
        raise RuntimeError("OpenVPN 升级正在执行，请勿重复发起")
    try:
        return _apply_staged_upgrade_locked(stage, on_output)
    finally:
        _OPENVPN_UPGRADE_APPLY_LOCK.release()


def _apply_staged_upgrade_locked(stage: dict, on_output: _OUTPUT | None = None) -> dict:
    """Python 负责兼容性预检；版本发布、软链接切换和回滚统一由 Shell 完成。"""

    current = detect_openvpn()
    if current.get("install_mode") != "source":
        raise RuntimeError(
            "当前 OpenVPN 不是 NiceVPN 管理的源码安装；系统包请使用 apt/dnf/yum 更新。"
        )

    current_binary = Path(str(current.get("path") or "")).expanduser()
    if not current_binary.is_file():
        raise RuntimeError("当前 OpenVPN binary 不存在，无法安全升级")
    current_realpath = str(current_binary.resolve(strict=True))
    expected_current = str(stage.get("base_binary_realpath") or "")
    if expected_current and current_realpath != expected_current:
        raise RuntimeError(
            "候选 OpenVPN 已过期：预检后当前安装版本发生变化，请重新检查并生成候选版本。"
        )

    stage_binary = Path(str(stage.get("binary") or "")).expanduser()
    stage_prefix = Path(str(stage.get("prefix") or "")).expanduser()
    if not stage_binary.is_file() or not stage_prefix.is_dir():
        raise RuntimeError("候选 OpenVPN staging 目录不完整，请重新生成候选版本")

    version = str(stage.get("version") or _openvpn_version(stage_binary))
    actual_version = _openvpn_version(stage_binary)
    if actual_version != version:
        raise RuntimeError(
            f"候选 OpenVPN 版本信息不一致：记录为 {version}，binary 实际为 {actual_version}"
        )

    preflight = preflight_upgrade_candidate(stage, on_output)
    if not preflight.get("ok"):
        raise RuntimeError(str(preflight.get("error") or "候选 OpenVPN preflight 失败"))

    from app.services.openvpn.installer import apply_openvpn_release_upgrade

    apply_openvpn_release_upgrade(
        stage_prefix=stage_prefix,
        expected_version=version,
        expected_current_binary=current_realpath,
        on_output=_mirror_installer_output(on_output),
    )

    _emit(on_output, f"[完成] OpenVPN {version} 升级成功，旧版本目录保留，可通过稳定软链接回退")
    return {
        "ok": True,
        "version": version,
        "previous_binary": current_realpath,
        "preflight": preflight,
    }
