# -*- coding: utf-8 -*-
"""OpenVPN 脚本同步占位符替换。"""

from pathlib import Path


def test_sync_replaces_device_bind_log_placeholder(tmp_path: Path) -> None:
    """OpenVPN 钩子脚本占位符应替换为 constants 中的日志路径。"""
    from app.core.constants import DEVICE_BIND_LOG_FILE, VPN_SESSION_AUDIT_LOG_FILE
    from app.services.openvpn.script_sync import sync_packaged_openvpn_scripts

    sync_packaged_openvpn_scripts(tmp_path)
    sh = tmp_path / "scripts" / "device-bind.sh"
    disconnect = tmp_path / "scripts" / "vpn-session-disconnect.sh"
    audit_py = tmp_path / "scripts" / "vpn_session_audit.py"
    assert sh.is_file()
    assert disconnect.is_file()
    assert audit_py.is_file()
    text = sh.read_text(encoding="utf-8")
    assert "__DEVICE_BIND_LOG_FILE__" not in text
    assert "__VPN_SESSION_AUDIT_LOG_FILE__" not in text
    assert DEVICE_BIND_LOG_FILE.as_posix() in text
    assert VPN_SESSION_AUDIT_LOG_FILE.as_posix() in text
    disconnect_text = disconnect.read_text(encoding="utf-8")
    assert "__VPN_SESSION_AUDIT_LOG_FILE__" not in disconnect_text
    assert VPN_SESSION_AUDIT_LOG_FILE.as_posix() in disconnect_text
