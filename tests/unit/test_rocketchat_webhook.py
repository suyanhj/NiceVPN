# -*- coding: utf-8 -*-
"""Rocket.Chat Incoming Webhook 发送单元测试。"""
from unittest.mock import MagicMock, patch

import pytest

from app.services.notify.rocketchat import (
    COLOR_ALARM,
    COLOR_OK,
    RocketChatSendError,
    attachment_color,
    send_rocketchat_text,
)


@patch("app.services.notify.rocketchat.requests.post")
def test_send_rocketchat_text_success(mock_post: MagicMock) -> None:
    """成功时返回 2xx 状态。"""
    mock_post.return_value = MagicMock(status_code=200, text="ok")
    out = send_rocketchat_text(
        "https://chat.example.com/hooks/id/token",
        ["#test", "@root"],
        "【OpenVPN 配置文件下载】\n用户：alice",
        username="VPN管理",
        icon_emoji=":lock:",
        title="OpenVPN",
    )
    assert out["status_code"] == 200
    payload = mock_post.call_args.kwargs["json"]
    assert payload["channel"] == ["#test", "@root"]
    assert payload["text"] == "【OpenVPN 配置文件下载】"
    assert payload["attachments"][0]["color"] == COLOR_ALARM


def test_attachment_color_recovery() -> None:
    """含恢复的正文使用绿色。"""
    assert attachment_color("告警已恢复") == COLOR_OK


def test_send_rocketchat_text_empty_webhook_raises() -> None:
    """Webhook 为空应拒绝。"""
    with pytest.raises(ValueError, match="Webhook"):
        send_rocketchat_text(
            "",
            "#test",
            "x",
            username="VPN管理",
            icon_emoji=":lock:",
            title="OpenVPN",
        )


@patch("app.services.notify.rocketchat.requests.post")
def test_send_rocketchat_text_http_error_raises(mock_post: MagicMock) -> None:
    """HTTP 非 2xx 应抛错。"""
    mock_post.return_value = MagicMock(status_code=502, text="bad")
    with pytest.raises(RocketChatSendError, match="502"):
        send_rocketchat_text(
            "https://chat.example.com/hooks/id/token",
            "#test",
            "hello",
            username="VPN管理",
            icon_emoji=":lock:",
            title="OpenVPN",
        )


@patch("app.core.config.load_config")
@patch("app.services.notify.plugins.rocketchat_notify.send_rocketchat_text")
def test_notify_dispatches_rocketchat(
    mock_send: MagicMock,
    mock_load: MagicMock,
) -> None:
    """启用且通道为 rocketchat 时调用 Rocket.Chat 发送。"""
    from app.services.notify import send_download_link

    mock_load.return_value = {
        "notify_enabled": True,
        "notify_provider": "rocketchat",
        "rocketchat_webhook": "https://chat.example.com/hooks/id/token",
        "rocketchat_channel": "#test",
    }
    mock_send.return_value = {"status_code": 200}
    assert send_download_link("bob", "https://vpn/dl") is True
    mock_send.assert_called_once()
