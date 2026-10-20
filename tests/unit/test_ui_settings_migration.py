# -*- coding: utf-8 -*-
"""系统设置共享页面与核心保存流程测试。"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from box import Box
from nicegui.testing import user_simulation

from app.models.config import SystemConfig
from app.ui.pages.settings import SettingsPage


def _config(**overrides) -> Box:
    data = SystemConfig(
        openvpn_bin="/usr/sbin/openvpn",
        easyrsa_dir="/usr/share/easy-rsa/easyrsa",
        server_ip="vpn.example.com",
        **overrides,
    ).model_dump()
    return Box(data, default_box=True, default_box_attr=None)


def _input(value):
    return SimpleNamespace(value=value)


def _capture_notify(
    monkeypatch: pytest.MonkeyPatch,
) -> list[tuple[str, str]]:
    messages: list[tuple[str, str]] = []
    monkeypatch.setattr(
        "app.ui.pages.settings.safe_notify",
        lambda message, type="info", **_kwargs: messages.append(
            (str(message), type)
        )
        or True,
    )
    return messages


def test_settings_page_uses_shared_header_tabs_and_form_section(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = _config()
    monkeypatch.setattr(
        "app.ui.pages.settings.load_config",
        lambda: cfg,
    )
    page = SettingsPage()

    async def verify() -> None:
        async with user_simulation(page.render) as user:
            response = await user.open("/")
            assert response.status_code == 200
            await user.should_see("系统设置")
            await user.should_see("OpenVPN")
            await user.should_see("服务端")
            await user.should_see("设备绑定")
            await user.should_see("SSH")
            await user.should_see("下载配置")
            await user.should_see("通知")
            await user.should_see("OpenVPN 配置")
            await user.should_see("OpenVPN 可执行文件路径")
            await user.should_see("EasyRSA 可执行脚本路径")
            await user.should_see("验证路径")
            await user.should_see("保存")

    asyncio.run(verify())

    assert page._settings_tabs.value == "openvpn"


def test_validate_paths_checks_both_openvpn_and_easyrsa(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = SettingsPage()
    page.openvpn_input = _input("/opt/openvpn")
    page.easyrsa_input = _input("/opt/easyrsa")
    page.path_status = SimpleNamespace(text="")
    messages = _capture_notify(monkeypatch)

    monkeypatch.setattr(
        "app.ui.pages.settings.validate_custom_path",
        lambda _path: {
            "valid": True,
            "version": "2.7.0",
            "error": None,
        },
    )
    monkeypatch.setattr(
        "app.ui.pages.settings.validate_easyrsa_script_path",
        lambda _path: (True, "ok"),
    )

    assert page._validate_path() is True
    assert "OpenVPN 2.7.0" in page.path_status.text
    assert messages[-1][1] == "positive"

    monkeypatch.setattr(
        "app.ui.pages.settings.validate_easyrsa_script_path",
        lambda _path: (False, "文件不可执行"),
    )
    assert page._validate_path() is False
    assert "文件不可执行" in page.path_status.text
    assert messages[-1][1] == "negative"


def test_save_openvpn_rejects_invalid_and_reports_save_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = _config()
    page = SettingsPage()
    page.openvpn_input = _input("/opt/openvpn")
    page.easyrsa_input = _input("/opt/easyrsa")
    messages = _capture_notify(monkeypatch)

    monkeypatch.setattr(
        "app.ui.pages.settings.load_config",
        lambda: cfg,
    )
    monkeypatch.setattr(
        "app.ui.pages.settings.validate_custom_path",
        lambda _path: {"valid": False, "error": "not openvpn"},
    )
    saved: list[object] = []
    monkeypatch.setattr(
        "app.ui.pages.settings.save_config",
        lambda value: saved.append(value),
    )

    assert page._save_openvpn() is False
    assert saved == []

    monkeypatch.setattr(
        "app.ui.pages.settings.validate_custom_path",
        lambda _path: {"valid": True, "version": "2.7.0"},
    )
    monkeypatch.setattr(
        "app.ui.pages.settings.validate_easyrsa_script_path",
        lambda _path: (True, "ok"),
    )
    monkeypatch.setattr(
        "app.ui.pages.settings.save_config",
        lambda _value: (_ for _ in ()).throw(
            PermissionError("read-only")
        ),
    )

    assert page._save_openvpn() is False
    assert "保存失败" in messages[-1][0]


def _prepare_server_page() -> SettingsPage:
    page = SettingsPage()
    page.server_ip_input = _input("vpn.example.com")
    page.server_port_input = _input(1194)
    page.server_proto_input = _input("udp")
    page.push_lan_routes_input = _input("172.16.22.0/24")
    page.max_clients_input = _input(1024)
    return page


def test_server_save_rolls_back_when_regenerate_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = _config()
    page = _prepare_server_page()
    messages = _capture_notify(monkeypatch)
    saves: list[object] = []
    regen_calls: list[int] = []
    refresh_calls: list[int] = []

    monkeypatch.setattr(
        "app.ui.pages.settings.load_config",
        lambda: cfg,
    )
    monkeypatch.setattr(
        "app.ui.pages.settings.save_config",
        lambda value: saves.append(value),
    )

    def regenerate() -> None:
        regen_calls.append(1)
        if len(regen_calls) == 1:
            raise RuntimeError("render failed")

    monkeypatch.setattr(
        "app.services.openvpn.instance.regenerate_all_server_confs",
        regenerate,
    )

    class Firewall:
        def refresh_vpn_forward_only(self):
            refresh_calls.append(1)

    monkeypatch.setattr(
        "app.services.firewall.rule_service.FirewallRuleService",
        lambda: Firewall(),
    )

    assert page._save_server_connection() is False
    assert len(saves) == 2
    assert len(regen_calls) == 2
    assert refresh_calls == [1]
    assert "回滚" in messages[-1][0]


def test_server_save_success_updates_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = _config()
    page = _prepare_server_page()
    messages = _capture_notify(monkeypatch)
    saved: list[object] = []
    regen_calls: list[int] = []
    refresh_calls: list[int] = []

    monkeypatch.setattr(
        "app.ui.pages.settings.load_config",
        lambda: cfg,
    )
    monkeypatch.setattr(
        "app.ui.pages.settings.save_config",
        lambda value: saved.append(value),
    )
    monkeypatch.setattr(
        "app.services.openvpn.instance.regenerate_all_server_confs",
        lambda: regen_calls.append(1),
    )

    class Firewall:
        def refresh_vpn_forward_only(self):
            refresh_calls.append(1)

    monkeypatch.setattr(
        "app.services.firewall.rule_service.FirewallRuleService",
        lambda: Firewall(),
    )

    assert page._save_server_connection() is True
    assert len(saved) == 1
    assert regen_calls == [1]
    assert refresh_calls == [1]
    assert messages[-1][1] == "positive"


def test_sysctl_permission_error_has_specific_feedback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = SettingsPage()
    messages = _capture_notify(monkeypatch)
    monkeypatch.setattr(
        "app.utils.sysctl_tune.install_vpn_sysctl_dropin",
        lambda: (_ for _ in ()).throw(
            PermissionError("需要 root 权限")
        ),
    )

    assert page._install_sysctl_dropin() is False
    assert messages[-1] == ("需要 root 权限", "negative")


def test_device_bind_failure_rolls_back_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = _config()
    page = SettingsPage()
    page.device_bind_mode_input = _input("strict_hwaddr")
    messages = _capture_notify(monkeypatch)
    saved: list[object] = []

    monkeypatch.setattr(
        "app.ui.pages.settings.load_config",
        lambda: cfg,
    )
    monkeypatch.setattr(
        "app.ui.pages.settings.save_config",
        lambda value: saved.append(value),
    )
    monkeypatch.setattr(
        "app.services.user.device_bind_policy.sync_device_bind_mode_file",
        lambda _mode: (_ for _ in ()).throw(
            OSError("permission denied")
        ),
    )

    assert page._save_device_bind() is False
    assert len(saved) == 2
    assert "回滚" in messages[-1][0]


def test_global_ssh_failure_never_echoes_secret(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    secret_key = "VERY_SECRET_PRIVATE_KEY"
    secret_passphrase = "VERY_SECRET_PASSPHRASE"
    page = SettingsPage()
    page.global_ssh_key_input = _input(secret_key)
    page.global_ssh_passphrase_input = _input(secret_passphrase)
    messages = _capture_notify(monkeypatch)

    monkeypatch.setattr(
        "app.ui.pages.settings.load_config",
        lambda: _config(),
    )
    monkeypatch.setattr(
        "app.ui.pages.settings.build_global_ssh_config",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            ValueError(
                f"bad {secret_key} {secret_passphrase}"
            )
        ),
    )

    with caplog.at_level("ERROR"):
        assert page._save_global_ssh() is False

    combined = "\n".join(message for message, _type in messages)
    combined += "\n" + caplog.text
    assert secret_key not in combined
    assert secret_passphrase not in combined
    assert "配置校验失败" in combined
