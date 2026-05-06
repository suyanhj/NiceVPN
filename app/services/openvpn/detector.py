# -*- coding: utf-8 -*-
"""OpenVPN 安装检测服务"""
import re
import subprocess
from pathlib import Path

from packaging.version import Version

from app.core.constants import (
    EASYRSA_SEARCH_PATHS,
    OPENVPN_MIN_VERSION,
    OPENVPN_SEARCH_PATHS,
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


def detect_openvpn() -> dict:
    """检测系统中的 OpenVPN 安装状态。"""
    result = {
        "installed": False,
        "path": None,
        "version": None,
        "meets_requirement": False,
        "distro": get_distro_info(),
    }

    bin_path = _find_openvpn_binary()
    if not bin_path:
        return result

    result["installed"] = True
    result["path"] = bin_path

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
    """按标准路径列表搜索 easyrsa 脚本。"""
    search_paths = list(EASYRSA_SEARCH_PATHS)

    if openvpn_bin:
        prefix = Path(openvpn_bin).resolve().parent.parent
        extra_paths = [
            prefix / "share" / "easy-rsa" / "easyrsa",
            prefix / "share" / "easy-rsa" / "3" / "easyrsa",
        ]
        for extra in reversed(extra_paths):
            extra_str = str(extra)
            if extra_str not in search_paths:
                search_paths.insert(0, extra_str)

    for candidate in search_paths:
        path = Path(candidate)
        if path.is_file():
            return str(path.resolve())

    try:
        proc = subprocess.run(
            ["which", "easyrsa"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if proc.returncode == 0:
            found = proc.stdout.strip()
            if found and Path(found).is_file():
                return str(Path(found).resolve())
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        pass

    return None


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


def _find_openvpn_binary() -> str | None:
    """搜索 OpenVPN 可执行文件。"""
    for candidate in OPENVPN_SEARCH_PATHS:
        path = Path(candidate)
        if path.is_file():
            return str(path.resolve())

    for cmd in (["which", "openvpn"], ["bash", "-lc", "command -v openvpn"]):
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=5,
            )
            if proc.returncode == 0:
                path = proc.stdout.strip()
                if path:
                    return str(Path(path).resolve())
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            continue

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
