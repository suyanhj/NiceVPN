# -*- coding: utf-8 -*-
"""企业微信 Webhook 发送单元测试。"""
from unittest.mock import MagicMock, patch

import pytest

from app.services.notify.wework import send_wework_text


@patch("app.services.notify.wework.wechat_work_webhook.connect")
def test_send_wework_text_success(mock_connect: MagicMock) -> None:
    """成功时返回 JSON errcode 0。"""
    mock_client = MagicMock()
    mock_client.text.return_value = {"errcode": 0, "errmsg": "ok"}
    mock_connect.return_value = mock_client

    url = "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=testkey"
    out = send_wework_text(url, "hello")
    assert out["errcode"] == 0
    mock_connect.assert_called_once_with(url)
    mock_client.text.assert_called_once_with("hello")


def test_send_wework_text_invalid_url_raises() -> None:
    """非 qyapi 群机器人地址应拒绝。"""
    with pytest.raises(ValueError, match="企业微信"):
        send_wework_text("https://example.com/hook", "x")


@patch("app.core.config.load_config")
@patch("app.services.notify.plugins.wework_notify.send_wework_text")
def test_notify_dispatches_wework(
    mock_send: MagicMock,
    mock_load: MagicMock,
) -> None:
    """启用且通道为 wework 时调用企业微信发送。"""
    from app.services.notify import send_download_link

    mock_load.return_value = {
        "notify_enabled": True,
        "notify_provider": "wework",
        "wework_webhook": "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=y",
    }
    mock_send.return_value = {"errcode": 0}
    assert send_download_link("bob", "https://vpn/dl") is True
    mock_send.assert_called_once()
