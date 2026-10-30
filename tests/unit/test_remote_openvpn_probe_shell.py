# -*- coding: utf-8 -*-
"""remote_openvpn：探测脚本构造（含自定义 openvpn 路径优先）"""

from pathlib import Path

from app.services.peer_instance import remote_openvpn
from app.services.peer_instance.remote_openvpn import _build_openvpn_probe_shell


def test_probe_shell_no_preferred_only_standard():
    s = _build_openvpn_probe_shell()
    assert s.startswith("set -e\n")
    assert "command -v openvpn" in s
    assert "/usr/sbin/openvpn" in s or "/opt/openvpn/sbin/openvpn" in s


def test_probe_shell_preferred_before_standard_list():
    s = _build_openvpn_probe_shell(preferred_binary="/opt/custom/openvpn")
    assert s.startswith("set -e\n")
    assert "export PREFERRED_BINARY=/opt/custom/openvpn" in s
    preferred_check = 'if [ -n "${PREFERRED_BINARY}" ]; then'
    standard_loop = "while IFS= read -r candidate; do"
    assert preferred_check in s
    assert standard_loop in s
    assert s.index(preferred_check) < s.index(standard_loop)
    assert "command -v openvpn" in s


def test_probe_shell_logic_lives_in_peer_script() -> None:
    script = Path(remote_openvpn._OPENVPN_PROBE_SCRIPT)
    assert script.is_file()
    body = script.read_text(encoding="utf-8")
    assert "probe_path()" in body
    assert "command -v openvpn" in body

    py_source = Path(remote_openvpn.__file__).read_text(encoding="utf-8")
    assert 'lines.append("if command -v openvpn' not in py_source
    assert "probe_path()" not in py_source
