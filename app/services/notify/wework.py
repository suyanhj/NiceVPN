# -*- coding: utf-8 -*-
"""企业微信群机器人：委托 wechat-work-webhook 客户端发送文本。"""
from __future__ import annotations

import logging
from typing import Any

import wechat_work_webhook

logger = logging.getLogger(__name__)

_QYAPI_WEBHOOK_MARK = "qyapi.weixin.qq.com"
_QYAPI_WEBHOOK_PATH = "/cgi-bin/webhook/send"


def send_wework_text(webhook_url: str, content: str) -> dict[str, Any]:
    """
    向企业微信群机器人发送 text 类型消息。

    Args:
        webhook_url: 群机器人 Webhook 完整地址（含 key 查询参数）
        content: 正文（可含换行）

    Returns:
        企业微信接口 JSON，成功时 errcode 为 0

    Raises:
        ValueError: URL 为空或不符合群机器人地址
        requests.RequestException: 底层 HTTP 失败（库内使用 requests）
    """
    url = (webhook_url or "").strip()
    if not url:
        raise ValueError("企业微信 Webhook URL 不能为空")
    if _QYAPI_WEBHOOK_MARK not in url or _QYAPI_WEBHOOK_PATH not in url:
        raise ValueError(
            "企业微信 Webhook 须为 https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=… 格式"
        )

    client = wechat_work_webhook.connect(url)
    data = client.text(content)
    logger.debug("企业微信 webhook 返回: %s", data)
    return data
