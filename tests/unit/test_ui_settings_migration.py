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
    assert "rocketchat" in page.notify_provider_select.options


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


def test_notification_channel_sections_follow_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = _config(
        notify_enabled=True,
        notify_provider="dingtalk",
        dingtalk_webhook=(
            "https://oapi.dingtalk.com/robot/send?access_token=x"
        ),
    )
    monkeypatch.setattr(
        "app.ui.pages.settings.load_config",
        lambda: cfg,
    )
    page = SettingsPage()

    async def verify() -> None:
        async with user_simulation(page._render_notify_panel) as user:
            await user.open("/")
            assert page._dingtalk_fields.visible is True
            assert page._wework_fields.visible is False
            assert page._rocketchat_fields.visible is False

            page.notify_provider_select.set_value("wework")
            assert page._dingtalk_fields.visible is False
            assert page._wework_fields.visible is True
            assert page._rocketchat_fields.visible is False

            page.notify_provider_select.set_value("rocketchat")
            assert page._dingtalk_fields.visible is False
            assert page._wework_fields.visible is False
            assert page._rocketchat_fields.visible is True
            await user.should_see("Rocket.Chat 参数")
            await user.should_see(
                "发送测试消息（Rocket.Chat）"
            )

    asyncio.run(verify())


def test_proxy_download_test_uses_current_form_order_without_saving(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = SettingsPage()
    page.github_proxy_input = _input(
        "https://first.example/\nhttps://second.example/"
    )
    page.proxy_status = SimpleNamespace(text="")
    messages = _capture_notify(monkeypatch)
    requested: list[str] = []
    saved: list[object] = []

    class Response:
        def __init__(self, status: int):
            self.status_code = status
            self.headers = {"Content-Length": "1"}
            self.closed = False

        def close(self):
            self.closed = True

    def fake_get(url: str, **_kwargs):
        requested.append(url)
        if "first.example" in url:
            return Response(502)
        return Response(206)

    async def fake_io_bound(fn, *args, **kwargs):
        return fn(*args, **kwargs)

    monkeypatch.setattr(
        "app.ui.pages.settings.requests.get",
        fake_get,
    )
    monkeypatch.setattr(
        "app.ui.pages.settings.run.io_bound",
        fake_io_bound,
    )
    monkeypatch.setattr(
        "app.ui.pages.settings.save_config",
        lambda value: saved.append(value),
    )

    assert asyncio.run(page._test_easyrsa_download()) is True
    assert "first.example" in requested[0]
    assert "second.example" in requested[1]
    assert len(requested) == 2
    assert saved == []
    assert "第 2/" in page.proxy_status.text
    assert messages[-1][1] == "positive"


def test_proxy_download_failure_reports_each_candidate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = SettingsPage()
    page.github_proxy_input = _input("https://only.example/")
    page.proxy_status = SimpleNamespace(text="")
    messages = _capture_notify(monkeypatch)

    class Response:
        status_code = 503
        headers = {}

        def close(self):
            pass

    async def fake_io_bound(fn, *args, **kwargs):
        return fn(*args, **kwargs)

    monkeypatch.setattr(
        "app.ui.pages.settings.requests.get",
        lambda *_args, **_kwargs: Response(),
    )
    monkeypatch.setattr(
        "app.ui.pages.settings.run.io_bound",
        fake_io_bound,
    )

    assert asyncio.run(page._test_easyrsa_download()) is False
    assert "1. https://only.example/" in page.proxy_status.text
    assert "HTTP 503" in page.proxy_status.text
    assert "2. https://github.com/" in page.proxy_status.text
    assert messages[-1][1] == "negative"


def test_save_proxy_list_normalizes_without_reordering(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = _config()
    page = SettingsPage()
    page.github_proxy_input = _input(
        "https://second.example\nhttps://first.example/"
    )
    page.proxy_status = SimpleNamespace(text="")
    saved: list[object] = []
    _capture_notify(monkeypatch)

    monkeypatch.setattr(
        "app.ui.pages.settings.load_config",
        lambda: cfg,
    )
    monkeypatch.setattr(
        "app.ui.pages.settings.save_config",
        lambda value: saved.append(value),
    )

    assert page._save_github_proxies() is True
    assert saved[0].github_proxy_urls == [
        "https://second.example/",
        "https://first.example/",
    ]


def test_save_download_base_url_validates_and_allows_auto(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = _config()
    page = SettingsPage()
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

    page.base_url_input = _input("ftp://vpn.example.com")
    assert page._save_base_url() is False
    assert saved == []

    page.base_url_input = _input(
        "https://vpn.example.com/prefix/"
    )
    assert page._save_base_url() is True
    assert (
        saved[-1].download_base_url
        == "https://vpn.example.com/prefix"
    )

    page.base_url_input = _input("")
    assert page._save_base_url() is True
    assert saved[-1].download_base_url is None
    assert "自动解析" in messages[-1][0]


def _prepare_notify_page() -> SettingsPage:
    page = SettingsPage()
    page.notify_enabled_input = _input(True)
    page.notify_provider_select = _input("rocketchat")
    page.webhook_input = _input(
        "https://oapi.dingtalk.com/robot/send?access_token=current"
    )
    page.dingtalk_secret_input = _input("SEC_CURRENT")
    page.wework_webhook_input = _input(
        "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=current"
    )
    page.rocketchat_webhook_input = _input(
        "https://chat.example/hooks/current"
    )
    page.rocketchat_channel_input = _input(
        "#vpn\n#security"
    )
    page.rocketchat_username_input = _input("VPN Bot")
    page.rocketchat_icon_input = _input(":shield:")
    page.rocketchat_title_input = _input("VPN Alert")
    page.notify_status = SimpleNamespace(text="")
    return page


def test_save_notify_supports_rocketchat_and_current_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = _config()
    page = _prepare_notify_page()
    saved: list[object] = []
    messages = _capture_notify(monkeypatch)

    monkeypatch.setattr(
        "app.ui.pages.settings.load_config",
        lambda: cfg,
    )
    monkeypatch.setattr(
        "app.ui.pages.settings.save_config",
        lambda value: saved.append(value),
    )

    assert page._save_notify() is True
    merged = saved[0]
    assert merged.notify_enabled is True
    assert merged.notify_provider == "rocketchat"
    assert merged.rocketchat_webhook == (
        "https://chat.example/hooks/current"
    )
    assert merged.rocketchat_channel == [
        "#vpn",
        "#security",
    ]
    assert merged.rocketchat_username == "VPN Bot"
    assert merged.rocketchat_icon_emoji == ":shield:"
    assert merged.rocketchat_title == "VPN Alert"
    assert messages[-1] == ("通知配置已保存", "positive")


def test_notify_save_requires_provider_when_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = _prepare_notify_page()
    page.notify_provider_select.value = "none"
    saved: list[object] = []
    messages = _capture_notify(monkeypatch)

    monkeypatch.setattr(
        "app.ui.pages.settings.load_config",
        lambda: _config(),
    )
    monkeypatch.setattr(
        "app.ui.pages.settings.save_config",
        lambda value: saved.append(value),
    )

    assert page._save_notify() is False
    assert saved == []
    assert "必须选择通知通道" in messages[-1][0]


def test_notification_tests_use_current_form_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = _prepare_notify_page()
    messages = _capture_notify(monkeypatch)
    calls: dict[str, object] = {}

    async def fake_io_bound(fn, *args, **kwargs):
        return fn(*args, **kwargs)

    monkeypatch.setattr(
        "app.ui.pages.settings.run.io_bound",
        fake_io_bound,
    )

    def ding(url, secret, content):
        calls["ding"] = (url, secret, content)
        return {"errcode": 0}

    def wework(url, content):
        calls["wework"] = (url, content)
        return {"errcode": 0}

    def rocket(
        url,
        channel,
        content,
        *,
        username,
        icon_emoji,
        title,
    ):
        calls["rocket"] = {
            "url": url,
            "channel": channel,
            "content": content,
            "username": username,
            "icon": icon_emoji,
            "title": title,
        }
        return {"status_code": 200}

    monkeypatch.setattr(
        "app.ui.pages.settings.send_dingtalk_text",
        ding,
    )
    monkeypatch.setattr(
        "app.ui.pages.settings.send_wework_text",
        wework,
    )
    monkeypatch.setattr(
        "app.ui.pages.settings.send_rocketchat_text",
        rocket,
    )

    assert asyncio.run(page._test_webhook()) is True
    assert calls["ding"][0].endswith("access_token=current")
    assert calls["ding"][1] == "SEC_CURRENT"

    assert asyncio.run(page._test_wework_webhook()) is True
    assert calls["wework"][0].endswith("key=current")

    assert asyncio.run(page._test_rocketchat_webhook()) is True
    assert calls["rocket"]["channel"] == [
        "#vpn",
        "#security",
    ]
    assert calls["rocket"]["username"] == "VPN Bot"
    assert calls["rocket"]["icon"] == ":shield:"
    assert calls["rocket"]["title"] == "VPN Alert"
    assert messages[-1][1] == "positive"


def test_notification_save_failure_does_not_echo_secret(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    page = _prepare_notify_page()
    secret = "SEC_SUPER_SECRET"
    page.dingtalk_secret_input.value = secret
    messages = _capture_notify(monkeypatch)

    monkeypatch.setattr(
        "app.ui.pages.settings.load_config",
        lambda: _config(),
    )
    monkeypatch.setattr(
        "app.ui.pages.settings.save_config",
        lambda _value: (_ for _ in ()).throw(
            RuntimeError(f"disk failed {secret}")
        ),
    )

    with caplog.at_level("ERROR"):
        assert page._save_notify() is False

    combined = caplog.text + "\n" + "\n".join(
        message for message, _type in messages
    )
    assert secret not in combined
