# -*- coding: utf-8 -*-
"""钉钉机器人 Webhook 文本发送 — 基于 dingtalkchatbot；下载链接推送见 registry + plugins.dingtalk_notify。"""
import logging

from dingtalkchatbot.chatbot import DingtalkChatbot

logger = logging.getLogger(__name__)


def _normalize_secret(secret: str | None) -> str | None:
    """空白视为未配置；与 DingtalkChatbot 一致，仅当以 SEC 开头的密钥会触发库内加签逻辑。"""
    if secret is None:
        return None
    s = secret.strip()
    return s or None


def send_dingtalk_text(webhook_url: str, secret: str | None, content: str) -> dict:
    """
    通过自定义机器人发送文本消息（走 DingtalkChatbot，含加签与安全频率控制）。

    Args:
        webhook_url: Webhook 完整地址
        secret: 加签密钥，须与钉钉安全设置一致（一般以 SEC 开头）；未启用加签则传 None
        content: 文本正文

    Returns:
        钉钉接口返回的 JSON 字典（含 errcode、errmsg）

    Raises:
        ValueError: Webhook 为空
    """
    base = (webhook_url or "").strip()
    if not base:
        raise ValueError("钉钉 Webhook URL 不能为空")

    bot = DingtalkChatbot(base, secret=_normalize_secret(secret))
    result = bot.send_text(msg=content, is_at_all=False)
    logger.debug("钉钉 send_text 返回: %s", result)
    return result
