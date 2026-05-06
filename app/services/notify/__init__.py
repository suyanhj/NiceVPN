# -*- coding: utf-8 -*-
"""通知子系统：统一入口（导入 registry 即加载内置插件）。"""
from app.services.notify.registry import register_notify_sender, send_download_link

__all__ = ["register_notify_sender", "send_download_link"]
