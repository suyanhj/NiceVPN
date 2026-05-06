# -*- coding: utf-8 -*-
"""钉钉通道：下载链接推送（注册为 notify_provider=dingtalk）。"""
from __future__ import annotations

from app.services.notify.dingtalk import send_dingtalk_text
from app.services.notify.registry import register_notify_sender
from app.utils.audit_log import AuditLogger


def send_download_link_dingtalk(config, username: str, download_url: str) -> bool:
    """
    通过钉钉机器人发送 .ovpn 下载链接。

    Args:
        config: 系统配置（含 dingtalk_webhook、dingtalk_secret）
        username: VPN 用户名
        download_url: 完整一次性下载 URL

    Returns:
        True 表示钉钉返回成功
    """
    audit = AuditLogger()
    webhook_url = config.get("dingtalk_webhook")
    if not webhook_url:
        audit.log(
            "dingtalk_push",
            "download_link",
            username,
            {"error": "钉钉 Webhook 未配置"},
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
        resp_data = send_dingtalk_text(
            webhook_url, config.get("dingtalk_secret"), text
        )
        if resp_data.get("errcode") == 0:
            audit.log(
                "dingtalk_push",
                "download_link",
                username,
                {"url": download_url},
                "success",
            )
            return True
        audit.log(
            "dingtalk_push",
            "download_link",
            username,
            {
                "errcode": resp_data.get("errcode"),
                "errmsg": resp_data.get("errmsg"),
            },
            "failure",
        )
        return False
    except ValueError as e:
        audit.log(
            "dingtalk_push",
            "download_link",
            username,
            {"error": str(e)},
            "failure",
            error_message=str(e),
        )
        return False
    except Exception as e:
        audit.log(
            "dingtalk_push",
            "download_link",
            username,
            {"error": str(e)},
            "failure",
            error_message=str(e),
        )
        return False


register_notify_sender("dingtalk", send_download_link_dingtalk)
