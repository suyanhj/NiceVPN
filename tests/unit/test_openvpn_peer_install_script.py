# -*- coding: utf-8 -*-
"""对端安装脚本与 os-release 解析（无 SSH）。"""

import os
import shlex
import subprocess

import pytest

from app.services.openvpn.detector import detect_distro_family, parse_os_release_text
from app.services.openvpn import installer as installer_mod
from app.services.openvpn.installer import (
    _INSTALL_SCRIPTS_DIR,
    _read_install_script_text,
    build_peer_openvpn_install_script,
    stage_openvpn_source_build,
)


def test_parse_os_release_text_ubuntu() -> None:
    text = """
ID=ubuntu
VERSION_ID="22.04"
ID_LIKE=debian
PRETTY_NAME="Ubuntu 22.04"
"""
    d = parse_os_release_text(text)
    assert d["id"] == "ubuntu"
    assert d["version_id"] == "22.04"
    assert d["id_like"] == "debian"
    assert detect_distro_family(d) == "debian"


def test_install_script_files_exist() -> None:
    for name in (
        "openvpn-install-common.sh",
        "openvpn-install-debian.sh",
        "openvpn-install-rhel.sh",
        "openvpn-install-systemd.sh",
        "ensure-runtime-dependencies.sh",
    ):
        assert (_INSTALL_SCRIPTS_DIR / name).is_file(), name


def test_openvpn_runtime_dependency_entry_uses_shared_profile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict] = []

    def fake_run(command, _output, *, env=None, cwd=None):
        calls.append({"command": command, "env": dict(env or {})})

    monkeypatch.setattr(installer_mod, "_run_command", fake_run)
    installer_mod.ensure_openvpn_runtime_dependencies()

    assert len(calls) == 1
    assert calls[0]["command"] == [
        "bash",
        str(_INSTALL_SCRIPTS_DIR / "ensure-runtime-dependencies.sh"),
    ]
    assert calls[0]["env"]["NICEVPN_DEPENDENCY_PROFILE"] == "openvpn-runtime"


def test_easyrsa_runtime_dependency_entry_uses_shared_profile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict] = []

    def fake_run(command, _output, *, env=None, cwd=None):
        calls.append({"command": command, "env": dict(env or {})})

    monkeypatch.setattr(installer_mod, "_run_command", fake_run)
    installer_mod.ensure_easyrsa_runtime_dependencies()

    assert len(calls) == 1
    assert calls[0]["env"]["NICEVPN_DEPENDENCY_PROFILE"] == "easyrsa-runtime"


def test_remote_install_script_inlines_shared_package_helpers() -> None:
    """对端 SSH 执行时不依赖 NiceVPN 脚本已存在于远端文件系统。"""
    script = build_peer_openvpn_install_script("rhel", "9")

    assert "nicevpn_linux_family() {" in script
    assert "nicevpn_package_manager() {" in script
    assert 'PM="$(nicevpn_package_manager)"' in script
    assert 'source "${SCRIPT_DIR}/../common/linux-packages.sh"' not in script
    assert 'source "${SCRIPT_DIR}/openvpn-install-common.sh"' not in script

    result = subprocess.run(
        ["bash", "-n"],
        input=script,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_build_peer_script_debian_ubuntu() -> None:
    s = build_peer_openvpn_install_script("debian", "22.04")
    assert "export DEBIAN_CODENAME=jammy" in s
    assert "export OPENVPN_MIN_VERSION=2.7.0" in s
    assert "version_ge()" in s
    assert "nicevpn_install_packages apt-get" in s and "openvpn" in s
    assert "openvpn_compile_install" in s
    assert "build.openvpn.net/debian" in s
    assert "export INSTALL_EASYRSA=0" in s


def test_build_peer_script_rhel() -> None:
    s = build_peer_openvpn_install_script("rhel", "9")
    assert "export OPENVPN_MIN_VERSION=2.7.0" in s
    assert "info openvpn" in s
    assert "openvpn_compile_install" in s
    assert "openvpn-client@.service" in s
    assert "INSTALL_CLIENT_SYSTEMD" in _read_install_script_text("openvpn-install-common.sh")


def test_common_script_supports_non_destructive_staging_builds() -> None:
    common = _read_install_script_text("openvpn-install-common.sh")
    assert 'OPENVPN_CREATE_SYMLINK:=1' in common
    assert 'OPENVPN_FORCE_SOURCE:=0' in common
    assert 'if [ "${OPENVPN_CREATE_SYMLINK}" = "1" ]' in common

    for name in ("openvpn-install-debian.sh", "openvpn-install-rhel.sh"):
        entry = _read_install_script_text(name)
        assert 'if [ "${OPENVPN_FORCE_SOURCE}" = "1" ]' in entry
        assert "install_source_dependencies" in entry
        assert "openvpn_compile_install" in entry


def test_stage_source_build_uses_distribution_install_entry(
    monkeypatch,
    tmp_path,
) -> None:
    prefix = tmp_path / "prefix"
    src_root = tmp_path / "src"
    cache = tmp_path / "openvpn-source.tar.gz"
    seen: dict[str, object] = {}

    monkeypatch.setattr(
        installer_mod,
        "_build_install_env",
        lambda *args, **kwargs: {
            "OPENVPN_TAG": "v2.7.8",
            "OPENVPN_ASSET": "openvpn-2.7.8.tar.gz",
            "OPENVPN_DOWNLOAD_URLS": "https://example.invalid/openvpn.tar.gz",
            "INSTALL_EASYRSA": "0",
            "INSTALL_SERVER_SYSTEMD": "0",
            "INSTALL_CLIENT_SYSTEMD": "0",
            "INSTALL_SERVER_OVERRIDE": "0",
            "DEBIAN_CODENAME": "noble",
        },
    )
    monkeypatch.setattr(
        installer_mod,
        "_install_script_path",
        lambda distro: tmp_path / f"openvpn-install-{distro}.sh",
    )

    def fake_run(cmd, _output, *, env=None, cwd=None):
        seen["cmd"] = cmd
        seen["env"] = dict(env or {})
        (prefix / "sbin").mkdir(parents=True)
        (prefix / "sbin" / "openvpn").write_text("binary", encoding="utf-8")
        (src_root / "openvpn-2.7.8").mkdir(parents=True)

    monkeypatch.setattr(installer_mod, "_run_command", fake_run)

    result = stage_openvpn_source_build(
        "debian",
        "24.04",
        prefix=prefix,
        cache=cache,
        src_root=src_root,
    )

    assert seen["cmd"] == ["bash", str(tmp_path / "openvpn-install-debian.sh")]
    env = seen["env"]
    assert env["OPENVPN_FORCE_SOURCE"] == "1"
    assert env["OPENVPN_CREATE_SYMLINK"] == "0"
    assert env["INSTALL_EASYRSA"] == "0"
    assert env["INSTALL_SERVER_SYSTEMD"] == "0"
    assert env["INSTALL_CLIENT_SYSTEMD"] == "0"
    assert env["INSTALL_SERVER_OVERRIDE"] == "0"
    assert env["OPENVPN_PREFIX"] == str(prefix)
    assert result["binary"] == str(prefix / "sbin" / "openvpn")


def test_systemd_common_generator_accepts_detected_openvpn_binary() -> None:
    common = _read_install_script_text("openvpn-install-common.sh")
    assert 'OPENVPN_SBIN_DIR:=${OPENVPN_PREFIX}/sbin' in common
    assert 'OPENVPN_BIN:=${OPENVPN_SBIN_DIR}/openvpn' in common
    assert ': "${OPENVPN_SRC_DIR:=${OPENVPN_SRC_ROOT}/${OPENVPN_ASSET%.tar.gz}}"' in common
    assert 'exec_new="ExecStart=${OPENVPN_BIN} --suppress-timestamps --config %i.conf"' in common
    assert 's#@sbindir@#${OPENVPN_SBIN_DIR}#g' in common


def test_systemd_install_entry_reuses_common_helpers() -> None:
    script = _read_install_script_text("openvpn-install-systemd.sh")
    assert "source " in script and "openvpn-install-common.sh" in script
    assert "openvpn_install_server_systemd_from_src" in script
    assert "openvpn_install_server_systemd_override" in script


def test_easyrsa_download_reuses_existing_binary_without_download(
    tmp_path: Path,
) -> None:
    install_root = tmp_path / "easy-rsa"
    existing = install_root / "current" / "easyrsa"
    existing.parent.mkdir(parents=True)
    existing.write_text("#!/bin/sh\\nexit 0\\n", encoding="utf-8")
    existing.chmod(0o755)

    result = _run_common_shell(
        tmp_path,
        "INSTALL_EASYRSA=1; easyrsa_download_and_install",
        extra_env={"EASYRSA_INSTALL_DIR": str(install_root)},
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "跳过下载、安装和覆盖" in result.stdout


def test_standalone_easyrsa_script_skips_existing_install_before_release_lookup(
    tmp_path: Path,
) -> None:
    install_root = tmp_path / "easy-rsa"
    existing = install_root / "current" / "easyrsa"
    existing.parent.mkdir(parents=True)
    existing.write_text("#!/bin/sh\\nexit 0\\n", encoding="utf-8")
    existing.chmod(0o755)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    openssl = bin_dir / "openssl"
    openssl.write_text("#!/bin/sh\\nexit 0\\n", encoding="utf-8")
    openssl.chmod(0o755)

    env = os.environ.copy()
    env.update({
        "PATH": str(bin_dir),
        "EASYRSA_INSTALL_DIR": str(install_root),
    })
    result = subprocess.run(
        ["/bin/bash", str(_INSTALL_SCRIPTS_DIR / "easyrsa-install.sh")],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "EasyRSA 已存在，复用" in result.stdout
    assert "必须设置 EASYRSA_DOWNLOAD_URLS" not in result.stderr


def test_common_script_has_shared_helpers() -> None:
    common = _read_install_script_text("openvpn-install-common.sh")
    assert "openvpn_download_source()" in common
    assert "easyrsa_download_and_install()" in common
    assert "openvpn_install_server_systemd_from_src()" in common

def _run_common_shell(
    tmp_path,
    command: str,
    *,
    pkg_config_version: str | None = None,
    pkg_config_module_available: bool = True,
    pkg_config_command: str = "pkg-config",
    extra_env: dict[str, str] | None = None,
):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    if pkg_config_command == "pkg-config":
        pkg_config = bin_dir / "pkg-config"
        if pkg_config_module_available and pkg_config_version:
            body = (
                "#!/bin/sh\n"
                'if [ "$1" = "--modversion" ] && [ "$2" = "libnl-genl-3.0" ]; then\n'
                f'    printf "%s\n" "{pkg_config_version}"\n'
                "    exit 0\n"
                "fi\n"
                "exit 1\n"
            )
        else:
            body = "#!/bin/sh\nexit 1\n"
        pkg_config.write_text(body, encoding="utf-8")
        pkg_config.chmod(0o755)

    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}:{env.get('PATH', '')}"
    env["PKG_CONFIG"] = pkg_config_command
    env.update(extra_env or {})
    common_path = shlex.quote(str(_INSTALL_SCRIPTS_DIR / "openvpn-install-common.sh"))
    shell = f"set -euo pipefail\nsource {common_path}\n{command}\n"
    return subprocess.run(
        ["bash", "-c", shell],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.parametrize(
    ("version", "supported"),
    [
        ("3.4.0", True),
        ("3.5.0", True),
        ("3.10.0", True),
        ("3.3.9", False),
        ("3.2.28", False),
    ],
)
def test_dco_probe_checks_libnl_genl_minimum_version(tmp_path, version, supported) -> None:
    result = _run_common_shell(
        tmp_path,
        "if openvpn_detect_dco_support; then echo DCO_SUPPORTED; else echo DCO_DISABLED; fi",
        pkg_config_version=version,
    )

    assert result.returncode == 0
    assert ("DCO_SUPPORTED" in result.stdout) is supported
    assert ("DCO_DISABLED" in result.stdout) is (not supported)


def test_dco_probe_disables_when_libnl_genl_module_is_missing(tmp_path) -> None:
    result = _run_common_shell(
        tmp_path,
        "if openvpn_detect_dco_support; then echo DCO_SUPPORTED; else echo DCO_DISABLED; fi",
        pkg_config_module_available=False,
    )

    assert result.returncode == 0
    assert "DCO_DISABLED" in result.stdout
    assert "未找到 libnl-genl-3.0" in result.stdout


def test_dco_probe_disables_when_pkg_config_command_is_missing(tmp_path) -> None:
    result = _run_common_shell(
        tmp_path,
        "if openvpn_detect_dco_support; then echo DCO_SUPPORTED; else echo DCO_DISABLED; fi",
        pkg_config_command="nicevpn-pkg-config-not-installed",
    )

    assert result.returncode == 0
    assert "DCO_DISABLED" in result.stdout
    assert "未找到 nicevpn-pkg-config-not-installed" in result.stdout


def test_configure_passes_disable_dco_when_libnl_is_too_old(tmp_path) -> None:
    args_log = tmp_path / "configure-args.log"
    configure = tmp_path / "configure"
    configure.write_text(
        "#!/usr/bin/env bash\n"
        'printf "%s\n" "$*" >> "$CONFIGURE_ARGS_LOG"\n'
        "exit 0\n",
        encoding="utf-8",
    )
    configure.chmod(0o755)

    result = _run_common_shell(
        tmp_path,
        "openvpn_configure_with_optional_features",
        pkg_config_version="3.2.28",
        extra_env={"CONFIGURE_ARGS_LOG": str(args_log)},
    )

    assert result.returncode == 0, result.stdout + result.stderr
    args = args_log.read_text(encoding="utf-8").splitlines()
    assert len(args) == 1
    assert "--disable-dco" in args[0]
    assert "--enable-async-push" in args[0]
    assert "--enable-systemd" in args[0]


def test_configure_retries_without_dco_on_specific_dependency_error(tmp_path) -> None:
    args_log = tmp_path / "configure-args.log"
    configure = tmp_path / "configure"
    configure.write_text(
        "#!/usr/bin/env bash\n"
        'printf "%s\n" "$*" >> "$CONFIGURE_ARGS_LOG"\n'
        'if [[ " $* " == *" --disable-dco "* ]]; then exit 0; fi\n'
        'printf "%s\n" "configure: error: libnl-genl-3.0 package not found or too old" > config.log\n'
        "exit 1\n",
        encoding="utf-8",
    )
    configure.chmod(0o755)

    result = _run_common_shell(
        tmp_path,
        "openvpn_configure_with_optional_features",
        pkg_config_version="3.4.0",
        extra_env={"CONFIGURE_ARGS_LOG": str(args_log)},
    )

    assert result.returncode == 0, result.stdout + result.stderr
    args = args_log.read_text(encoding="utf-8").splitlines()
    assert len(args) == 2
    assert "--disable-dco" not in args[0]
    assert "--disable-dco" in args[1]


def test_configure_does_not_hide_non_dco_errors(tmp_path) -> None:
    args_log = tmp_path / "configure-args.log"
    configure = tmp_path / "configure"
    configure.write_text(
        "#!/usr/bin/env bash\n"
        'printf "%s\n" "$*" >> "$CONFIGURE_ARGS_LOG"\n'
        'printf "%s\n" "configure: error: OpenSSL version too old" > config.log\n'
        "exit 2\n",
        encoding="utf-8",
    )
    configure.chmod(0o755)

    result = _run_common_shell(
        tmp_path,
        "openvpn_configure_with_optional_features",
        pkg_config_version="3.4.0",
        extra_env={"CONFIGURE_ARGS_LOG": str(args_log)},
    )

    assert result.returncode == 2
    assert len(args_log.read_text(encoding="utf-8").splitlines()) == 1




def test_runtime_dependency_profiles_are_defined_in_shared_helper() -> None:
    helper = (_INSTALL_SCRIPTS_DIR.parent / "common" / "linux-packages.sh").read_text(
        encoding="utf-8"
    )
    runtime = _read_install_script_text("ensure-runtime-dependencies.sh")

    assert "nicevpn_ensure_openvpn_runtime_dependencies()" in helper
    assert "nicevpn_ensure_easyrsa_runtime_dependencies()" in helper
    assert "nicevpn_ensure_easyrsa_install_dependencies()" in helper
    assert 'openvpn-runtime)' in runtime
    assert 'easyrsa-runtime)' in runtime
    assert 'easyrsa-install)' in runtime
    assert "conntrack_package=\"conntrack-tools\"" in helper
    assert "sysctl_package=\"procps-ng\"" in helper


def test_openvpn_install_batches_iptables_and_ipset_dependencies() -> None:
    debian = _read_install_script_text("openvpn-install-debian.sh")
    rhel = _read_install_script_text("openvpn-install-rhel.sh")

    debian_source = debian.split("install_source_dependencies() {", 1)[1].split("\n}", 1)[0]
    rhel_source = rhel.split("install_source_dependencies() {", 1)[1].split("\n}", 1)[0]

    assert "conntrack iptables ipset" in debian_source
    assert "conntrack-tools" in rhel_source
    assert "iptables ipset" in rhel_source
    assert "packages+=(openssl findutils coreutils)" in debian_source
    assert "packages+=(openssl findutils coreutils)" in rhel_source
    assert debian_source.count("nicevpn_install_packages apt-get") == 1
    assert rhel_source.count('nicevpn_install_packages "${PM}"') == 1

    # 系统包安装路径也一次性带上防火墙工具；EasyRSA 追加而不是覆盖已有依赖。
    assert "pkgs=(openvpn conntrack iptables ipset procps)" in debian
    assert 'pkgs+=(easy-rsa openssl)' in debian
    assert "packages=(openvpn conntrack-tools iptables ipset procps-ng)" in rhel
    assert "packages+=(curl tar gzip openssl findutils coreutils)" in rhel


def test_dco_development_packages_are_best_effort() -> None:
    debian = _read_install_script_text("openvpn-install-debian.sh")
    rhel = _read_install_script_text("openvpn-install-rhel.sh")

    assert "if nicevpn_install_packages apt-get libnl-3-dev libnl-genl-3-dev; then" in debian
    assert 'if nicevpn_install_packages "${PM}" libnl3-devel; then' in rhel
    for source in (debian, rhel):
        required = source.split("install_source_dependencies() {", 1)[1].split("\n}", 1)[0]
        assert "libnl-genl-3-dev" not in required
        assert "libnl3-devel" not in required



def test_easyrsa_installer_reuses_shared_dependency_helper() -> None:
    script = _read_install_script_text("easyrsa-install.sh")
    helper = (_INSTALL_SCRIPTS_DIR.parent / "common" / "linux-packages.sh").read_text(
        encoding="utf-8"
    )

    assert "nicevpn_ensure_easyrsa_runtime_dependencies" in script
    assert "nicevpn_ensure_easyrsa_install_dependencies" in script
    assert "easyrsa_reuse_existing" in script
    assert "apt-get update" not in script
    assert "nicevpn_package_manager" not in script
    assert "find findutils" in helper
    assert "cp coreutils" in helper



def test_easyrsa_install_shell_syntax() -> None:
    script = _INSTALL_SCRIPTS_DIR / "easyrsa-install.sh"
    result = subprocess.run(
        ["/bin/bash", "-n", str(script)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
