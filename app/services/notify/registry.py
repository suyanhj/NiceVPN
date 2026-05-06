# -*- coding: utf-8 -*-
"""通知通道注册表与统一推送入口。"""
from __future__ import annotations

import logging
from typing import Any, Callable

logger = logging.getLogger(__name__)

# Box 或兼容 dict，由各插件读取所需字段
ConfigLike = Any

DownloadLinkSender = Callable[[ConfigLike, str, str], bool]

_SENDERS: dict[str, DownloadLinkSender] = {}


def register_notify_sender(provider_id: str, fn: DownloadLinkSender) -> None:
    """
    注册「一次性下载链接」推送实现。

    Args:
        provider_id: 与 SystemConfig.notify_provider 对应，例如 dingtalk
        fn: (config, username, download_url) -> 是否发送成功

    Raises:
        ValueError: provider_id 重复注册
    """
    if provider_id in _SENDERS:
        raise ValueError(f"notify sender already registered: {provider_id}")
    _SENDERS[provider_id] = fn


def send_download_link(username: str, download_url: str) -> bool:
    """
    按系统配置选择通道并推送一次性 .ovpn 下载链接。

    仅在管理员点击用户列表「推送下载链接」时调用；不因批量导入等自动触发。

    Args:
        username: VPN 用户名
        download_url: 完整一次性下载 URL

    Returns:
        True 表示已由某通道提交成功；False 表示未发送（未启用、无通道或发送失败）。
    """
    from app.core.config import load_config
    from app.utils.audit_log import AuditLogger

    audit = AuditLogger()
    config = load_config()

    enabled = bool(config.get("notify_enabled"))
    provider_raw = config.get("notify_provider")
    provider = str(provider_raw or "none").strip() or "none"

    if not enabled:
        audit.log(
            "notify_push",
            "download_link",
            username,
            {"error": "通知推送未启用", "provider": provider},
            "failure",
        )
        logger.warning("notify_push skipped: notify_enabled=false user=%s", username)
        return False

    if provider == "none":
        audit.log(
            "notify_push",
            "download_link",
            username,
            {"error": "未选择通知通道"},
            "failure",
        )
        logger.warning("notify_push skipped: notify_provider=none user=%s", username)
        return False

    sender = _SENDERS.get(provider)
    if sender is None:
        audit.log(
            "notify_push",
            "download_link",
            username,
            {"error": f"未知通知通道: {provider}"},
            "failure",
        )
        logger.error("notify_push: unknown notify_provider=%s", provider)
        return False

    return bool(sender(config, username, download_url))


def _register_builtin_plugins() -> None:
    """导入内置插件模块以完成通道注册。"""
    import app.services.notify.plugins.dingtalk_notify  # noqa: F401
    import app.services.notify.plugins.wework_notify  # noqa: F401


_register_builtin_plugins()
