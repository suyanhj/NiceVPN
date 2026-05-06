# -*- coding: utf-8 -*-
"""对端安装脚本与 os-release 解析（无 SSH）。"""

from app.services.openvpn.detector import detect_distro_family, parse_os_release_text
from app.services.openvpn.installer import (
    _download_file,
    _parse_openvpn_version_parts,
    _render_openvpn_systemd_unit_from_source,
    build_peer_openvpn_install_script,
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


def test_build_peer_script_debian_ubuntu() -> None:
    s = build_peer_openvpn_install_script("debian", "22.04")
    assert "apt-get update" in s
    assert "apt-get install -y openvpn" in s
    assert "build.openvpn.net/debian" in s
    assert "easy-rsa" not in s


def test_build_peer_script_rhel() -> None:
    s = build_peer_openvpn_install_script("rhel", "9")
    assert "openvpn" in s
    assert "dnf" in s or "yum" in s
    assert "info openvpn" in s
    assert "OPENVPN_MIN_TLS_CRYPT_V2='2.5.0'" in s
    assert "低于 ${OPENVPN_MIN_TLS_CRYPT_V2}" in s
    assert "OVPN_URLS" in s
    assert "https://github.com/OpenVPN/openvpn/releases/download/v2.7.0/openvpn-2.7.0.tar.gz" in s
    assert "复用 OpenVPN 源码缓存" in s
    assert "下载 OpenVPN 源码包" in s
    assert "./configure" in s
    assert "/opt/openvpn" in s
    assert "openvpn-client@.service" in s


def test_render_openvpn_systemd_unit_from_source_uses_official_template(tmp_path):
    template_dir = tmp_path / "distro" / "systemd"
    template_dir.mkdir(parents=True)
    (template_dir / "openvpn-server@.service.in").write_text(
        """[Unit]
Description=OpenVPN service for %i
Documentation=https://openvpn.net/openvpn-@OPENVPN_VERSION_MAJOR@-@OPENVPN_VERSION_MINOR@/

[Service]
Type=notify
WorkingDirectory=/etc/openvpn/server
ExecStart=@sbindir@/openvpn --status %t/openvpn-server/status-%i.log --status-version 2 --suppress-timestamps --config %i.conf
ProtectSystem=true

[Install]
WantedBy=multi-user.target
""",
        encoding="utf-8",
    )

    unit = _render_openvpn_systemd_unit_from_source(tmp_path, "v2.7.0")

    assert "Type=notify" in unit
    assert "WorkingDirectory=/etc/openvpn/server" in unit
    assert "ExecStart=/opt/openvpn/sbin/openvpn --suppress-timestamps --config %i.conf" in unit
    assert "ProtectSystem=true" in unit
    assert "ReadWritePaths=/etc/openvpn" in unit
    assert "openvpn-2-7" in unit


def test_parse_openvpn_version_parts():
    assert _parse_openvpn_version_parts("v2.7.0") == ("2", "7")
    assert _parse_openvpn_version_parts("bad") == ("2", "6")


def test_download_file_reuses_existing_archive_cache(monkeypatch, tmp_path):
    """缓存源码包存在且校验通过时，不应再次触发 curl 下载。"""
    archive = tmp_path / "openvpn-2.7.0.tar.gz"
    archive.write_bytes(b"cached")
    commands = []

    def fake_run_command(cmd, on_output, cwd=None):
        commands.append(cmd)

    monkeypatch.setattr("app.services.openvpn.installer._run_command", fake_run_command)
    monkeypatch.setattr("app.services.openvpn.installer._validate_archive_file", lambda path, on_output: None)

    _download_file("https://github.com/OpenVPN/openvpn/releases/download/v2.7.0/openvpn-2.7.0.tar.gz", archive, None)

    assert archive.read_bytes() == b"cached"
    assert not any(cmd and cmd[0] == "curl" for cmd in commands)
