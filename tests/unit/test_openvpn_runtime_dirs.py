# -*- coding: utf-8 -*-
"""OpenVPN 运行时目录创建规则单测。"""

from pathlib import Path


def test_ensure_openvpn_runtime_dirs_does_not_create_client_dir(monkeypatch, tmp_path: Path) -> None:
    """初始化阶段不应创建 client 目录；对端客户端配置下发时再按需创建。"""
    from app.core import constants

    base = tmp_path / "openvpn"
    monkeypatch.setattr(constants, "OPENVPN_ETC_DIR", base)
    monkeypatch.setattr(constants, "OPENVPN_SERVER_CONF_DIR", base / "server")
    monkeypatch.setattr(constants, "OPENVPN_CLIENT_CONF_DIR", base / "client")
    monkeypatch.setattr(constants, "OPENVPN_MGMT_DIR", base / "mgmt")
    monkeypatch.setattr(constants, "CCD_DIR", base / "ccd")
    monkeypatch.setattr(constants, "DEVICE_BINDINGS_DIR", base / "mgmt" / "device_bindings")
    monkeypatch.setattr(constants, "OVPN_PROFILES_DIR", base / "mgmt" / "ovpn")
    monkeypatch.setattr(constants, "OPENVPN_LOG_ROOT", base / "log")
    monkeypatch.setattr(constants, "OPENVPN_DAEMON_LOG_DIR", base / "log")

    constants.ensure_openvpn_runtime_dirs()

    assert (base / "server").is_dir()
    assert not (base / "client").exists()
