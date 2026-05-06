# -*- coding: utf-8 -*-
"""remote_openvpn：探测脚本构造（含自定义 openvpn 路径优先）"""

from app.services.peer_instance.remote_openvpn import _build_openvpn_probe_shell


def test_probe_shell_no_preferred_only_standard():
    s = _build_openvpn_probe_shell()
    assert s.startswith("set -e\n")
    assert "command -v openvpn" in s
    assert "/usr/sbin/openvpn" in s or "/opt/openvpn/sbin/openvpn" in s


def test_probe_shell_preferred_before_standard_list():
    s = _build_openvpn_probe_shell(preferred_binary="/opt/custom/openvpn")
    assert s.startswith("set -e\n")
    first_if = [ln for ln in s.split("\n") if ln.startswith("if ")][0]
    assert "/opt/custom/openvpn" in first_if
    assert "command -v openvpn" in s
