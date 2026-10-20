# -*- coding: utf-8 -*-
"""系统设置纯校验适配层测试。"""

from __future__ import annotations

import os

import pytest

from app.models.config import SystemConfig
from app.ui.adapters.settings import (
    build_device_bind_config,
    build_download_config,
    build_github_candidate_urls,
    build_global_ssh_config,
    build_notify_config,
    build_openvpn_path_config,
    build_server_connection_config,
    normalize_download_base_url,
    parse_github_proxy_urls,
    parse_rocketchat_channels,
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


def test_proxy_parser_preserves_order_normalizes_and_deduplicates() -> None:
    proxies = parse_github_proxy_urls(
        """
https://b.example/proxy
https://a.example/
https://b.example/proxy/
"""
    )

    assert proxies == [
        "https://b.example/proxy/",
        "https://a.example/",
    ]

    candidates = build_github_candidate_urls(
        "https://github.com/OpenVPN/easy-rsa/releases/x.tgz",
        proxies,
    )
    assert candidates == [
        "https://b.example/proxy/https://github.com/OpenVPN/easy-rsa/releases/x.tgz",
        "https://a.example/https://github.com/OpenVPN/easy-rsa/releases/x.tgz",
        "https://github.com/OpenVPN/easy-rsa/releases/x.tgz",
    ]


@pytest.mark.parametrize(
    "text",
    [
        "",
        "ftp://proxy.example/",
        "https://user:pass@proxy.example/",
        "https://proxy.example/?x=1",
    ],
)
def test_proxy_parser_rejects_invalid_prefixes(text: str) -> None:
    with pytest.raises(ValueError):
        parse_github_proxy_urls(text)


def test_download_base_url_blank_and_normalization() -> None:
    assert normalize_download_base_url("") is None
    assert (
        normalize_download_base_url(
            "https://vpn.example.com/prefix/"
        )
        == "https://vpn.example.com/prefix"
    )

    cfg = build_download_config(
        SystemConfig(),
        download_base_url="https://vpn.example.com/prefix/",
    )
    assert (
        cfg.download_base_url
        == "https://vpn.example.com/prefix"
    )


@pytest.mark.parametrize(
    "value",
    [
        "vpn.example.com",
        "ftp://vpn.example.com",
        "https://user:pass@vpn.example.com",
        "https://vpn.example.com/?token=x",
        "https://vpn.example.com/#frag",
    ],
)
def test_download_base_url_rejects_unsafe_values(value: str) -> None:
    with pytest.raises(ValueError):
        normalize_download_base_url(value)


def test_parse_rocketchat_channels_supports_multi_value() -> None:
    assert parse_rocketchat_channels("") is None
    assert parse_rocketchat_channels("#ops") == "#ops"
    assert parse_rocketchat_channels(
        "#ops, #vpn\n#ops\n#security"
    ) == ["#ops", "#vpn", "#security"]


def test_notify_config_accepts_webhook_queries_and_rocketchat() -> None:
    cfg = build_notify_config(
        SystemConfig(),
        enabled=True,
        provider="dingtalk",
        dingtalk_webhook=(
            "https://oapi.dingtalk.com/robot/send?access_token=abc"
        ),
        dingtalk_secret="SEC123",
        wework_webhook=(
            "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=xyz"
        ),
        rocketchat_webhook="https://chat.example/hooks/abc",
        rocketchat_channel="#vpn",
        rocketchat_username="VPN Bot",
        rocketchat_icon_emoji=":lock:",
        rocketchat_title="OpenVPN",
    )
    assert cfg.notify_provider == "dingtalk"
    assert cfg.dingtalk_webhook.endswith("access_token=abc")

    rocket = build_notify_config(
        cfg,
        enabled=True,
        provider="rocketchat",
        dingtalk_webhook=cfg.dingtalk_webhook or "",
        dingtalk_secret=cfg.dingtalk_secret or "",
        wework_webhook=cfg.wework_webhook or "",
        rocketchat_webhook="https://chat.example/hooks/abc",
        rocketchat_channel="#vpn\n#security",
        rocketchat_username="VPN Bot",
        rocketchat_icon_emoji=":shield:",
        rocketchat_title="VPN Alert",
    )
    assert rocket.notify_provider == "rocketchat"
    assert rocket.rocketchat_channel == ["#vpn", "#security"]
    assert rocket.rocketchat_username == "VPN Bot"
    assert rocket.rocketchat_icon_emoji == ":shield:"
    assert rocket.rocketchat_title == "VPN Alert"


@pytest.mark.parametrize(
    ("provider", "kwargs", "message"),
    [
        ("none", {}, "必须选择通知通道"),
        ("dingtalk", {}, "必须填写 Webhook"),
        ("wework", {}, "必须填写 Webhook"),
        (
            "rocketchat",
            {"rocketchat_webhook": "https://chat.example/hooks/a"},
            "必须填写频道",
        ),
    ],
)
def test_notify_config_requires_active_provider_fields(
    provider: str,
    kwargs: dict,
    message: str,
) -> None:
    data = {
        "enabled": True,
        "provider": provider,
        "dingtalk_webhook": "",
        "dingtalk_secret": "",
        "wework_webhook": "",
        "rocketchat_webhook": "",
        "rocketchat_channel": "",
        "rocketchat_username": "",
        "rocketchat_icon_emoji": "",
        "rocketchat_title": "",
    }
    data.update(kwargs)
    with pytest.raises(ValueError, match=message):
        build_notify_config(SystemConfig(), **data)
