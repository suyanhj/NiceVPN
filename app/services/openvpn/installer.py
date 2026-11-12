# -*- coding: utf-8 -*-
"""OpenVPN 自动安装服务 — 本机与对端 SSH 共用 ``app/scripts/install/`` 下 shell 脚本。"""
import json
import logging
import os
import shlex
import subprocess
import urllib.request
from pathlib import Path
from typing import Callable

from app.core.config import load_config
from app.core.constants import EASYRSA_INSTALL_DIR, OPENVPN_MIN_VERSION

log = logging.getLogger(__name__)

_INSTALL_SCRIPTS_DIR = Path(__file__).resolve().parent.parent.parent / "scripts" / "install"

_OPENVPN_SOURCE_ROOT = Path("/tmp/ovpn-mgmt-openvpn-src")

_APT_KEY_URL = "https://swupdate.openvpn.net/repos/repo-public.gpg"
_GITHUB_API_LATEST = "https://api.github.com/repos/{repo}/releases/latest"

_OPENVPN_FALLBACK_TAG = "v2.7.0"
_OPENVPN_FALLBACK_ASSET = "openvpn-2.7.0.tar.gz"
_EASYRSA_FALLBACK_TAG = "v3.2.4"
_EASYRSA_FALLBACK_ASSET = "EasyRSA-3.2.4.tgz"


def resolve_latest_openvpn_release(
    on_output: Callable[[str], None] | None = None,
) -> dict:
    """返回当前安装器会使用的 OpenVPN 最新 release。"""

    return _resolve_latest_release_asset(
        repo="OpenVPN/openvpn",
        asset_prefix="openvpn-",
        asset_suffixes=(".tar.gz", ".tgz"),
        fallback_tag=_OPENVPN_FALLBACK_TAG,
        fallback_asset=_OPENVPN_FALLBACK_ASSET,
        on_output=on_output,
    )


def build_peer_openvpn_install_script(distro: str, version_id: str) -> str:
    """生成在 **对端** 以 root/sudo 执行的 bash 脚本（内联 install/*.sh 内容）。

    与 ``install_openvpn`` 同源：Debian/RHEL 安装逻辑均来自 ``app/scripts/install/``。

    Args:
        distro: ``detect_distro_family`` 返回值 ``debian`` / ``rhel``
        version_id: 远端 ``VERSION_ID``（如 ``22.04``、``12``）

    Raises:
        ValueError: 不支持的族或 Debian 版本映射缺失
    """
    env = _build_install_env(
        distro,
        version_id,
        install_easyrsa=False,
        install_server_systemd=False,
        install_client_systemd=True,
        install_server_override=False,
        on_output=None,
    )
    return _render_remote_install_script(distro, env)


def stage_openvpn_source_build(
    distro: str,
    version_id: str,
    *,
    prefix: Path,
    cache: Path,
    src_root: Path,
    on_output: Callable[[str], None] | None = None,
) -> dict[str, str]:
    """使用正式发行版安装脚本把候选 OpenVPN 源码构建到隔离目录。

    staging 强制走源码构建，禁止修改系统 OpenVPN symlink/systemd/EasyRSA。
    Debian/RHEL 的依赖安装逻辑全部由对应 shell 入口维护。
    """

    distro = distro.lower().strip()
    env = _build_install_env(
        distro,
        version_id,
        install_easyrsa=False,
        install_server_systemd=False,
        install_client_systemd=False,
        install_server_override=False,
        on_output=on_output,
    )
    env.update(
        {
            "OPENVPN_PREFIX": str(prefix),
            "OPENVPN_CACHE": str(cache),
            "OPENVPN_SRC_ROOT": str(src_root),
            "OPENVPN_CREATE_SYMLINK": "0",
            "OPENVPN_FORCE_SOURCE": "1",
        }
    )
    script_path = _install_script_path(distro)
    _emit(
        on_output,
        f"[信息] staging 候选 OpenVPN {env['OPENVPN_TAG']} "
        f"（{script_path.name} -> {prefix}）",
    )
    _run_command(
        ["bash", str(script_path)],
        on_output,
        env=_subprocess_env(env),
    )

    binary = prefix / "sbin" / "openvpn"
    if not binary.is_file():
        raise RuntimeError(f"候选 OpenVPN 构建完成但 binary 不存在: {binary}")

    asset = str(env["OPENVPN_ASSET"])
    source_name = (
        asset[:-7]
        if asset.endswith(".tar.gz")
        else asset[:-4]
        if asset.endswith(".tgz")
        else asset
    )
    source_dir = src_root / source_name
    if not source_dir.is_dir():
        candidates = sorted(path for path in src_root.iterdir() if path.is_dir())
        if len(candidates) != 1:
            raise RuntimeError(f"无法识别 OpenVPN 源码目录: {src_root}")
        source_dir = candidates[0]

    return {
        "prefix": str(prefix),
        "source_dir": str(source_dir),
        "binary": str(binary),
        "tag": str(env["OPENVPN_TAG"]),
        "asset": asset,
        "distro": distro,
        "distro_version": str(version_id or ""),
    }


def install_openvpn_server_systemd_from_staged_source(
    *,
    source_dir: Path,
    tag: str,
    openvpn_bin: str | Path | None = None,
    on_output: Callable[[str], None] | None = None,
) -> None:
    """复用 OpenVPN 源码安装时的既有逻辑生成 server systemd unit/override。"""

    script_path = _INSTALL_SCRIPTS_DIR / "openvpn-install-systemd.sh"
    if not script_path.is_file():
        raise FileNotFoundError(f"OpenVPN systemd 安装脚本不存在: {script_path}")

    env_values = {
        "OPENVPN_SRC_DIR": str(source_dir),
        "OPENVPN_TAG": str(tag),
    }
    if openvpn_bin:
        binary = Path(openvpn_bin).expanduser().resolve()
        env_values["OPENVPN_BIN"] = str(binary)
        env_values["OPENVPN_SBIN_DIR"] = str(binary.parent)

    _run_command(
        ["bash", str(script_path)],
        on_output,
        env=_subprocess_env(env_values),
    )


def _find_openvpn_source_dir(version: str | None = None) -> Path | None:
    """查找 NiceVPN 编译流程保留的 OpenVPN 源码目录。"""

    source_root = Path(os.environ.get("OPENVPN_SRC_ROOT", str(_OPENVPN_SOURCE_ROOT)))
    template_name = Path("distro/systemd/openvpn-server@.service.in")

    if version:
        expected = source_root / f"openvpn-{version.lstrip('v')}"
        if (expected / template_name).is_file():
            return expected

    try:
        candidates = sorted(
            path
            for path in source_root.iterdir()
            if path.is_dir() and (path / template_name).is_file()
        )
    except OSError:
        return None

    return candidates[0] if len(candidates) == 1 else None


def _list_openvpn_server_units() -> list[str]:
    """读取 systemd 已安装的 OpenVPN 服务端 unit 文件列表。"""

    try:
        proc = subprocess.run(
            [
                "systemctl",
                "list-unit-files",
                "--type=service",
                "--no-legend",
                "--no-pager",
            ],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except FileNotFoundError as exc:
        raise RuntimeError("未找到 systemctl，无法检查 OpenVPN 服务单元") from exc
    except (subprocess.TimeoutExpired, OSError) as exc:
        raise RuntimeError(f"检查 OpenVPN systemd 服务单元失败: {exc}") from exc

    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        raise RuntimeError(f"systemctl list-unit-files 执行失败: {detail or proc.returncode}")

    return [
        line.split()[0]
        for line in proc.stdout.splitlines()
        if line.split() and "openvpn-server@" in line.split()[0].lower()
    ]


def ensure_openvpn_server_systemd_unit(
    openvpn_bin: str | Path,
    openvpn_version: str | None = None,
    on_output: Callable[[str], None] | None = None,
) -> str:
    """没有 OpenVPN server unit 时，复用原安装脚本生成并安装。"""

    binary = Path(openvpn_bin).expanduser()
    if not binary.is_file():
        raise FileNotFoundError(f"OpenVPN 二进制不存在: {binary}")
    binary = binary.resolve()

    existing = _list_openvpn_server_units()
    if existing:
        unit_name = existing[0]
        _emit(on_output, f"[检查] 已存在 OpenVPN 服务单元，跳过安装: {unit_name}")
        return unit_name

    if not openvpn_version:
        from app.services.openvpn.detector import _extract_version

        openvpn_version = _extract_version(str(binary))
    if not openvpn_version:
        raise RuntimeError(f"无法识别 OpenVPN 版本，不能生成服务单元: {binary}")

    source_dir = _find_openvpn_source_dir(openvpn_version)
    if source_dir is None:
        raise FileNotFoundError(
            "未找到 OpenVPN 源码中的 systemd 模板。"
            f"请确认源码目录位于 {_OPENVPN_SOURCE_ROOT}，"
            "并包含 distro/systemd/openvpn-server@.service.in"
        )

    tag = openvpn_version if openvpn_version.startswith("v") else f"v{openvpn_version}"
    _emit(
        on_output,
        "[检查] 未找到 OpenVPN server 服务单元，复用原安装脚本生成并安装",
    )
    install_openvpn_server_systemd_from_staged_source(
        source_dir=source_dir,
        tag=tag,
        openvpn_bin=binary,
        on_output=on_output,
    )

    installed = _list_openvpn_server_units()
    if not installed:
        raise RuntimeError("OpenVPN systemd 服务文件生成完成，但 systemd 仍未检测到服务单元")

    _emit(on_output, f"[完成] OpenVPN 服务单元已就绪: {installed[0]}")
    return installed[0]



def install_easyrsa(
    on_output: Callable[[str], None] | None = None,
) -> str:
    """仅安装 EasyRSA，不触碰已存在的 OpenVPN 包、二进制或服务。"""

    env = _build_easyrsa_install_env(on_output)
    env["EASYRSA_INSTALL_DIR"] = str(EASYRSA_INSTALL_DIR)
    script_path = _INSTALL_SCRIPTS_DIR / "easyrsa-install.sh"
    if not script_path.is_file():
        raise FileNotFoundError(f"EasyRSA 安装脚本不存在: {script_path}")

    _emit(on_output, f"[信息] OpenVPN 已就绪，单独安装 EasyRSA（{script_path.name}）...")
    _run_command(
        ["bash", str(script_path)],
        on_output,
        env=_subprocess_env(env),
    )

    installed_bin = Path(EASYRSA_INSTALL_DIR) / "current" / "easyrsa"
    if not installed_bin.is_file():
        raise RuntimeError(f"EasyRSA 安装命令完成，但脚本不存在: {installed_bin}")

    _emit(on_output, f"[完成] EasyRSA 安装成功: {installed_bin}")
    return str(installed_bin)


def install_openvpn(
    distro: str,
    version_id: str,
    on_output: Callable[[str], None] | None = None,
) -> bool:
    """按发行版自动安装 OpenVPN（执行本地 install 入口脚本）。"""
    distro = distro.lower().strip()

    try:
        env = _build_install_env(
            distro,
            version_id,
            install_easyrsa=True,
            install_server_systemd=True,
            install_client_systemd=False,
            install_server_override=True,
            on_output=on_output,
        )
        script_path = _install_script_path(distro)
        _emit(on_output, f"[信息] 开始为 {distro} {version_id} 安装 OpenVPN（{script_path.name}）...")
        _run_command(["bash", str(script_path)], on_output, env=_subprocess_env(env))

        # 仅本地服务端 OpenVPN 安装后部署系统日志策略；对端脚本不安装
        # NiceVPN 特定配置（远端客户端安装逻辑继续保持不变）。
        logrotate_installer = (
            _INSTALL_SCRIPTS_DIR.parents[2]
            / "deploy"
            / "shell"
            / "install-nicevpn-logrotate.sh"
        )
        if logrotate_installer.is_file() and os.name == "posix":
            try:
                _run_command(
                    ["bash", str(logrotate_installer)], on_output, env=_subprocess_env(env)
                )
            except Exception as rotate_exc:
                _emit(on_output, f"[警告] OpenVPN 已安装，但日志轮转初始化未完成：{rotate_exc}")
                log.warning("OpenVPN 已安装，日志轮转初始化失败: %s", rotate_exc)
    except Exception as exc:
        _emit(on_output, f"[错误] 安装 OpenVPN 失败: {exc}")
        log.exception("安装 OpenVPN 失败")
        return False

    _emit(on_output, "[完成] OpenVPN 安装成功")
    return True


def _install_script_path(distro: str) -> Path:
    """返回发行版对应的安装入口脚本路径。"""
    distro = distro.lower().strip()
    script_path = _INSTALL_SCRIPTS_DIR / f"openvpn-install-{distro}.sh"
    if not script_path.is_file():
        raise ValueError(f"不支持的发行版类型: {distro}")
    return script_path


def _build_install_env(
    distro: str,
    version_id: str,
    *,
    install_easyrsa: bool,
    install_server_systemd: bool,
    install_client_systemd: bool,
    install_server_override: bool,
    on_output: Callable[[str], None] | None,
) -> dict[str, str]:
    """构造传给安装 shell 脚本的环境变量。"""
    distro = distro.lower().strip()
    openvpn_release = _resolve_latest_release_asset(
        repo="OpenVPN/openvpn",
        asset_prefix="openvpn-",
        asset_suffixes=(".tar.gz", ".tgz"),
        fallback_tag=_OPENVPN_FALLBACK_TAG,
        fallback_asset=_OPENVPN_FALLBACK_ASSET,
        on_output=on_output,
    )
    env: dict[str, str] = {
        "OPENVPN_MIN_VERSION": OPENVPN_MIN_VERSION,
        "OPENVPN_TAG": openvpn_release["tag_name"],
        "OPENVPN_ASSET": openvpn_release["asset_name"],
        "OPENVPN_DOWNLOAD_URLS": "\n".join(
            _build_github_candidate_urls(openvpn_release["download_url"])
        ),
        "INSTALL_EASYRSA": "1" if install_easyrsa else "0",
        "INSTALL_SERVER_SYSTEMD": "1" if install_server_systemd else "0",
        "INSTALL_CLIENT_SYSTEMD": "1" if install_client_systemd else "0",
        "INSTALL_SERVER_OVERRIDE": "1" if install_server_override else "0",
        "APT_KEY_URL": _APT_KEY_URL,
    }
    if install_easyrsa:
        env.update(_build_easyrsa_install_env(on_output))
    if distro == "debian":
        env["DEBIAN_CODENAME"] = _get_codename(version_id)
    elif distro != "rhel":
        raise ValueError(f"不支持的发行版族: {distro}")
    return env


def _build_easyrsa_install_env(
    on_output: Callable[[str], None] | None,
) -> dict[str, str]:
    """独立解析 EasyRSA release，避免为了补装 EasyRSA 再请求 OpenVPN release。"""

    release = _resolve_latest_release_asset(
        repo="OpenVPN/easy-rsa",
        asset_prefix="EasyRSA-",
        asset_suffixes=(".tar.gz", ".tgz"),
        fallback_tag=_EASYRSA_FALLBACK_TAG,
        fallback_asset=_EASYRSA_FALLBACK_ASSET,
        on_output=on_output,
    )
    return {
        "INSTALL_EASYRSA": "1",
        "EASYRSA_TAG": release["tag_name"],
        "EASYRSA_ASSET": release["asset_name"],
        "EASYRSA_DOWNLOAD_URLS": "\n".join(
            _build_github_candidate_urls(release["download_url"])
        ),
    }


def _render_remote_install_script(distro: str, env: dict[str, str]) -> str:
    """将对端安装脚本打包为单段 bash（export 环境变量 + 内联 common + 入口逻辑）。"""
    lines = ["set -eux"]
    for key, value in env.items():
        lines.append(f"export {key}={shlex.quote(value)}")

    common_text = _read_install_script_text("openvpn-install-common.sh")
    entry_text = _strip_entry_script_wrapper(
        _read_install_script_text(f"openvpn-install-{distro}.sh")
    )
    return "\n".join(lines) + "\n" + common_text + "\n" + entry_text


def _read_install_script_text(name: str) -> str:
    """读取 install 目录下的 shell 脚本正文。"""
    path = _INSTALL_SCRIPTS_DIR / name
    if not path.is_file():
        raise FileNotFoundError(f"安装脚本不存在: {path}")
    return path.read_text(encoding="utf-8").replace("\r\n", "\n")


def _strip_entry_script_wrapper(text: str) -> str:
    """去掉入口脚本的 shebang、set -euo、source common 行，保留业务逻辑。"""
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
        if "source " in stripped and "openvpn-install-common.sh" in stripped:
            continue
        if stripped.startswith("# shellcheck"):
            continue
        out_lines.append(line)
    return "\n".join(out_lines).strip() + "\n"


def _subprocess_env(extra: dict[str, str]) -> dict[str, str]:
    """合并当前进程环境与安装脚本专用变量。"""
    merged = dict(os.environ)
    merged.update(extra)
    return merged


def _resolve_latest_release_asset(
    repo: str,
    asset_prefix: str,
    asset_suffixes: tuple[str, ...],
    fallback_tag: str,
    fallback_asset: str,
    on_output: Callable[[str], None] | None,
) -> dict:
    """解析 GitHub 最新 release 资源，失败时使用兜底版本。"""
    api_url = _GITHUB_API_LATEST.format(repo=repo)
    last_error: Exception | None = None

    for candidate_url in _build_github_candidate_urls(api_url):
        request = urllib.request.Request(
            candidate_url,
            headers={
                "Accept": "application/vnd.github+json",
                "User-Agent": "openvpn-mgmt-installer",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                data = json.loads(response.read().decode("utf-8"))

            tag_name = data.get("tag_name") or fallback_tag
            for asset in data.get("assets", []):
                name = str(asset.get("name", ""))
                if not name.startswith(asset_prefix):
                    continue
                if not name.endswith(asset_suffixes):
                    continue
                download_url = asset.get("browser_download_url")
                if download_url:
                    _emit(on_output, f"[信息] 使用 {repo} 最新 release: {tag_name}")
                    return {
                        "tag_name": tag_name,
                        "asset_name": name,
                        "download_url": download_url,
                    }
        except Exception as exc:
            last_error = exc
            continue

    if last_error is not None:
        _emit(on_output, f"[警告] 获取 {repo} 最新 release 失败，改用兜底版本: {last_error}")

    return {
        "tag_name": fallback_tag,
        "asset_name": fallback_asset,
        "download_url": f"https://github.com/{repo}/releases/download/{fallback_tag}/{fallback_asset}",
    }


def _get_codename(version_id: str) -> str:
    """将 Debian 系版本号映射为 apt 源代号。"""
    vid = str(version_id or "").strip().strip('"').strip("'")
    codename_map: dict[str, str] = {
        "11": "bullseye",
        "12": "bookworm",
        "13": "trixie",
        "20.04": "focal",
        "22.04": "jammy",
        "24.04": "noble",
    }

    codename = codename_map.get(vid)
    if not codename:
        raise ValueError(
            f"不支持的 debian 系版本: {vid}，"
            f"支持的版本: {', '.join(codename_map.keys())}"
        )
    return codename


def _run_command(
    cmd: list[str],
    on_output: Callable[[str], None] | None,
    *,
    env: dict[str, str] | None = None,
    cwd: Path | None = None,
) -> None:
    """执行命令并实时输出日志。"""
    _emit(on_output, f"[执行] {' '.join(cmd)}")
    try:
        proc = subprocess.Popen(
            cmd,
            cwd=str(cwd) if cwd else None,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
    except (FileNotFoundError, OSError) as exc:
        raise RuntimeError(f"无法执行命令: {exc}") from exc

    assert proc.stdout is not None
    for line in proc.stdout:
        _emit(on_output, line.rstrip("\n"))

    returncode = proc.wait()
    if returncode != 0:
        raise RuntimeError(f"命令执行失败，退出码 {returncode}: {' '.join(cmd)}")


def _emit(callback: Callable[[str], None] | None, message: str) -> None:
    """安全地输出日志。"""
    log.info(message)
    if not callback:
        return
    try:
        callback(message)
    except Exception:
        log.warning("输出回调执行异常", exc_info=True)


def _build_github_candidate_urls(url: str) -> list[str]:
    """构造 GitHub 原始地址与代理地址候选列表。"""
    candidates: list[str] = []
    config = load_config()
    proxies = list(config.get("github_proxy_urls", []) or [])
    normalized_proxies: list[str] = []
    for proxy in proxies:
        prefix = str(proxy).strip()
        if not prefix:
            continue
        if not prefix.endswith("/"):
            prefix = prefix + "/"
        normalized_proxies.append(prefix)

    if any(url.startswith(prefix) for prefix in normalized_proxies):
        return [url]

    if "github.com" not in url and "githubusercontent.com" not in url and "api.github.com" not in url:
        return [url]

    for prefix in normalized_proxies:
        candidates.append(prefix + url)

    candidates.append(url)

    deduped: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        if candidate not in seen:
            seen.add(candidate)
            deduped.append(candidate)
    return deduped
