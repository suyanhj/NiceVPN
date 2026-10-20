# -*- coding: utf-8 -*-
"""Rocket.Chat Incoming Webhook 文本发送。"""
from __future__ import annotations

import logging
from typing import Any

import requests

logger = logging.getLogger(__name__)

COLOR_ALARM = "#E74C3C"
COLOR_OK = "#2ECC71"


class RocketChatSendError(Exception):
    """Rocket.Chat 发送失败。"""


def attachment_color(msg: str) -> str:
    """按告警正文判断 attachment 颜色。"""
    lower = msg.lower()
    if "ok" in lower or "recovered" in lower or "恢复" in msg:
        return COLOR_OK
    return COLOR_ALARM


def send_rocketchat_text(
    webhook_url: str,
    channel: str | list[str],
    content: str,
    *,
    username: str,
    icon_emoji: str,
    title: str,
) -> dict[str, Any]:
    """
    通过 Rocket.Chat Incoming Webhook 发送文本。

    Args:
        webhook_url: Incoming Webhook 完整地址
        channel: 频道，单个字符串或多个字符串列表
        content: 正文
        username: 消息显示用户名
        icon_emoji: 消息图标
        title: attachment 标题

    Returns:
        含 status_code 的字典

    Raises:
        ValueError: Webhook 或 channel 为空
        RocketChatSendError: HTTP 非 2xx
    """
    base = (webhook_url or "").strip()
    if not base:
        raise ValueError("Rocket.Chat Webhook URL 不能为空")
    if isinstance(channel, str):
        if not channel.strip():
            raise ValueError("Rocket.Chat channel 不能为空")
    elif not isinstance(channel, list) or not channel:
        raise ValueError("Rocket.Chat channel 必须是字符串或非空列表")
    else:
        for item in channel:
            if not isinstance(item, str) or not item.strip():
                raise ValueError("Rocket.Chat channel 列表项必须是非空字符串")

    text = str(content)
    payload = {
        "channel": channel,
        "username": username,
        "icon_emoji": icon_emoji,
        "text": text.splitlines()[0],
        "attachments": [
            {
                "color": attachment_color(text),
                "title": title,
                "text": text,
            }
        ],
    }
    logger.info("开始发送 Rocket.Chat 通知 channel=%s", channel)
    response = requests.post(
        base,
        json=payload,
        headers={"Content-Type": "application/json"},
        timeout=15,
    )
    if response.status_code < 200 or response.status_code >= 300:
        logger.error(
            "Rocket.Chat 发送失败 status=%s body=%s",
            response.status_code,
            response.text,
        )
        raise RocketChatSendError(
            f"Rocket.Chat 发送失败 status={response.status_code} body={response.text}"
        )
    logger.info("Rocket.Chat 发送成功 status=%s", response.status_code)
    return {"status_code": response.status_code, "body": response.text}
