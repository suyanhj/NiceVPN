# -*- coding: utf-8 -*-
"""企业微信通道：下载链接推送（notify_provider=wework）。"""
from __future__ import annotations

import logging

import requests

from app.services.notify.registry import register_notify_sender
from app.services.notify.wework import send_wework_text
from app.utils.audit_log import AuditLogger

logger = logging.getLogger(__name__)


def send_download_link_wework(config, username: str, download_url: str) -> bool:
    """
    通过企业微信群机器人发送 .ovpn 下载链接。

    Args:
        config: 系统配置（含 wework_webhook）
        username: VPN 用户名
        download_url: 完整一次性下载 URL

    Returns:
        True 表示接口返回 errcode==0
    """
    audit = AuditLogger()
    webhook_url = config.get("wework_webhook")
    if not webhook_url:
        audit.log(
            "wework_push",
            "download_link",
            username,
            {"error": "企业微信 Webhook 未配置"},
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
        resp_data = send_wework_text(webhook_url, text)
        if int(resp_data.get("errcode", -1)) == 0:
            audit.log(
                "wework_push",
                "download_link",
                username,
                {"url": download_url},
                "success",
            )
            return True
        audit.log(
            "wework_push",
            "download_link",
            username,
            {
                "errcode": resp_data.get("errcode"),
                "errmsg": resp_data.get("errmsg"),
            },
            "failure",
        )
        logger.warning(
            "企业微信推送业务失败 user=%s errcode=%s errmsg=%s",
            username,
            resp_data.get("errcode"),
            resp_data.get("errmsg"),
        )
        return False
    except ValueError as e:
        audit.log(
            "wework_push",
            "download_link",
            username,
            {"error": str(e)},
            "failure",
            error_message=str(e),
        )
        return False
    except requests.RequestException as e:
        audit.log(
            "wework_push",
            "download_link",
            username,
            {"error": str(e)},
            "failure",
            error_message=str(e),
        )
        logger.warning("企业微信推送 HTTP 失败 user=%s: %s", username, e)
        return False
    except Exception as e:
        audit.log(
            "wework_push",
            "download_link",
            username,
            {"error": str(e)},
            "failure",
            error_message=str(e),
        )
        logger.exception("企业微信推送异常 user=%s", username)
        return False


register_notify_sender("wework", send_download_link_wework)
