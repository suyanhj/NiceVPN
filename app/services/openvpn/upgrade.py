# -*- coding: utf-8 -*-
"""OpenVPN 本机安全升级：候选版本 staging、服务端/客户端 preflight 与原子切换。"""

from __future__ import annotations

import hashlib
import json
import logging
import os
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
from app.core.constants import OPENVPN_INSTALL_PREFIX, OPENVPN_RELEASES_DIR
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
from app.services.openvpn.install_lock import openvpn_install_lock
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


def _release_binary(release_dir: Path) -> Path:
    """返回源码 release 中的 OpenVPN binary。"""
    for candidate in (release_dir / "sbin" / "openvpn", release_dir / "bin" / "openvpn"):
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate
    raise FileNotFoundError(f"OpenVPN release 缺少可执行 binary: {release_dir}")


def _release_id(version: str, binary: str | Path) -> str:
    """版本号 + binary SHA-256 前缀，避免同版本不同构建相互覆盖。"""
    digest = hashlib.sha256()
    with Path(binary).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return f"{version}-{digest.hexdigest()[:12]}"


def _atomic_symlink(target: Path, link_path: Path) -> None:
    """在同一目录创建临时软链接并原子替换稳定入口。"""
    link_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = link_path.parent / f".{link_path.name}.link-{uuid.uuid4().hex[:12]}"
    try:
        temporary.symlink_to(str(target))
        os.replace(temporary, link_path)
    finally:
        if temporary.is_symlink() or temporary.exists():
            temporary.unlink(missing_ok=True)


def _migrate_legacy_prefix(prefix: Path, releases_dir: Path) -> Path | None:
    """把历史普通 prefix 迁入 release 仓库；返回切换前的版本目录。"""
    if prefix.is_symlink():
        target = prefix.resolve(strict=True)
        if not target.is_dir():
            raise RuntimeError(f"OpenVPN 稳定入口没有指向目录: {prefix} -> {target}")
        return target
    if not prefix.exists():
        return None
    if not prefix.is_dir():
        raise RuntimeError(f"OpenVPN 安装入口不是目录或软链接，拒绝迁移: {prefix}")

    binary = _release_binary(prefix)
    version = _openvpn_version(binary)
    release_dir = releases_dir / f"legacy-{_release_id(version, binary)}"
    releases_dir.mkdir(parents=True, exist_ok=True)
    if release_dir.exists() or release_dir.is_symlink():
        raise RuntimeError(f"旧 OpenVPN 迁移目标已存在，拒绝覆盖: {release_dir}")

    os.replace(prefix, release_dir)
    try:
        _atomic_symlink(release_dir, prefix)
    except Exception:
        if not prefix.exists() and not prefix.is_symlink():
            os.replace(release_dir, prefix)
        raise
    logger.info("Migrated legacy OpenVPN prefix %s -> %s", prefix, release_dir)
    return release_dir


def _recover_interrupted_prefix_switch(prefix: Path) -> None:
    """恢复旧版目录备份；新版切换只更新软链接，release 本身不再移动。"""
    parent = prefix.parent
    if not parent.is_dir():
        return

    if not prefix.exists() and not prefix.is_symlink():
        candidates: list[tuple[int, Path, Path | None]] = []
        for candidate in parent.glob(f"{prefix.name}.nicevpn-old-*"):
            if candidate.is_dir() or candidate.is_symlink():
                try:
                    candidates.append((candidate.lstat().st_ctime_ns, candidate, None))
                except OSError:
                    continue
        for backup_root in parent.glob(f".{prefix.name}.backup.*"):
            candidate = backup_root / prefix.name
            if candidate.is_dir() and not candidate.is_symlink():
                try:
                    candidates.append((candidate.lstat().st_ctime_ns, candidate, backup_root))
                except OSError:
                    continue
        if candidates:
            _, restore_path, backup_root = max(candidates, key=lambda item: item[0])
            os.replace(restore_path, prefix)
            if backup_root is not None:
                try:
                    backup_root.rmdir()
                except OSError:
                    pass
            logger.warning("Recovered legacy OpenVPN prefix backup: %s -> %s", restore_path, prefix)

    # 仅源码安装允许从 release 仓库恢复缺失入口，绝不覆盖系统包安装。
    if not prefix.exists() and not prefix.is_symlink():
        try:
            current = detect_openvpn()
            source_mode = current.get("install_mode") == "source"
        except Exception:
            source_mode = False
        releases_dir = Path(OPENVPN_RELEASES_DIR)
        if source_mode and releases_dir.is_dir():
            candidates: list[tuple[int, Path]] = []
            for candidate in releases_dir.iterdir():
                if not candidate.is_dir() or candidate.name.startswith("."):
                    continue
                if (candidate / ".nicevpn-failed").is_file():
                    continue
                try:
                    _release_binary(candidate)
                    candidates.append((candidate.stat().st_mtime_ns, candidate))
                except (OSError, FileNotFoundError):
                    continue
            if candidates:
                _, latest = max(candidates, key=lambda item: item[0])
                _atomic_symlink(latest, prefix)
                logger.warning("Recovered missing OpenVPN stable link: %s -> %s", prefix, latest)

    stale_candidates = list(parent.glob(f"{prefix.name}.nicevpn-new-*"))
    stale_candidates.extend(parent.glob(f".{prefix.name}.stage.*"))
    stale_candidates.extend(Path(OPENVPN_RELEASES_DIR).glob(".stage.*"))
    for candidate in stale_candidates:
        try:
            _remove_path_strict(candidate)
        except Exception:
            logger.warning("Unable to clean stale OpenVPN candidate tree %s", candidate, exc_info=True)


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


def _remove_path_strict(path: Path) -> None:
    """严格删除路径；升级回滚不能吞掉旧 binary/systemd 的清理错误。"""
    if path.is_symlink() or path.is_file():
        path.unlink(missing_ok=True)
    elif path.is_dir():
        shutil.rmtree(path)


def _restore_path(record: dict) -> None:
    """从快照恢复系统配置：先在目标同目录准备新内容，再原子切换。"""
    path = Path(str(record["path"]))
    kind = record.get("kind")
    if kind == "missing":
        _remove_path_strict(path)
        return

    path.parent.mkdir(parents=True, exist_ok=True)
    if kind in {"symlink", "file"}:
        candidate = path.with_name(f".{path.name}.nicevpn-restore-{uuid.uuid4().hex}")
        try:
            if kind == "symlink":
                candidate.symlink_to(str(record["target"]))
            else:
                shutil.copy2(Path(str(record["backup"])), candidate)
            if path.is_dir() and not path.is_symlink():
                raise RuntimeError(f"回滚拒绝用文件覆盖目录: {path}")
            os.replace(candidate, path)
        finally:
            _remove_path_strict(candidate)
        return

    if kind != "dir":
        raise ValueError(f"未知的 OpenVPN 系统路径快照类型: {kind!r}")

    # staging 与目标目录同盘，先准备完整副本再切换，避免先删除造成空窗。
    temp_root = Path(
        tempfile.mkdtemp(prefix=f".{path.name}.nicevpn-restore-", dir=path.parent)
    )
    candidate = temp_root / "candidate"
    previous = temp_root / "previous"
    preserve_previous = False
    try:
        shutil.copytree(Path(str(record["backup"])), candidate, symlinks=True)
        if path.exists() or path.is_symlink():
            os.replace(path, previous)
        try:
            os.replace(candidate, path)
        except Exception:
            if previous.exists() or previous.is_symlink():
                try:
                    os.replace(previous, path)
                except Exception:
                    preserve_previous = True
                    logger.critical(
                        "OpenVPN systemd 目录恢复失败，保留前一版本目录: %s",
                        previous,
                        exc_info=True,
                    )
            raise
    finally:
        if not preserve_previous:
            try:
                shutil.rmtree(temp_root)
            except OSError:
                logger.warning(
                    "清理 OpenVPN systemd 恢复临时目录失败: %s",
                    temp_root,
                    exc_info=True,
                )


def _upgrade_transaction_root() -> Path:
    """持久化升级事务目录；不能放在 /tmp，必须跨进程退出和重启保留。"""
    return Path(OPENVPN_RELEASES_DIR) / ".nicevpn-upgrade-transaction"


def _write_upgrade_manifest(root: Path, manifest: dict) -> None:
    """原子写入事务状态；fsync 文件与目录，确保切换意图先于系统变更落盘。"""
    temporary = root / f".manifest-{uuid.uuid4().hex}.tmp"
    try:
        with temporary.open("x", encoding="utf-8") as stream:
            json.dump(manifest, stream, ensure_ascii=False, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, root / "manifest.json")
        directory_fd = os.open(root, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        temporary.unlink(missing_ok=True)


def _legacy_release_target(prefix: Path, releases_dir: Path) -> Path | None:
    """在移动旧普通目录之前计算确定的 legacy release 目标。"""
    if prefix.is_symlink():
        target = prefix.resolve(strict=True)
        if not target.is_dir():
            raise RuntimeError(f"OpenVPN 稳定入口没有指向目录: {prefix} -> {target}")
        return target
    if not prefix.exists():
        return None
    if not prefix.is_dir():
        raise RuntimeError(f"OpenVPN 安装入口不是目录或软链接，拒绝迁移: {prefix}")
    binary = _release_binary(prefix)
    version = _openvpn_version(binary)
    return releases_dir / f"legacy-{_release_id(version, binary)}"


def _validate_upgrade_manifest(root: Path, manifest: dict) -> tuple[Path, Path, Path | None, Path | None]:
    """只允许恢复本升级流程管理的固定路径，避免损坏的 manifest 触碰任意系统文件。"""
    prefix = Path(OPENVPN_INSTALL_PREFIX)
    releases_dir = Path(OPENVPN_RELEASES_DIR)
    if manifest.get("schema_version") != 1:
        raise RuntimeError("OpenVPN 升级事务 manifest 版本不受支持")
    if Path(str(manifest.get("prefix") or "")) != prefix:
        raise RuntimeError("OpenVPN 升级事务 prefix 与当前安装路径不一致")
    if Path(str(manifest.get("releases_dir") or "")) != releases_dir:
        raise RuntimeError("OpenVPN 升级事务 release 目录与当前配置不一致")

    def release_child(value: object, label: str) -> Path | None:
        if value in (None, ""):
            return None
        candidate = Path(str(value))
        if candidate.parent != releases_dir:
            raise RuntimeError(f"OpenVPN 升级事务 {label} 不在 release 目录下: {candidate}")
        return candidate

    old_target = release_child(manifest.get("old_target"), "old_target")
    new_release = release_child(manifest.get("new_release"), "new_release")
    stage = release_child(manifest.get("release_stage"), "release_stage")
    allowed_paths = {
        "/etc/systemd/system/openvpn-server@.service",
        "/etc/systemd/system/openvpn-server@.service.d",
        "/usr/local/sbin/openvpn",
    }
    snapshots = manifest.get("snapshots")
    if not isinstance(snapshots, list):
        raise RuntimeError("OpenVPN 升级事务 snapshots 格式错误")
    for record in snapshots:
        if not isinstance(record, dict) or record.get("path") not in allowed_paths:
            raise RuntimeError("OpenVPN 升级事务包含未授权的系统路径快照")
        backup = record.get("backup")
        if backup:
            backup_path = Path(str(backup))
            if root not in backup_path.parents:
                raise RuntimeError("OpenVPN 升级事务快照文件不在事务目录内")
    instances = manifest.get("active_instances", [])
    if not isinstance(instances, list) or any(
        not isinstance(name, str) or not name or "/" in name or "\\x00" in name
        for name in instances
    ):
        raise RuntimeError("OpenVPN 升级事务 active_instances 格式错误")
    return prefix, releases_dir, old_target, new_release


def _recover_interrupted_openvpn_upgrade_locked(
    on_output: _OUTPUT | None = None,
) -> bool:
    """恢复被 SIGKILL、断电或进程重启中断的 OpenVPN 升级。调用方必须持有安装锁。"""
    root = _upgrade_transaction_root()
    if not root.exists() and not root.is_symlink():
        return False
    if root.is_symlink() or not root.is_dir():
        raise RuntimeError(f"OpenVPN 升级事务目录不安全: {root}")

    manifest_path = root / "manifest.json"
    if not manifest_path.is_file():
        # manifest 原子发布前尚未修改 prefix、systemd 或 CLI；只留下了未完成快照。
        shutil.rmtree(root)
        return True

    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"OpenVPN 升级事务 manifest 无法读取，保留恢复材料: {root}") from exc
    if not isinstance(manifest, dict):
        raise RuntimeError(f"OpenVPN 升级事务 manifest 格式错误，保留恢复材料: {root}")

    prefix, releases_dir, old_target, new_release = _validate_upgrade_manifest(root, manifest)
    state = str(manifest.get("state") or "")
    if state == "committed":
        stage = manifest.get("release_stage")
        if stage:
            _remove_path_strict(Path(str(stage)))
        shutil.rmtree(root)
        _emit(on_output, "[恢复] 清理已提交的 OpenVPN 升级事务")
        return True
    if state != "switching":
        raise RuntimeError(f"未知 OpenVPN 升级事务状态: {state!r}，恢复材料保留在 {root}")

    errors: list[str] = []

    def record(step: str, exc: Exception) -> None:
        errors.append(f"{step}: {exc}")
        logger.critical("OpenVPN interrupted-upgrade recovery failed step=%s", step, exc_info=True)

    _emit(on_output, "[恢复] 检测到未完成的 OpenVPN 升级，恢复旧版本和 systemd 配置")
    prefix_was_present = bool(manifest.get("prefix_was_present"))
    try:
        if old_target is not None:
            if old_target.is_dir():
                if prefix.is_symlink():
                    current_target = prefix.resolve(strict=False)
                    if current_target != old_target.resolve():
                        _atomic_symlink(old_target, prefix)
                elif not prefix.exists():
                    _atomic_symlink(old_target, prefix)
                elif prefix.is_dir():
                    # 普通目录仍在说明旧目录迁移尚未发生；绝不覆盖它。
                    pass
                else:
                    raise RuntimeError(f"无法安全恢复旧 OpenVPN 入口: {prefix}")
            elif prefix.is_dir() and not prefix.is_symlink():
                # 在迁移动作发生前被中断，原目录仍完整。
                pass
            else:
                raise RuntimeError(f"旧 OpenVPN release 不存在，无法恢复: {old_target}")
        elif not prefix_was_present:
            if prefix.is_symlink():
                if new_release is not None and prefix.resolve(strict=False) == new_release:
                    prefix.unlink()
                else:
                    raise RuntimeError(f"首次安装回滚发现入口指向未知目标: {prefix}")
            elif prefix.exists():
                raise RuntimeError(f"首次安装回滚拒绝删除非软链接路径: {prefix}")
        else:
            raise RuntimeError("事务缺少原 OpenVPN 版本目标，拒绝猜测回滚")
    except Exception as exc:
        record("恢复 OpenVPN 稳定入口", exc)

    for snapshot in manifest.get("snapshots", []):
        try:
            _restore_path(snapshot)
        except Exception as exc:
            record(f"恢复系统路径 {snapshot.get('path')}", exc)

    try:
        proc = _run(["systemctl", "daemon-reload"], timeout=30)
        if proc.returncode != 0:
            raise RuntimeError((proc.stderr or proc.stdout or "daemon-reload 失败").strip())
    except Exception as exc:
        record("systemd daemon-reload", exc)

    for name in manifest.get("active_instances", []):
        try:
            proc = _run(["systemctl", "restart", f"openvpn-server@{name}"], timeout=30)
            if proc.returncode != 0:
                raise RuntimeError((proc.stderr or proc.stdout or "restart 失败").strip())
            if not is_instance_active(name):
                raise RuntimeError("restart 后实例仍未处于 active 状态")
        except Exception as exc:
            record(f"恢复原实例 {name}", exc)

    if new_release is not None and new_release.is_dir() and new_release != old_target:
        try:
            (new_release / ".nicevpn-failed").write_text(
                "OpenVPN upgrade was interrupted before commit\\n",
                encoding="utf-8",
            )
        except OSError as exc:
            record("标记未完成 release", exc)

    stage = manifest.get("release_stage")
    if stage:
        try:
            _remove_path_strict(Path(str(stage)))
        except Exception as exc:
            record("清理候选 release staging", exc)

    if errors:
        raise RuntimeError(
            "OpenVPN 升级中断恢复不完整，恢复材料已保留在 "
            f"{root}: " + "；".join(errors)
        )

    shutil.rmtree(root)
    _emit(on_output, "[恢复] 已恢复旧 OpenVPN 版本、systemd 配置及原运行实例")
    return True


def recover_interrupted_openvpn_upgrade(
    on_output: _OUTPUT | None = None,
) -> bool:
    """应用启动时调用；存在事务时持有跨进程安装锁完成恢复。"""
    root = _upgrade_transaction_root()
    if not root.exists() and not root.is_symlink():
        return False
    with openvpn_install_lock():
        return _recover_interrupted_openvpn_upgrade_locked(on_output)


def apply_staged_upgrade(stage: dict, on_output: _OUTPUT | None = None) -> dict:
    """串行执行 OpenVPN 切换，并与 Shell 安装入口共享跨进程锁。"""
    if not _OPENVPN_UPGRADE_APPLY_LOCK.acquire(blocking=False):
        raise RuntimeError("OpenVPN 升级正在执行，请勿重复发起")
    try:
        with openvpn_install_lock():
            return _apply_staged_upgrade_locked(stage, on_output)
    finally:
        _OPENVPN_UPGRADE_APPLY_LOCK.release()


def _apply_staged_upgrade_locked(stage: dict, on_output: _OUTPUT | None = None) -> dict:
    """通过持久化事务切换稳定入口；旧 release 保留，进程中断后可在启动时恢复。"""

    current = detect_openvpn()
    if current.get("install_mode") != "source":
        raise RuntimeError(
            "当前 OpenVPN 不是 NiceVPN 管理的源码安装，拒绝执行源码 release 切换。"
        )

    # 先恢复上次异常退出留下的事务，再兼容恢复更早版本的旧式备份目录。
    _recover_interrupted_openvpn_upgrade_locked(on_output)
    prefix = Path(OPENVPN_INSTALL_PREFIX)
    releases_dir = Path(OPENVPN_RELEASES_DIR)
    _recover_interrupted_prefix_switch(prefix)

    preflight = preflight_upgrade_candidate(stage, on_output)
    if not preflight.get("ok"):
        raise RuntimeError(str(preflight.get("error") or "候选 OpenVPN preflight 失败"))

    stage_binary = Path(str(stage.get("binary") or ""))
    if not stage_binary.is_file():
        raise RuntimeError(f"候选 OpenVPN binary 不存在: {stage_binary}")
    version = str(stage.get("version") or _openvpn_version(stage_binary))
    release_id = _release_id(version, stage_binary)
    release_dir = releases_dir / release_id
    release_stage = releases_dir / f".{release_id}.stage-{uuid.uuid4().hex[:12]}"

    from app.services.backup import create_full_backup

    backup_file = create_full_backup(label=f"openvpn-upgrade-{version}")
    _emit(on_output, f"[备份] NiceVPN 完整备份: {backup_file}")

    active_instances = [
        name for name in iter_all_openvpn_instance_names() if is_instance_active(name)
    ]
    old_target = _legacy_release_target(prefix, releases_dir)
    prefix_was_present = prefix.exists() or prefix.is_symlink()

    unit = Path("/etc/systemd/system/openvpn-server@.service")
    override_dir = Path("/etc/systemd/system/openvpn-server@.service.d")
    cli_link = Path("/usr/local/sbin/openvpn")
    txn_root = _upgrade_transaction_root()
    releases_dir.mkdir(parents=True, exist_ok=True)
    if txn_root.exists() or txn_root.is_symlink():
        raise RuntimeError(f"OpenVPN 升级事务目录仍存在，拒绝覆盖: {txn_root}")
    txn_root.mkdir(mode=0o700, parents=True, exist_ok=False)

    manifest_written = False
    try:
        snapshots = [
            _snapshot_path(unit, txn_root, "openvpn-server.service"),
            _snapshot_path(override_dir, txn_root, "openvpn-server.service.d"),
            _snapshot_path(cli_link, txn_root, "openvpn-cli-link"),
        ]
        manifest = {
            "schema_version": 1,
            "state": "switching",
            "prefix": str(prefix),
            "releases_dir": str(releases_dir),
            "prefix_was_present": prefix_was_present,
            "old_target": str(old_target) if old_target else None,
            "new_release": str(release_dir),
            "release_stage": str(release_stage),
            "version": version,
            "backup_file": str(backup_file),
            "active_instances": active_instances,
            "snapshots": snapshots,
        }
        # 必须先持久化回滚目标和系统配置快照，再迁移旧目录或切换软链接。
        _write_upgrade_manifest(txn_root, manifest)
        manifest_written = True

        migrated_target = _migrate_legacy_prefix(prefix, releases_dir)
        if migrated_target != old_target:
            raise RuntimeError(
                f"OpenVPN 旧版本迁移目标发生变化: expected={old_target}, actual={migrated_target}"
            )

        if release_dir.exists() or release_dir.is_symlink():
            if not release_dir.is_dir() or release_dir.is_symlink():
                raise RuntimeError(f"OpenVPN release 目标不是普通目录: {release_dir}")
            existing_binary = _release_binary(release_dir)
            if (
                _openvpn_version(existing_binary) != version
                or _release_id(version, existing_binary) != release_id
            ):
                raise RuntimeError(f"已有 OpenVPN release 校验不匹配，拒绝覆盖: {release_dir}")
            (release_dir / ".nicevpn-failed").unlink(missing_ok=True)
            _emit(on_output, f"[复用] 已校验候选版本目录: {release_dir}")
        else:
            _emit(on_output, f"[发布] 将 OpenVPN {version} 安装到独立 release: {release_dir}")
            shutil.copytree(Path(str(stage["prefix"])), release_stage, symlinks=True)
            staged_binary = _release_binary(release_stage)
            if _openvpn_version(staged_binary) != version:
                raise RuntimeError("候选 release 发布前版本校验失败")
            if _release_id(version, staged_binary) != release_id:
                raise RuntimeError("候选 release binary SHA-256 校验失败")
            os.replace(release_stage, release_dir)
            manifest["release_stage"] = None
            _write_upgrade_manifest(txn_root, manifest)

        _emit(on_output, f"[升级] 原子切换稳定入口 {prefix} -> {release_dir}")
        _atomic_symlink(release_dir, prefix)

        cli_link.parent.mkdir(parents=True, exist_ok=True)
        _remove_path_strict(cli_link)
        cli_link.symlink_to(prefix / "sbin" / "openvpn")

        from app.services.openvpn.installer import (
            install_openvpn_server_systemd_from_staged_source,
        )

        install_openvpn_server_systemd_from_staged_source(
            source_dir=Path(str(stage["source_dir"])),
            tag=str(stage["tag"]),
            openvpn_bin=str(prefix / "sbin" / "openvpn"),
            on_output=_mirror_installer_output(on_output),
        )

        for name in active_instances:
            _emit(on_output, f"[升级] 重启实例 {name}")
            proc = _run(["systemctl", "restart", f"openvpn-server@{name}"], timeout=30)
            if proc.returncode != 0:
                raise RuntimeError(
                    f"重启实例 {name} 失败: {(proc.stderr or proc.stdout).strip()}"
                )

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

        manifest["state"] = "committed"
        manifest["release_stage"] = None
        _write_upgrade_manifest(txn_root, manifest)
        try:
            shutil.rmtree(txn_root)
        except OSError:
            # committed manifest 表示新版本已验收；下次启动只清理事务目录，不回滚。
            logger.warning("OpenVPN 升级成功，但事务目录清理失败: %s", txn_root, exc_info=True)

        _emit(on_output, f"[完成] OpenVPN {version} 升级成功，稳定入口指向 {release_dir}")
        return {
            "ok": True,
            "version": version,
            "release_dir": str(release_dir),
            "previous_release": str(old_target) if old_target else None,
            "backup_file": str(backup_file),
            "active_instances": active_instances,
            "preflight": preflight,
            "postflight": postflight,
        }
    except Exception as upgrade_exc:
        if manifest_written:
            try:
                _recover_interrupted_openvpn_upgrade_locked(on_output)
            except Exception as rollback_exc:
                logger.critical(
                    "OpenVPN 升级失败且持久化回滚未完成；事务保留在 %s",
                    txn_root,
                    exc_info=True,
                )
                raise RuntimeError(
                    f"OpenVPN 升级失败: {upgrade_exc}；自动回滚不完整: {rollback_exc}；"
                    f"恢复材料: {txn_root}"
                ) from upgrade_exc
        else:
            shutil.rmtree(txn_root, ignore_errors=True)
        raise
