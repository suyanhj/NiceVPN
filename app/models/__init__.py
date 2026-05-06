# -*- coding: utf-8 -*-
"""Pydantic v2 数据模型（惰性导出，避免 import app.models.config 时拉全量子模块）"""
from __future__ import annotations

from typing import Any

__all__ = [
    "SystemConfig",
    "Group",
    "User",
    "DeviceBinding",
    "FirewallRule",
    "Certificate",
    "DownloadLink",
    "AuditEntry",
]


def __getattr__(name: str) -> Any:
    if name == "SystemConfig":
        from app.models.config import SystemConfig

        return SystemConfig
    if name == "Group":
        from app.models.group import Group

        return Group
    if name == "User":
        from app.models.user import User

        return User
    if name == "DeviceBinding":
        from app.models.device import DeviceBinding

        return DeviceBinding
    if name == "FirewallRule":
        from app.models.firewall import FirewallRule

        return FirewallRule
    if name == "Certificate":
        from app.models.cert import Certificate

        return Certificate
    if name == "DownloadLink":
        from app.models.download_link import DownloadLink

        return DownloadLink
    if name == "AuditEntry":
        from app.models.audit import AuditEntry

        return AuditEntry
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
