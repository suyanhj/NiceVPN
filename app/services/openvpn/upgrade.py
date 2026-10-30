# -*- coding: utf-8 -*-
"""OpenVPN 本机安全升级：候选版本 staging、服务端/客户端 preflight 与原子切换。"""

from __future__ import annotations

import logging
import os
import shutil
import socket
import subprocess
import tempfile
import time
import uuid
from pathlib import Path
from typing import Callable, Iterable

from packaging.version import Version

from app.core.config import load_config
from app.core.constants import OPENVPN_INSTALL_PREFIX, OVPN_PROFILES_DIR
from app.services.openvpn.detector import (
    detect_distro_family,
    detect_openvpn,
    get_distro_info,
)
from app.services.openvpn.instance import (
    is_instance_active,
    iter_all_openvpn_instance_names,
    resolve_server_conf_dir,
)
from app.services.user.ovpn_gen import generate_ovpn

logger = logging.getLogger(__name__)

_OUTPUT = Callable[[str], None]
_PREFLIGHT_SECONDS = 3.0
_COMMON_SCRIPT = Path(__file__).resolve().parent.parent.parent / "scripts" / "install" / "openvpn-install-common.sh"

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
)
_CLIENT_READY_MARKERS = (
    "udp link remote:",
    "tcp/udp: preserving recently used remote address:",
    "attempting to establish tcp connection",
    "initialization sequence completed",
)


def _emit(callback: _OUTPUT | None, message: str) -> None:
    logger.info(message)
    if callback is not None:
        callback(message)


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


def _install_build_dependencies(distro: str, on_output: _OUTPUT | None = None) -> None:
    """只安装编译依赖，不安装/替换正在使用的 OpenVPN。"""

    distro = str(distro or "").strip().lower()
    if distro == "debian":
        commands = [
            ["apt", "update", "-y"],
            [
                "apt",
                "install",
                "-y",
                "build-essential",
                "curl",
                "ca-certificates",
                "pkg-config",
                "autoconf",
                "automake",
                "libtool",
                "libssl-dev",
                "libnl-3-dev",
                "libnl-genl-3-dev",
                "libpam0g-dev",
                "libcap-ng-dev",
                "liblzo2-dev",
                "liblz4-dev",
                "libpkcs11-helper1-dev",
                "libsystemd-dev",
            ],
        ]
    elif distro == "rhel":
        pm = shutil.which("dnf") or shutil.which("yum")
        if not pm:
            raise RuntimeError("未找到 dnf/yum，无法准备 OpenVPN 编译依赖")
        commands = [[
            pm,
            "install",
            "-y",
            "gcc",
            "gcc-c++",
            "make",
            "tar",
            "gzip",
            "curl",
            "ca-certificates",
            "pkgconfig",
            "autoconf",
            "automake",
            "libtool",
            "openssl-devel",
            "libnl3-devel",
            "pam-devel",
            "libcap-ng-devel",
            "lzo-devel",
            "lz4-devel",
            "pkcs11-helper-devel",
            "systemd-devel",
            "which",
        ]]
    else:
        raise RuntimeError(f"暂不支持该发行版族的安全升级: {distro or 'unknown'}")

    for cmd in commands:
        _emit(on_output, "[准备] " + " ".join(cmd))
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        assert proc.stdout is not None
        for line in proc.stdout:
            _emit(on_output, line.rstrip())
        if proc.wait() != 0:
            raise RuntimeError(f"安装 OpenVPN 编译依赖失败: {' '.join(cmd)}")


def inspect_upgrade(on_output: _OUTPUT | None = None) -> dict:
    """检测当前 OpenVPN 与 GitHub 最新候选 release。"""

    from app.services.openvpn.installer import resolve_latest_openvpn_release

    current = detect_openvpn()
    release = resolve_latest_openvpn_release(on_output=on_output)
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
    }


def stage_upgrade_candidate(on_output: _OUTPUT | None = None) -> dict:
    """把最新 OpenVPN 编译到临时目录，不修改当前 binary/systemd。"""

    from app.services.openvpn.installer import (
        _build_install_env,
        _run_command,
    )

    info = get_distro_info()
    distro = detect_distro_family(info)
    if not distro:
        raise RuntimeError("无法识别系统发行版族，不能自动升级 OpenVPN")

    _install_build_dependencies(distro, on_output)

    version_id = str(info.get("version_id") or "")
    env = _build_install_env(
        distro,
        version_id,
        install_easyrsa=False,
        install_server_systemd=False,
        install_client_systemd=False,
        install_server_override=False,
        on_output=on_output,
    )
    stage_root = Path(tempfile.mkdtemp(prefix="nicevpn-openvpn-upgrade-"))
    prefix = stage_root / "prefix"
    src_root = stage_root / "src"
    cache = stage_root / str(env["OPENVPN_ASSET"])
    env.update({
        "OPENVPN_PREFIX": str(prefix),
        "OPENVPN_CACHE": str(cache),
        "OPENVPN_SRC_ROOT": str(src_root),
        "OPENVPN_CREATE_SYMLINK": "0",
    })

    shell = f"source {shlex_quote(str(_COMMON_SCRIPT))}; openvpn_compile_install"
    _emit(on_output, f"[Stage] 编译候选 OpenVPN {env['OPENVPN_TAG']} 到 {prefix}")
    _run_command(["bash", "-lc", shell], on_output, env={**os.environ, **env})

    binary = prefix / "sbin" / "openvpn"
    if not binary.is_file():
        raise RuntimeError(f"候选 OpenVPN 编译完成但 binary 不存在: {binary}")

    version = _openvpn_version(binary)
    asset = str(env["OPENVPN_ASSET"])
    source_name = asset[:-7] if asset.endswith(".tar.gz") else asset[:-4] if asset.endswith(".tgz") else asset
    source_dir = src_root / source_name
    if not source_dir.is_dir():
        candidates = sorted(path for path in src_root.iterdir() if path.is_dir())
        if len(candidates) != 1:
            raise RuntimeError(f"无法识别 OpenVPN 源码目录: {src_root}")
        source_dir = candidates[0]
    return {
        "stage_root": str(stage_root),
        "prefix": str(prefix),
        "source_dir": str(source_dir),
        "binary": str(binary),
        "version": version,
        "tag": str(env["OPENVPN_TAG"]),
        "asset": asset,
        "distro": distro,
        "distro_version": version_id,
    }


def shlex_quote(value: str) -> str:
    import shlex

    return shlex.quote(value)


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


def _run_foreground_preflight(
    binary: str,
    config_path: Path,
    *,
    seconds: float = _PREFLIGHT_SECONDS,
) -> tuple[bool, str, bool]:
    """运行到初始化阶段；进程能稳定存活到阈值即认为解析/初始化通过。"""

    log_path = config_path.with_suffix(config_path.suffix + ".log")
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
            rc = proc.poll()
            if rc is not None:
                break
            time.sleep(0.1)
        survived = proc.poll() is None
        if survived:
            proc.terminate()
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=2)
        rc = proc.returncode

    output = log_path.read_text(encoding="utf-8", errors="replace") if log_path.exists() else ""
    lowered = output.casefold()
    fatal = any(marker in lowered for marker in _FATAL_CONFIG_MARKERS)
    if survived and not fatal:
        return True, output, True
    if rc == 0 and not fatal:
        return True, output, False
    return False, output, False


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
        ok, output, survived = _run_foreground_preflight(binary, candidate)
        return {
            "ok": ok,
            "path": str(source),
            "survived_initialization": survived,
            "log_tail": "\n".join(output.splitlines()[-30:]),
            "error": None if ok else "候选 OpenVPN 无法完成服务端配置初始化",
        }


def _profile_has_encrypted_private_key(text: str) -> bool:
    upper = str(text or "").upper()
    return "ENCRYPTED PRIVATE KEY" in upper or "PROC-TYPE: 4,ENCRYPTED" in upper


def _find_existing_client_profile() -> Path | None:
    if not OVPN_PROFILES_DIR.exists():
        return None
    for path in sorted(OVPN_PROFILES_DIR.glob("*.ovpn")):
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        if text.strip() and not _profile_has_encrypted_private_key(text):
            return path
    return None


def _generate_ephemeral_client_profile(binary: str, root: Path) -> Path:
    """无可用现有 profile 时生成一次性客户端文件，不写入 NiceVPN PKI/SQLite。"""

    openssl = shutil.which("openssl")
    if not openssl:
        raise RuntimeError("没有现有可测试 .ovpn，且系统缺少 openssl，无法生成一次性客户端配置")

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


def preflight_client_profile(binary: str, profile_path: str | Path | None = None) -> dict:
    """验证代表性客户端 .ovpn；没有可用现有配置时生成一次性 profile。"""

    with tempfile.TemporaryDirectory(prefix="nicevpn-client-preflight-") as td:
        root = Path(td)
        source = Path(profile_path) if profile_path else _find_existing_client_profile()
        generated = source is None
        if source is None:
            source = _generate_ephemeral_client_profile(binary, root)
        if not source.is_file():
            return {"ok": False, "error": f"客户端配置不存在: {source}"}

        original = source.read_text(encoding="utf-8")
        if _profile_has_encrypted_private_key(original):
            return {
                "ok": False,
                "profile": str(source),
                "error": "客户端私钥带密码，无法在无人值守 preflight 中验证",
            }

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
        ok, output, survived = _run_foreground_preflight(binary, candidate)
        lowered = output.casefold()
        ready = survived or any(marker in lowered for marker in _CLIENT_READY_MARKERS)
        fatal = any(marker in lowered for marker in _FATAL_CONFIG_MARKERS)
        if not ok and ready and not fatal:
            ok = True

        return {
            "ok": ok,
            "profile": str(source),
            "generated": generated,
            "survived_initialization": survived,
            "log_tail": "\n".join(output.splitlines()[-30:]),
            "error": None if ok else "候选 OpenVPN 无法解析/初始化客户端配置",
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
        if not result["ok"]:
            return {
                "ok": False,
                "server_results": server_results,
                "client_result": None,
                "error": f"服务端配置不兼容: {path.name}",
            }

    _emit(on_output, "[Preflight] 验证代表性客户端 .ovpn")
    client_result = preflight_client_profile(binary)
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


def _snapshot_path(path: Path, root: Path, name: str) -> dict:
    record = {"path": str(path), "exists": path.exists() or path.is_symlink(), "kind": "missing"}
    if not record["exists"]:
        return record
    dst = root / name
    if path.is_symlink():
        record["kind"] = "symlink"
        record["target"] = os.readlink(path)
    elif path.is_dir():
        record["kind"] = "dir"
        shutil.copytree(path, dst, symlinks=True)
        record["backup"] = str(dst)
    else:
        record["kind"] = "file"
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, dst)
        record["backup"] = str(dst)
    return record


def _remove_path(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink(missing_ok=True)
    elif path.is_dir():
        shutil.rmtree(path, ignore_errors=True)


def _restore_path(record: dict) -> None:
    path = Path(str(record["path"]))
    _remove_path(path)
    kind = record.get("kind")
    if kind == "symlink":
        path.parent.mkdir(parents=True, exist_ok=True)
        path.symlink_to(str(record["target"]))
    elif kind == "dir":
        shutil.copytree(Path(str(record["backup"])), path, symlinks=True)
    elif kind == "file":
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(Path(str(record["backup"])), path)


def _write_candidate_systemd_unit(stage: dict) -> None:
    from app.services.openvpn.installer import _render_openvpn_systemd_unit_from_source

    source_dir = Path(str(stage["source_dir"]))
    unit_text = _render_openvpn_systemd_unit_from_source(source_dir, str(stage["tag"]))
    unit = Path("/etc/systemd/system/openvpn-server@.service")
    unit.parent.mkdir(parents=True, exist_ok=True)
    unit.write_text(unit_text, encoding="utf-8")
    override = Path("/etc/systemd/system/openvpn-server@.service.d/10-ovpn-mgmt.conf")
    override.parent.mkdir(parents=True, exist_ok=True)
    override.write_text("[Service]\nReadWritePaths=/etc/openvpn\n", encoding="utf-8")
    subprocess.run(["systemctl", "daemon-reload"], check=True)


def apply_staged_upgrade(stage: dict, on_output: _OUTPUT | None = None) -> dict:
    """切换候选 OpenVPN；失败时恢复 binary/systemd 并重启升级前运行实例。"""

    preflight = preflight_upgrade_candidate(stage, on_output)
    if not preflight.get("ok"):
        raise RuntimeError(str(preflight.get("error") or "候选 OpenVPN preflight 失败"))

    from app.services.backup import create_full_backup

    backup_file = create_full_backup(label=f"openvpn-upgrade-{stage.get('version')}")
    _emit(on_output, f"[备份] NiceVPN 完整备份: {backup_file}")

    active_instances = [
        name for name in iter_all_openvpn_instance_names() if is_instance_active(name)
    ]

    rollback_root = Path(tempfile.mkdtemp(prefix="nicevpn-openvpn-rollback-"))
    prefix = Path(OPENVPN_INSTALL_PREFIX)
    unit = Path("/etc/systemd/system/openvpn-server@.service")
    override_dir = Path("/etc/systemd/system/openvpn-server@.service.d")
    cli_link = Path("/usr/local/sbin/openvpn")
    snapshots = [
        _snapshot_path(unit, rollback_root, "openvpn-server.service"),
        _snapshot_path(override_dir, rollback_root, "openvpn-server.service.d"),
        _snapshot_path(cli_link, rollback_root, "openvpn-cli-link"),
    ]

    new_tree = prefix.parent / f"{prefix.name}.nicevpn-new-{uuid.uuid4().hex[:8]}"
    old_tree = prefix.parent / f"{prefix.name}.nicevpn-old-{uuid.uuid4().hex[:8]}"
    prefix_existed = prefix.exists() or prefix.is_symlink()
    prefix_moved = False
    candidate_installed = False
    try:
        _emit(on_output, f"[升级] 安装 OpenVPN {stage['version']} 到 {prefix}")
        shutil.copytree(Path(str(stage["prefix"])), new_tree, symlinks=True)
        if prefix_existed:
            os.replace(prefix, old_tree)
            prefix_moved = True
        os.replace(new_tree, prefix)
        candidate_installed = True

        cli_link.parent.mkdir(parents=True, exist_ok=True)
        _remove_path(cli_link)
        cli_link.symlink_to(prefix / "sbin" / "openvpn")
        _write_candidate_systemd_unit(stage)

        for name in active_instances:
            _emit(on_output, f"[升级] 重启实例 {name}")
            proc = _run(["systemctl", "restart", f"openvpn-server@{name}"], timeout=30)
            if proc.returncode != 0:
                raise RuntimeError(f"重启实例 {name} 失败: {(proc.stderr or proc.stdout).strip()}")

        post_stage = dict(stage)
        post_stage["binary"] = str(prefix / "sbin" / "openvpn")
        postflight = preflight_upgrade_candidate(post_stage, on_output)
        if not postflight.get("ok"):
            raise RuntimeError(str(postflight.get("error") or "升级后 preflight 失败"))

        failed_instances = [
            name for name in active_instances if not is_instance_active(name)
        ]
        if failed_instances:
            raise RuntimeError("升级后实例未恢复运行: " + ", ".join(failed_instances))

        if old_tree.exists() or old_tree.is_symlink():
            _remove_path(old_tree)
        shutil.rmtree(rollback_root, ignore_errors=True)
        return {
            "ok": True,
            "version": str(stage["version"]),
            "backup_file": str(backup_file),
            "active_instances": active_instances,
            "preflight": preflight,
            "postflight": postflight,
        }
    except Exception:
        _emit(on_output, "[回滚] OpenVPN 升级失败，恢复升级前 binary/systemd")
        _remove_path(new_tree)
        if candidate_installed:
            _remove_path(prefix)
        if prefix_moved and old_tree.exists():
            os.replace(old_tree, prefix)
        elif not prefix_existed:
            _remove_path(prefix)
        for record in snapshots:
            _restore_path(record)
        subprocess.run(["systemctl", "daemon-reload"], check=False)
        for name in active_instances:
            subprocess.run(
                ["systemctl", "restart", f"openvpn-server@{name}"],
                check=False,
            )
        raise
    finally:
        _remove_path(new_tree)
        if old_tree.exists() or old_tree.is_symlink():
            _remove_path(old_tree)
        shutil.rmtree(rollback_root, ignore_errors=True)
