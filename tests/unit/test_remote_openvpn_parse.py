# -*- coding: utf-8 -*-
"""remote_openvpn 解析逻辑单测（无 SSH）。"""

import io

import pytest
from paramiko import RSAKey

from app.services.peer_instance import remote_openvpn as ro


def test_parse_probe_stdout_bin_and_version() -> None:
    out = "BIN:/usr/sbin/openvpn\nOpenVPN 2.6.3 x86_64-pc-linux-gnu\n"
    b, v = ro._parse_probe_stdout(out)
    assert b == "/usr/sbin/openvpn"
    assert v == "2.6.3"


def test_parse_probe_stdout_quoted_bin() -> None:
    out = 'BIN:"/opt/openvpn/sbin/openvpn"\nOpenVPN 2.5.9\n'
    b, v = ro._parse_probe_stdout(out)
    assert b == "/opt/openvpn/sbin/openvpn"
    assert v == "2.5.9"


def test_parse_probe_stdout_no_version_line() -> None:
    out = "BIN:/usr/bin/openvpn\n"
    b, v = ro._parse_probe_stdout(out)
    assert b == "/usr/bin/openvpn"
    assert v is None


def test_parse_remote_os_release() -> None:
    text = """
NAME="Ubuntu"
VERSION="22.04.3 LTS (Jammy Jellyfish)"
ID=ubuntu
PRETTY_NAME="Ubuntu 22.04.3 LTS"
"""
    d = ro._parse_remote_os_release(text)
    assert d["id"] == "ubuntu"
    assert d["pretty_name"] == "Ubuntu 22.04.3 LTS"
    assert d["name"] == "Ubuntu"


def test_load_private_key_from_pem_rsa_roundtrip() -> None:
    key = RSAKey.generate(2048)
    buf = io.StringIO()
    key.write_private_key(buf)
    buf.seek(0)
    pem = buf.read()
    loaded = ro.load_private_key_from_pem(pem, None)
    assert loaded.get_fingerprint() == key.get_fingerprint()


def test_load_private_key_from_pem_invalid() -> None:
    with pytest.raises(ValueError, match="无法解析|为空"):
        ro.load_private_key_from_pem("not a pem at all")
