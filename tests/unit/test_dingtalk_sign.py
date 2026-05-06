# -*- coding: utf-8 -*-
"""钉钉推送封装单元测试（委托 DingtalkChatbot）。"""
from unittest.mock import MagicMock, patch

import pytest

from app.services.notify.dingtalk import send_dingtalk_text


@patch("app.services.notify.dingtalk.DingtalkChatbot")
def test_send_dingtalk_text_delegates_to_chatbot(mock_cls: MagicMock) -> None:
    """应构造 DingtalkChatbot 并调用 send_text，返回库返回的字典。"""
    mock_cls.return_value.send_text.return_value = {"errcode": 0, "errmsg": "ok"}
    out = send_dingtalk_text(
        "https://oapi.dingtalk.com/robot/send?access_token=x",
        "SECabc",
        "hello",
    )
    mock_cls.assert_called_once_with(
        "https://oapi.dingtalk.com/robot/send?access_token=x",
        secret="SECabc",
    )
    mock_cls.return_value.send_text.assert_called_once_with(msg="hello", is_at_all=False)
    assert out == {"errcode": 0, "errmsg": "ok"}


@patch("app.services.notify.dingtalk.DingtalkChatbot")
def test_send_dingtalk_text_blank_secret_becomes_none(mock_cls: MagicMock) -> None:
    """空白密钥应传 secret=None，由库按不加签处理。"""
    mock_cls.return_value.send_text.return_value = {"errcode": 0}
    send_dingtalk_text("https://x?a=1", "  \n", "m")
    mock_cls.assert_called_once_with("https://x?a=1", secret=None)


def test_send_dingtalk_text_empty_webhook_raises() -> None:
    with pytest.raises(ValueError, match="不能为空"):
        send_dingtalk_text("", None, "x")
    with pytest.raises(ValueError, match="不能为空"):
        send_dingtalk_text("   ", None, "x")
