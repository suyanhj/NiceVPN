# -*- coding: utf-8 -*-
"""对端安装脚本与 os-release 解析（无 SSH）。"""

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
    ):
        assert (_INSTALL_SCRIPTS_DIR / name).is_file(), name


def test_build_peer_script_debian_ubuntu() -> None:
    s = build_peer_openvpn_install_script("debian", "22.04")
    assert "export DEBIAN_CODENAME=jammy" in s
    assert "export OPENVPN_MIN_VERSION=2.7.0" in s
    assert "version_ge()" in s
    assert "apt install -y" in s and "openvpn" in s
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


def test_systemd_install_entry_reuses_common_helpers() -> None:
    script = _read_install_script_text("openvpn-install-systemd.sh")
    assert "source " in script and "openvpn-install-common.sh" in script
    assert "openvpn_install_server_systemd_from_src" in script
    assert "openvpn_install_server_systemd_override" in script


def test_common_script_has_shared_helpers() -> None:
    common = _read_install_script_text("openvpn-install-common.sh")
    assert "openvpn_download_source()" in common
    assert "easyrsa_download_and_install()" in common
    assert "openvpn_install_server_systemd_from_src()" in common
