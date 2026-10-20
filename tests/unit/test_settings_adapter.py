# -*- coding: utf-8 -*-
"""系统设置纯校验适配层测试。"""

from __future__ import annotations

import os

import pytest

from app.models.config import SystemConfig
from app.ui.adapters.settings import (
    build_device_bind_config,
    build_global_ssh_config,
    build_openvpn_path_config,
    build_server_connection_config,
    validate_easyrsa_script_path,
    validate_server_host,
)


def test_validate_easyrsa_script_path_missing_and_directory(tmp_path) -> None:
    ok, message = validate_easyrsa_script_path("")
    assert ok is False
    assert "请输入" in message

    ok, message = validate_easyrsa_script_path(
        str(tmp_path / "missing")
    )
    assert ok is False
    assert "不存在" in message

    ok, message = validate_easyrsa_script_path(str(tmp_path))
    assert ok is False
    assert "不是文件" in message


def test_validate_easyrsa_script_path_requires_executable(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    script = tmp_path / "easyrsa"
    script.write_text("#!/bin/sh\n", encoding="utf-8")

    monkeypatch.setattr(os, "access", lambda *_args: False)
    ok, message = validate_easyrsa_script_path(str(script))

    assert ok is False
    assert "不可执行" in message

    monkeypatch.setattr(os, "access", lambda *_args: True)
    ok, message = validate_easyrsa_script_path(str(script))
    assert ok is True
    assert "有效" in message


@pytest.mark.parametrize(
    "value",
    [
        "1.2.3.4",
        "2001:db8::1",
        "vpn.example.com",
        "vpn-host_01",
    ],
)
def test_validate_server_host_accepts_ip_or_hostname(value: str) -> None:
    assert validate_server_host(value) == value


@pytest.mark.parametrize(
    "value",
    [
        "",
        "https://vpn.example.com",
        "vpn.example.com/path",
        "vpn.example.com:1194",
        "bad host",
    ],
)
def test_validate_server_host_rejects_invalid_remote(value: str) -> None:
    with pytest.raises(ValueError):
        validate_server_host(value)


def test_build_server_connection_config_normalizes_routes() -> None:
    cfg = build_server_connection_config(
        SystemConfig(),
        server_ip="vpn.example.com",
        port="443",
        proto="TCP",
        push_lan_routes="172.16.22.18/24\n10.10.0.0/16",
        max_clients="2048",
    )

    assert cfg.server_ip == "vpn.example.com"
    assert cfg.port == 443
    assert cfg.proto == "tcp"
    assert cfg.push_lan_routes == [
        "172.16.22.0/24",
        "10.10.0.0/16",
    ]
    assert cfg.max_clients == 2048


@pytest.mark.parametrize(
    ("port", "proto", "routes", "message"),
    [
        (0, "udp", "", "端口范围"),
        (65536, "udp", "", "端口范围"),
        (1194, "sctp", "", "协议"),
        (1194, "udp", "not-cidr", "push_lan_routes"),
        (1194, "udp", "2001:db8::/64", "IPv4"),
    ],
)
def test_build_server_connection_config_rejects_invalid_values(
    port,
    proto,
    routes,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        build_server_connection_config(
            SystemConfig(),
            server_ip="vpn.example.com",
            port=port,
            proto=proto,
            push_lan_routes=routes,
            max_clients=1024,
        )


def test_openvpn_device_and_ssh_builders_preserve_other_config() -> None:
    base = SystemConfig(
        server_ip="vpn.example.com",
        notify_enabled=True,
    )

    path_cfg = build_openvpn_path_config(
        base,
        openvpn_bin="/usr/sbin/openvpn",
        easyrsa_bin="/usr/share/easy-rsa/easyrsa",
    )
    assert path_cfg.server_ip == "vpn.example.com"
    assert path_cfg.notify_enabled is True
    assert path_cfg.openvpn_bin == "/usr/sbin/openvpn"

    device_cfg = build_device_bind_config(
        path_cfg,
        mode="strict_hwaddr",
    )
    assert device_cfg.device_bind_mode == "strict_hwaddr"

    ssh_cfg = build_global_ssh_config(
        device_cfg,
        private_key=" PRIVATE KEY ",
        passphrase=" secret ",
    )
    assert ssh_cfg.global_ssh_private_key == "PRIVATE KEY"
    assert ssh_cfg.global_ssh_private_key_passphrase == "secret"


def test_device_bind_builder_does_not_silently_fallback() -> None:
    with pytest.raises(ValueError, match="策略无效"):
        build_device_bind_config(
            SystemConfig(),
            mode="typo-mode",
        )
