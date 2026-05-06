# -*- coding: utf-8 -*-
"""OpenVPN 脚本同步占位符替换。"""

from pathlib import Path


def test_sync_replaces_device_bind_log_placeholder(tmp_path: Path) -> None:
    """device-bind.sh 中 BIND_LOG 应写入 constants.DEVICE_BIND_LOG_FILE。"""
    from app.core.constants import DEVICE_BIND_LOG_FILE
    from app.services.openvpn.script_sync import sync_packaged_openvpn_scripts

    sync_packaged_openvpn_scripts(tmp_path)
    sh = tmp_path / "scripts" / "device-bind.sh"
    assert sh.is_file()
    text = sh.read_text(encoding="utf-8")
    assert "__DEVICE_BIND_LOG_FILE__" not in text
    assert DEVICE_BIND_LOG_FILE.as_posix() in text
