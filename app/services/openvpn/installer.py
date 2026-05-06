# -*- coding: utf-8 -*-
"""OpenVPN 自动安装服务"""
import json
import logging
import os
import shutil
import subprocess
import tempfile
import urllib.request
from pathlib import Path
from typing import Callable

from app.core.config import load_config
from app.core.constants import EASYRSA_INSTALL_DIR, OPENVPN_INSTALL_PREFIX

log = logging.getLogger(__name__)

_APT_KEY_URL = "https://swupdate.openvpn.net/repos/repo-public.gpg"
_APT_KEYRING = "/usr/share/keyrings/openvpn-archive-keyring.gpg"
_GITHUB_API_LATEST = "https://api.github.com/repos/{repo}/releases/latest"

_OPENVPN_FALLBACK_TAG = "v2.7.0"
_OPENVPN_FALLBACK_ASSET = "openvpn-2.7.0.tar.gz"
_OPENVPN_TLS_CRYPT_V2_MIN_VERSION = "2.5.0"
_EASYRSA_FALLBACK_TAG = "v3.2.4"
_EASYRSA_FALLBACK_ASSET = "EasyRSA-3.2.4.tgz"
_SOURCE_ARCHIVE_CACHE_DIR = Path("/tmp")

_RHEL_BUILD_DEPS = [
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
]


def build_peer_openvpn_install_script(distro: str, version_id: str) -> str:
    """生成在 **对端** 以 root/sudo 执行的 bash 脚本。

    与 ``InitWizard._install_openvpn`` 同源策略：**Debian 系** 使用官方 OpenVPN apt 源（同 ``_install_debian``，仅装 ``openvpn`` 不装 easy-rsa）；**RHEL 系** 先查询仓库版本，低于 tls-crypt-v2 支持版本时才源码编译。

    Args:
        distro: ``detect_distro_family`` 返回值 ``debian`` / ``rhel``
        version_id: 远端 ``VERSION_ID``（如 ``22.04``、``12``）

    Raises:
        ValueError: 不支持的族或 Debian 版本映射缺失
    """
    distro = distro.lower().strip()
    vid = str(version_id or "").strip().strip('"').strip("'")
    if distro == "debian":
        codename = _get_codename(vid)
        return (
            "set -eux\n"
            f"curl -fsSL {_APT_KEY_URL} | gpg --dearmor -o {_APT_KEYRING}\n"
            f"echo 'deb [signed-by={_APT_KEYRING}] "
            f"https://build.openvpn.net/debian/openvpn/release/{codename} {codename} main' "
            f"> /etc/apt/sources.list.d/openvpn.list\n"
            "export DEBIAN_FRONTEND=noninteractive\n"
            "apt-get update -y\n"
            "apt-get install -y openvpn\n"
        )
    if distro == "rhel":
        deps = " ".join(_RHEL_BUILD_DEPS)
        archive_url = f"https://github.com/OpenVPN/openvpn/releases/download/{_OPENVPN_FALLBACK_TAG}/{_OPENVPN_FALLBACK_ASSET}"
        archive_urls = "\n".join(_build_github_candidate_urls(archive_url))
        return (
            "set -eux\n"
            f"OPENVPN_TAG='{_OPENVPN_FALLBACK_TAG}'\n"
            f"OPENVPN_ASSET='{_OPENVPN_FALLBACK_ASSET}'\n"
            f"OPENVPN_MIN_TLS_CRYPT_V2='{_OPENVPN_TLS_CRYPT_V2_MIN_VERSION}'\n"
            "OPENVPN_PREFIX='/opt/openvpn'\n"
            "OPENVPN_CACHE=\"/tmp/${OPENVPN_ASSET}\"\n"
            "OPENVPN_SRC_ROOT='/tmp/ovpn-mgmt-openvpn-src'\n"
            "OPENVPN_SRC_DIR=\"${OPENVPN_SRC_ROOT}/${OPENVPN_ASSET%.tar.gz}\"\n"
            "version_ge() {\n"
            "  [ \"$(printf '%s\\n%s\\n' \"$2\" \"$1\" | sort -V | head -n 1)\" = \"$2\" ]\n"
            "}\n"
            "if command -v dnf >/dev/null 2>&1; then\n"
            "  PM=dnf\n"
            "elif command -v yum >/dev/null 2>&1; then\n"
            "  PM=yum\n"
            "else\n"
            "  echo '未找到 dnf/yum' >&2\n"
            "  exit 1\n"
            "fi\n"
            "PKG_VERSION=\"$(${PM} -q info openvpn 2>/dev/null "
            "| awk -F: '/^Version[[:space:]]*:/ {gsub(/^[ \\t]+|[ \\t]+$/, \"\", $2); print $2}' "
            "| sort -V | tail -n 1)\"\n"
            "if [ -n \"${PKG_VERSION}\" ] && version_ge \"${PKG_VERSION}\" \"${OPENVPN_MIN_TLS_CRYPT_V2}\"; then\n"
            "  echo \"仓库 openvpn ${PKG_VERSION} 支持 tls-crypt-v2，使用系统包安装\"\n"
            "  ${PM} install -y openvpn\n"
            "  exit 0\n"
            "fi\n"
            "echo \"仓库 openvpn 版本 ${PKG_VERSION:-unknown} 低于 ${OPENVPN_MIN_TLS_CRYPT_V2}，改为源码编译安装\"\n"
            f"${{PM}} install -y {deps}\n"
            "if [ ! -s \"${OPENVPN_CACHE}\" ]; then\n"
            "  ok=0\n"
            "  while IFS= read -r url; do\n"
            "    [ -n \"${url}\" ] || continue\n"
            "    echo \"下载 OpenVPN 源码包: ${url}\"\n"
            "    if curl -fL --retry 3 --retry-delay 2 \"${url}\" -o \"${OPENVPN_CACHE}.part\" "
            "&& tar -tzf \"${OPENVPN_CACHE}.part\" >/dev/null; then\n"
            "      mv -f \"${OPENVPN_CACHE}.part\" \"${OPENVPN_CACHE}\"\n"
            "      ok=1\n"
            "      break\n"
            "    fi\n"
            "    rm -f \"${OPENVPN_CACHE}.part\"\n"
            "  done <<'OVPN_URLS'\n"
            f"{archive_urls}\n"
            "OVPN_URLS\n"
            "  [ \"${ok}\" = \"1\" ] || { echo '下载 OpenVPN 源码包失败' >&2; exit 1; }\n"
            "else\n"
            "  echo \"复用 OpenVPN 源码缓存: ${OPENVPN_CACHE}\"\n"
            "  tar -tzf \"${OPENVPN_CACHE}\" >/dev/null\n"
            "fi\n"
            "rm -rf \"${OPENVPN_SRC_ROOT}\"\n"
            "mkdir -p \"${OPENVPN_SRC_ROOT}\"\n"
            "tar -xzf \"${OPENVPN_CACHE}\" -C \"${OPENVPN_SRC_ROOT}\"\n"
            "cd \"${OPENVPN_SRC_DIR}\"\n"
            "./configure --prefix=\"${OPENVPN_PREFIX}\" --enable-pkcs11 --enable-async-push --enable-systemd\n"
            "make -j\"$(getconf _NPROCESSORS_ONLN 2>/dev/null || echo 1)\"\n"
            "make install\n"
            "ln -sfn \"${OPENVPN_PREFIX}/sbin/openvpn\" /usr/local/sbin/openvpn\n"
            "if [ -f distro/systemd/openvpn-client@.service.in ]; then\n"
            "  sed "
            "-e \"s#@sbindir@#${OPENVPN_PREFIX}/sbin#g\" "
            "-e 's#@OPENVPN_VERSION_MAJOR@#2#g' "
            "-e 's#@OPENVPN_VERSION_MINOR@#7#g' "
            "distro/systemd/openvpn-client@.service.in > /etc/systemd/system/openvpn-client@.service\n"
            "  systemctl daemon-reload\n"
            "fi\n"
        )
    raise ValueError(f"不支持的发行版族: {distro}")


def install_openvpn(
    distro: str,
    version_id: str,
    on_output: Callable[[str], None] | None = None,
) -> bool:
    """按发行版自动安装 OpenVPN。"""
    distro = distro.lower().strip()

    try:
        if distro == "debian":
            _install_debian(version_id, on_output)
        elif distro == "rhel":
            _install_rhel_from_source(version_id, on_output)
        else:
            raise ValueError(f"不支持的发行版类型: {distro}")
    except Exception as exc:
        _emit(on_output, f"[错误] 安装 OpenVPN 失败: {exc}")
        log.exception("安装 OpenVPN 失败")
        return False

    _emit(on_output, "[完成] OpenVPN 安装成功")
    return True


def _install_debian(version_id: str, on_output: Callable[[str], None] | None) -> None:
    """Debian 系通过官方仓库安装。"""
    codename = _get_codename(version_id)
    commands = [
        ["bash", "-lc", f"curl -fsSL {_APT_KEY_URL} | gpg --dearmor -o {_APT_KEYRING}"],
        [
            "bash", "-lc",
            "echo 'deb [signed-by={keyring}] https://build.openvpn.net/debian/openvpn/release/{code} "
            "{code} main' > /etc/apt/sources.list.d/openvpn.list".format(
                keyring=_APT_KEYRING,
                code=codename,
            ),
        ],
        ["apt-get", "update", "-y"],
        ["apt-get", "install", "-y", "openvpn", "easy-rsa"],
    ]

    _emit(on_output, f"[信息] 开始为 debian {version_id} 安装 OpenVPN ...")
    for cmd in commands:
        _run_command(cmd, on_output)
    _install_openvpn_server_systemd_override(on_output)


def _install_rhel_from_source(version_id: str, on_output: Callable[[str], None] | None) -> None:
    """红帽系通过源码编译安装 OpenVPN，并安装 Easy-RSA。"""
    major = _parse_major_version(version_id)
    package_manager = "yum" if major is not None and major <= 7 else "dnf"

    _emit(on_output, f"[信息] 开始为 rhel {version_id} 编译安装 OpenVPN ...")
    _run_command([package_manager, "install", "-y", *_RHEL_BUILD_DEPS], on_output)

    openvpn_release = _resolve_latest_release_asset(
        repo="OpenVPN/openvpn",
        asset_prefix="openvpn-",
        asset_suffixes=(".tar.gz", ".tgz"),
        fallback_tag=_OPENVPN_FALLBACK_TAG,
        fallback_asset=_OPENVPN_FALLBACK_ASSET,
        on_output=on_output,
    )
    easyrsa_release = _resolve_latest_release_asset(
        repo="OpenVPN/easy-rsa",
        asset_prefix="EasyRSA-",
        asset_suffixes=(".tar.gz", ".tgz"),
        fallback_tag=_EASYRSA_FALLBACK_TAG,
        fallback_asset=_EASYRSA_FALLBACK_ASSET,
        on_output=on_output,
    )

    with tempfile.TemporaryDirectory(prefix="openvpn-build-") as temp_dir:
        temp_path = Path(temp_dir)

        openvpn_archive = _SOURCE_ARCHIVE_CACHE_DIR / openvpn_release["asset_name"]
        easyrsa_archive = _SOURCE_ARCHIVE_CACHE_DIR / easyrsa_release["asset_name"]

        _download_file(openvpn_release["download_url"], openvpn_archive, on_output)
        _download_file(easyrsa_release["download_url"], easyrsa_archive, on_output)

        openvpn_src_dir = _extract_archive(openvpn_archive, temp_path / "openvpn-src", on_output)
        easyrsa_src_dir = _extract_archive(easyrsa_archive, temp_path / "easyrsa-src", on_output)

        configure_cmd = [
            "./configure",
            f"--prefix={OPENVPN_INSTALL_PREFIX}",
            "--enable-pkcs11",
            "--enable-async-push",
            "--enable-systemd",
        ]
        _run_command(configure_cmd, on_output, cwd=openvpn_src_dir)
        _run_command(["make", f"-j{max(1, os.cpu_count() or 1)}"], on_output, cwd=openvpn_src_dir)
        _run_command(["make", "install"], on_output, cwd=openvpn_src_dir)

        _install_easyrsa(easyrsa_src_dir, easyrsa_release["tag_name"], on_output)
        _install_systemd_unit(openvpn_src_dir, openvpn_release["tag_name"], on_output)
        _install_openvpn_server_systemd_override(on_output)

    _install_openvpn_symlink(on_output)
    _emit(
        on_output,
        f"[信息] 已安装 OpenVPN 到 {OPENVPN_INSTALL_PREFIX}，Easy-RSA 到 {EASYRSA_INSTALL_DIR}",
    )


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


def _download_file(
    url: str,
    output_path: Path,
    on_output: Callable[[str], None] | None,
) -> None:
    """下载文件到指定路径；若 /tmp 缓存文件可用则直接复用。"""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if output_path.exists():
        _emit(on_output, f"[缓存] 发现已下载源码包，直接复用: {output_path}")
        try:
            _validate_archive_file(output_path, on_output)
            return
        except Exception as exc:
            _emit(on_output, f"[警告] 缓存源码包校验失败，将重新下载: {exc}")
            output_path.unlink()

    partial_path = output_path.with_name(f"{output_path.name}.part")
    last_error: Exception | None = None
    for candidate_url in _build_github_candidate_urls(url):
        _emit(on_output, f"[下载] {candidate_url}")
        try:
            _run_command(
                [
                    "curl",
                    "--location",
                    "--fail",
                    "--silent",
                    "--show-error",
                    "--retry",
                    "3",
                    "--retry-delay",
                    "2",
                    "--continue-at",
                    "-",
                    "--connect-timeout",
                    "15",
                    "--max-time",
                    "1800",
                    "--output",
                    str(partial_path),
                    candidate_url,
                ],
                on_output,
            )
            _validate_archive_file(partial_path, on_output)
            partial_path.replace(output_path)
            _emit(on_output, f"[缓存] 源码包已保存: {output_path}")
            return
        except Exception as exc:
            last_error = exc
            try:
                if partial_path.exists():
                    partial_path.unlink()
            except OSError as unlink_exc:
                raise RuntimeError(f"删除失败的临时下载文件失败: {partial_path}") from unlink_exc
            continue

    raise RuntimeError(f"下载失败: {url}，最后错误: {last_error}")


def _extract_archive(
    archive_path: Path,
    target_dir: Path,
    on_output: Callable[[str], None] | None,
) -> Path:
    """解压 tar 包并返回根目录路径。"""
    target_dir.mkdir(parents=True, exist_ok=True)
    _run_command(
        [
            "tar",
            "-xzf",
            str(archive_path),
            "-C",
            str(target_dir),
        ],
        on_output,
    )

    children = [item for item in target_dir.iterdir() if item.is_dir()]
    if not children:
        raise RuntimeError(f"解压失败，未找到目录: {archive_path}")
    return children[0]


def _validate_archive_file(
    archive_path: Path,
    on_output: Callable[[str], None] | None,
) -> None:
    """校验下载后的归档文件是否存在且可正常列出内容。"""
    if not archive_path.exists():
        raise RuntimeError(f"下载文件不存在: {archive_path}")

    if archive_path.stat().st_size <= 0:
        raise RuntimeError(f"下载文件为空: {archive_path}")

    _run_command(["file", str(archive_path)], on_output)
    _run_command(["tar", "-tzf", str(archive_path)], on_output)


def _install_easyrsa(
    extracted_dir: Path,
    tag_name: str,
    on_output: Callable[[str], None] | None,
) -> None:
    """安装 Easy-RSA 到固定目录。"""
    source_dir = extracted_dir / "easyrsa3"
    if not source_dir.is_dir():
        source_dir = extracted_dir

    version_name = tag_name.lstrip("v") or "latest"
    releases_dir = Path(EASYRSA_INSTALL_DIR) / "releases"
    install_dir = releases_dir / version_name
    current_link = Path(EASYRSA_INSTALL_DIR) / "current"

    releases_dir.mkdir(parents=True, exist_ok=True)
    if install_dir.exists():
        shutil.rmtree(install_dir)
    shutil.copytree(source_dir, install_dir)

    if current_link.is_symlink() or current_link.exists():
        if current_link.is_dir() and not current_link.is_symlink():
            shutil.rmtree(current_link)
        else:
            current_link.unlink()
    current_link.symlink_to(install_dir, target_is_directory=True)

    easyrsa_bin = install_dir / "easyrsa"
    if not easyrsa_bin.is_file():
        raise RuntimeError(f"Easy-RSA 安装失败，未找到 easyrsa: {easyrsa_bin}")

    bin_link = Path("/usr/local/bin/easyrsa")
    if bin_link.is_symlink() or bin_link.exists():
        bin_link.unlink()
    bin_link.symlink_to(easyrsa_bin)
    _emit(on_output, f"[信息] Easy-RSA 已安装到 {install_dir}")


def _install_openvpn_symlink(on_output: Callable[[str], None] | None) -> None:
    """创建 OpenVPN 兼容软链接，避免 PATH 检测失败。"""
    source = Path(OPENVPN_INSTALL_PREFIX) / "sbin" / "openvpn"
    if not source.is_file():
        raise RuntimeError(f"未找到 OpenVPN 可执行文件: {source}")

    target = Path("/usr/local/sbin/openvpn")
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.is_symlink() or target.exists():
        target.unlink()
    target.symlink_to(source)
    _emit(on_output, f"[信息] 已创建 OpenVPN 软链接: {target} -> {source}")


def _install_systemd_unit(
    openvpn_src_dir: Path,
    tag_name: str,
    on_output: Callable[[str], None] | None,
) -> None:
    """基于 OpenVPN 源码官方 server 模板安装项目兼容的 systemd 单元。"""
    unit_path = Path("/etc/systemd/system/openvpn-server@.service")
    unit_content = _render_openvpn_systemd_unit_from_source(openvpn_src_dir, tag_name)
    unit_path.parent.mkdir(parents=True, exist_ok=True)
    unit_path.write_text(unit_content, encoding="utf-8")
    _run_command(["systemctl", "daemon-reload"], on_output)
    _emit(on_output, f"[信息] 已基于 OpenVPN 源码官方模板安装 systemd 单元文件: {unit_path}")


def _install_openvpn_server_systemd_override(on_output: Callable[[str], None] | None) -> None:
    """安装服务端 systemd drop-in，允许官方 unit 写入项目运行时目录。"""
    override_dir = Path("/etc/systemd/system/openvpn-server@.service.d")
    override_path = override_dir / "10-ovpn-mgmt.conf"
    override_content = """[Service]
ReadWritePaths=/etc/openvpn
"""
    override_dir.mkdir(parents=True, exist_ok=True)
    override_path.write_text(override_content, encoding="utf-8")
    _run_command(["systemctl", "daemon-reload"], on_output)
    _emit(on_output, f"[信息] 已安装 OpenVPN server systemd override: {override_path}")


def _render_openvpn_systemd_unit_from_source(openvpn_src_dir: Path, tag_name: str) -> str:
    """读取源码 ``distro/systemd/openvpn-server@.service.in`` 并适配项目运行时路径。"""
    template_path = openvpn_src_dir / "distro" / "systemd" / "openvpn-server@.service.in"
    if not template_path.is_file():
        raise RuntimeError(f"OpenVPN 源码缺少官方 systemd server 模板: {template_path}")
    major, minor = _parse_openvpn_version_parts(tag_name)
    text = template_path.read_text(encoding="utf-8")
    text = text.replace("@sbindir@", f"{OPENVPN_INSTALL_PREFIX}/sbin")
    text = text.replace("@OPENVPN_VERSION_MAJOR@", major)
    text = text.replace("@OPENVPN_VERSION_MINOR@", minor)
    # 项目在 server.conf 内维护 status/log 路径；避免 unit 命令行覆盖配置。
    text = text.replace(
        f"ExecStart={OPENVPN_INSTALL_PREFIX}/sbin/openvpn "
        "--status %t/openvpn-server/status-%i.log --status-version 2 "
        "--suppress-timestamps --config %i.conf",
        f"ExecStart={OPENVPN_INSTALL_PREFIX}/sbin/openvpn --suppress-timestamps --config %i.conf",
    )
    # 项目运行时会在 /etc/openvpn 写 ipp 与日志，保留官方保护并显式放行该目录。
    text = text.replace("ProtectSystem=true\n", "ProtectSystem=true\nReadWritePaths=/etc/openvpn\n")
    return text


def _parse_openvpn_version_parts(tag_name: str) -> tuple[str, str]:
    """从 release tag 提取 major/minor，用于替换官方 unit 文档 URL 占位符。"""
    raw = str(tag_name or "").strip().lstrip("v")
    parts = raw.split(".")
    if len(parts) >= 2 and parts[0].isdigit() and parts[1].isdigit():
        return parts[0], parts[1]
    return "2", "6"


def _get_codename(version_id: str) -> str:
    """将 Debian 系版本号映射为 apt 源代号。"""
    codename_map: dict[str, str] = {
        "11": "bullseye",
        "12": "bookworm",
        "13": "trixie",
        "20.04": "focal",
        "22.04": "jammy",
        "24.04": "noble",
    }

    codename = codename_map.get(version_id)
    if not codename:
        raise ValueError(
            f"不支持的 debian 系版本: {version_id}，"
            f"支持的版本: {', '.join(codename_map.keys())}"
        )
    return codename


def _parse_major_version(version_id: str) -> int | None:
    """提取版本号主版本。"""
    try:
        return int(version_id.split(".")[0])
    except (ValueError, IndexError):
        return None


def _run_command(
    cmd: list[str],
    on_output: Callable[[str], None] | None,
    cwd: Path | None = None,
) -> None:
    """执行命令并实时输出日志。"""
    _emit(on_output, f"[执行] {' '.join(cmd)}")
    try:
        proc = subprocess.Popen(
            cmd,
            cwd=str(cwd) if cwd else None,
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

    # 原始 GitHub 地址放到最后，作为兜底。
    candidates.append(url)

    deduped: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        if candidate not in seen:
            seen.add(candidate)
            deduped.append(candidate)
    return deduped
