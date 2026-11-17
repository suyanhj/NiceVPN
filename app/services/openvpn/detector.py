# -*- coding: utf-8 -*-
"""OpenVPN 安装检测服务"""
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Iterable

from packaging.version import Version

from app.core.constants import (
    EASYRSA_SEARCH_PATHS,
    OPENVPN_MIN_VERSION,
    OPENVPN_SEARCH_PATHS,
    OPENVPN_INSTALL_PREFIX,
    OPENVPN_RELEASES_DIR,
    OPENVPN_INSTALL_MODE_FILE,
)


def parse_os_release_text(text: str) -> dict:
    """解析 ``/etc/os-release`` 正文。

    返回键集与 ``get_distro_info`` 一致，供本机与 SSH 远端共用（与初始化向导 ``detect_distro_family`` 输入对齐）。
    """
    info: dict[str, str] = {}
    for line in (text or "").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        k = key.strip().lower()
        v = value.strip().strip('"').strip("'")
        info[k] = v
    want = ("id", "version_id", "id_like", "name", "pretty_name")
    return {key: info[key] for key in want if key in info}


def get_distro_info() -> dict:
    """获取 /etc/os-release 信息。"""
    os_release = Path("/etc/os-release")
    if not os_release.exists():
        return {}
    try:
        text = os_release.read_text(encoding="utf-8")
    except OSError:
        return {}
    return parse_os_release_text(text)


def detect_distro_family(distro_info: dict | None = None) -> str | None:
    """识别 Linux 发行版所属系统族。"""
    distro_info = distro_info or get_distro_info()
    candidates: list[str] = []

    distro_id = str(distro_info.get("id", "")).strip().lower()
    if distro_id:
        candidates.append(distro_id)

    id_like = str(distro_info.get("id_like", "")).strip().lower()
    if id_like:
        candidates.extend(token for token in id_like.split() if token)

    debian_family = {"debian", "ubuntu"}
    rhel_family = {"rhel", "centos", "rocky", "almalinux", "fedora"}

    for candidate in candidates:
        if candidate in debian_family:
            return "debian"
        if candidate in rhel_family:
            return "rhel"

    return None


def _path_is_within(path: Path, root: Path) -> bool:
    try:
        path.resolve(strict=False).relative_to(root.resolve(strict=False))
        return True
    except (OSError, ValueError):
        return False


def classify_openvpn_install_mode(path: str | None) -> str | None:
    """根据稳定入口及真实目标识别安装来源，不把软链接目标写回配置。"""
    raw = str(path or "").strip()
    if not raw:
        return None
    candidate = Path(raw)
    source_prefix = Path(OPENVPN_INSTALL_PREFIX)
    if (
        raw == str(source_prefix / "sbin" / "openvpn")
        or _path_is_within(candidate, source_prefix)
        or _path_is_within(candidate, Path(OPENVPN_RELEASES_DIR))
    ):
        return "source"
    if raw in {"/usr/sbin/openvpn", "/usr/bin/openvpn"}:
        return "package"
    return "custom"


def classify_easyrsa_install_mode(path: str | None) -> str | None:
    """识别 EasyRSA 的包管理器安装、NiceVPN 版本目录或自定义路径。"""
    raw = str(path or "").strip()
    if not raw:
        return None
    candidate = Path(raw)
    source_roots = (Path("/opt/ezrsa"), Path("/opt/easy-rsa"), Path(OPENVPN_INSTALL_PREFIX))
    if any(_path_is_within(candidate, root) for root in source_roots):
        return "source"
    if raw.startswith("/usr/share/easy-rsa/"):
        return "package"
    return "custom"


def detect_openvpn() -> dict:
    """检测 OpenVPN，并优先遵循持久化安装模式，避免误用旧源码版本。"""
    try:
        from app.core.config import load_config

        config = load_config()
    except Exception:
        config = {}
    mode = str(config.get("openvpn_install_mode") or "").strip() or None
    if mode not in {"package", "source", "custom"}:
        try:
            marker = Path(OPENVPN_INSTALL_MODE_FILE).read_text(encoding="utf-8").strip()
            mode = marker if marker in {"package", "source", "custom"} else None
        except OSError:
            mode = None

    result = {
        "installed": False,
        "path": None,
        "version": None,
        "meets_requirement": False,
        "install_mode": mode,
        "distro": get_distro_info(),
    }

    bin_path = _find_openvpn_binary({**dict(config), "openvpn_install_mode": mode})
    if not bin_path:
        return result

    result["installed"] = True
    result["path"] = bin_path
    result["install_mode"] = mode or classify_openvpn_install_mode(bin_path)

    version_str = _extract_version(bin_path)
    if version_str:
        result["version"] = version_str
        try:
            result["meets_requirement"] = (
                Version(version_str) >= Version(OPENVPN_MIN_VERSION)
            )
        except Exception:
            result["meets_requirement"] = False

    return result


def find_easyrsa(openvpn_bin: str | None = None) -> str | None:
    """按标准路径搜索 EasyRSA，保留 current 软链接入口而非解析到版本目录。"""
    search_paths = list(EASYRSA_SEARCH_PATHS)

    if openvpn_bin:
        # 不 resolve：源码安装的 /opt/openvpn 本身也是稳定软链接入口。
        prefix = Path(openvpn_bin).expanduser().parent.parent
        extra_paths = [
            prefix / "share" / "easy-rsa" / "easyrsa",
            prefix / "share" / "easy-rsa" / "3" / "easyrsa",
        ]
        for extra in reversed(extra_paths):
            extra_str = str(extra)
            if extra_str not in search_paths:
                search_paths.insert(0, extra_str)

    return _find_installed_executable(search_paths, "easyrsa")


def validate_custom_path(path: str) -> dict:
    """验证自定义 OpenVPN 路径是否有效。"""
    result: dict = {"valid": False, "version": None, "error": None}

    target = Path(path)
    if not target.exists():
        result["error"] = f"路径不存在: {path}"
        return result

    if not target.is_file():
        result["error"] = f"路径不是文件: {path}"
        return result

    version_str = _extract_version(str(target))
    if not version_str:
        result["error"] = f"无法从 {path} 获取 OpenVPN 版本信息"
        return result

    result["valid"] = True
    result["version"] = version_str
    return result


def _find_installed_executable(
    search_paths: Iterable[str],
    command: str,
    *,
    allow_path: bool = True,
) -> str | None:
    """优先复用预设路径，最后由标准库从 PATH 查找，避免调用 which/bash。"""
    for candidate in search_paths:
        path = Path(candidate)
        if path.is_file() and os.access(path, os.X_OK):
            return str(path)

    if not allow_path:
        return None

    found = shutil.which(command)
    if found:
        path = Path(found)
        if path.is_file() and os.access(path, os.X_OK):
            return str(path)
    return None


def _find_openvpn_binary(config: dict | None = None) -> str | None:
    """按持久化安装模式查找 OpenVPN，始终返回稳定入口而非 resolve() 结果。"""
    config = config or {}
    mode = str(config.get("openvpn_install_mode") or "").strip()
    configured = str(config.get("openvpn_bin") or "").strip()

    if mode == "custom":
        candidates = [configured] if configured else []
    elif mode == "package":
        candidates = [
            configured,
            "/usr/sbin/openvpn",
            "/usr/bin/openvpn",
            "/usr/local/sbin/openvpn",
            "/usr/local/bin/openvpn",
        ]
        candidates = [
            item for item in candidates
            if item and classify_openvpn_install_mode(item) == "package"
        ]
    elif mode == "source":
        candidates = [
            f"{OPENVPN_INSTALL_PREFIX}/sbin/openvpn",
            f"{OPENVPN_INSTALL_PREFIX}/bin/openvpn",
            configured,
        ]
        candidates = [
            item for item in candidates
            if item and classify_openvpn_install_mode(item) == "source"
        ]
    else:
        candidates = list(OPENVPN_SEARCH_PATHS)
        if configured:
            if classify_openvpn_install_mode(configured) == "source":
                # 旧配置可能保存过 current 的真实 release 路径，稳定入口优先。
                candidates.append(configured)
            else:
                candidates.insert(0, configured)

    found = _find_installed_executable(
        candidates, "openvpn", allow_path=mode not in {"custom", "source", "package"}
    )
    if found:
        return found

    # 自定义路径丢失时不能静默切换到另一套 OpenVPN。
    if mode in {"custom", "source"}:
        return None
    if mode == "package":
        found = shutil.which("openvpn")
        if found and classify_openvpn_install_mode(found) == "package":
            path = Path(found)
            if path.is_file() and os.access(path, os.X_OK):
                return str(path)
        return None
    return None

def _extract_version(bin_path: str) -> str | None:
    """执行 openvpn --version 并提取版本号。"""
    try:
        proc = subprocess.run(
            [bin_path, "--version"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError, IndexError):
        return None

    output = proc.stdout or proc.stderr or ""
    first_line = output.splitlines()[0] if output.strip() else ""
    match = re.search(r"(\d+\.\d+\.\d+)", first_line)
    return match.group(1) if match else None
