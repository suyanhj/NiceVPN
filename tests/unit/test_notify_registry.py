# -*- coding: utf-8 -*-
"""通知注册表与配置迁移单元测试。"""
from unittest.mock import MagicMock, patch

from app.models.config import SystemConfig
from app.services.notify import send_download_link


@patch("app.core.config.load_config")
@patch("app.services.notify.plugins.dingtalk_notify.send_dingtalk_text")
def test_send_download_link_respects_disabled(
    mock_send: MagicMock,
    mock_load: MagicMock,
) -> None:
    """未启用 notify_enabled 时不调用具体通道。"""
    mock_load.return_value = {
        "notify_enabled": False,
        "notify_provider": "dingtalk",
        "dingtalk_webhook": "https://oapi.dingtalk.com/robot/send?access_token=x",
    }
    assert send_download_link("u1", "https://example/dl") is False
    mock_send.assert_not_called()


@patch("app.core.config.load_config")
@patch("app.services.notify.plugins.dingtalk_notify.send_dingtalk_text")
def test_send_download_link_dispatches_dingtalk(
    mock_send: MagicMock,
    mock_load: MagicMock,
) -> None:
    """启用且通道为 dingtalk 时委托钉钉发送。"""
    mock_load.return_value = {
        "notify_enabled": True,
        "notify_provider": "dingtalk",
        "dingtalk_webhook": "https://oapi.dingtalk.com/robot/send?access_token=y",
        "dingtalk_secret": None,
    }
    mock_send.return_value = {"errcode": 0}
    assert send_download_link("alice", "https://vpn/dl") is True
    mock_send.assert_called_once()


def test_legacy_webhook_only_enables_dingtalk() -> None:
    """旧 JSON 仅有 dingtalk_webhook 时迁移为已启用钉钉。"""
    cfg = SystemConfig.model_validate(
        {
            "dingtalk_webhook": "https://oapi.dingtalk.com/robot/send?access_token=z",
        }
    )
    assert cfg.notify_enabled is True
    assert cfg.notify_provider == "dingtalk"
