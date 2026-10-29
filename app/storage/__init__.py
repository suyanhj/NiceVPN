# -*- coding: utf-8 -*-
"""NiceVPN 结构化管理元数据存储。"""

from app.storage.migration import ensure_storage_ready
from app.storage.sqlite_store import SqliteStore, get_default_store

__all__ = ["SqliteStore", "ensure_storage_ready", "get_default_store"]
