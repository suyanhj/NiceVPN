# -*- coding: utf-8 -*-
"""Rocket.Chat 通道：下载链接推送（notify_provider=rocketchat）。"""
from __future__ import annotations

import logging

import requests

from app.services.notify.registry import register_notify_sender
from app.services.notify.rocketchat import RocketChatSendError, send_rocketchat_text
from app.utils.audit_log import AuditLogger

logger = logging.getLogger(__name__)


def send_download_link_rocketchat(config, username: str, download_url: str) -> bool:
    """
    通过 Rocket.Chat Incoming Webhook 发送 .ovpn 下载链接。

    Args:
        config: 系统配置（含 rocketchat_webhook、rocketchat_channel 等）
        username: VPN 用户名
        download_url: 完整一次性下载 URL

    Returns:
        True 表示 HTTP 2xx
    """
    audit = AuditLogger()
    webhook_url = config.get("rocketchat_webhook")
    channel = config.get("rocketchat_channel")
    if not webhook_url or not channel:
        audit.log(
            "rocketchat_push",
            "download_link",
            username,
            {"error": "Rocket.Chat Webhook 或 channel 未配置"},
            "failure",
        )
        return False

    text = (
        f"【OpenVPN 配置文件下载】\n"
        f"用户：{username}\n"
        f"下载链接（1小时内有效，仅限下载一次）：\n"
        f"{download_url}"
    )

    try:
        send_rocketchat_text(
            webhook_url,
            channel,
            text,
            username=str(config.get("rocketchat_username") or "VPN管理"),
            icon_emoji=str(config.get("rocketchat_icon_emoji") or ":lock:"),
            title=str(config.get("rocketchat_title") or "OpenVPN"),
        )
        audit.log(
            "rocketchat_push",
            "download_link",
            username,
            {"url": download_url},
            "success",
        )
        return True
    except ValueError as e:
        audit.log(
            "rocketchat_push",
            "download_link",
            username,
            {"error": str(e)},
            "failure",
            error_message=str(e),
        )
        return False
    except (requests.RequestException, RocketChatSendError) as e:
        audit.log(
            "rocketchat_push",
            "download_link",
            username,
            {"error": str(e)},
            "failure",
            error_message=str(e),
        )
        logger.warning("Rocket.Chat 推送失败 user=%s: %s", username, e)
        return False
    except Exception as e:
        audit.log(
            "rocketchat_push",
            "download_link",
            username,
            {"error": str(e)},
            "failure",
            error_message=str(e),
        )
        logger.exception("Rocket.Chat 推送异常 user=%s", username)
        return False


register_notify_sender("rocketchat", send_download_link_rocketchat)
