# -*- coding: utf-8 -*-
"""设备绑定策略：SQLite 为真相源，降权后的 OpenVPN 只读取原子快照。"""
from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
from pathlib import Path

from app.core.constants import (
    DEVICE_BIND_MODE_FILE, DEVICE_BIND_EXEMPTIONS_FILE,
)

logger = logging.getLogger(__name__)
_ALLOWED = frozenset({"weak_log", "weak_fingerprint", "strict_hwaddr"})
_SYNC_LOCK = threading.RLock()


def _write_atomic(path: Path, content: str) -> None:
    """保护并发读者不遇到半成品；模式与快照只能由管理端发布。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_path = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        os.fchmod(fd, 0o644)
        with os.fdopen(fd, "w", encoding="utf-8") as out:
            out.write(content)
        os.replace(temp_path, path)
    finally:
        if os.path.exists(temp_path):
            os.unlink(temp_path)


def build_device_bind_exemptions(store) -> dict[str, str]:
    """直接标签 > 组标签 > 组属性；仅允许 active 用户及 active 组豁免。"""
    from app.storage.repository_helpers import _load_payload

    with store.connection() as conn:
        users = conn.execute(
            "SELECT username, group_id FROM users WHERE status='active' ORDER BY username"
        ).fetchall()
        groups = {
            str(row["id"]): row
            for row in conn.execute("SELECT id,status,payload FROM groups")
        }
        direct = {
            str(row["username"]): str(row["tag_id"])
            for row in conn.execute(
                "SELECT ut.username, MIN(ut.tag_id) AS tag_id FROM user_tags ut "
                "JOIN tags t ON t.id=ut.tag_id WHERE t.device_binding_exempt=1 "
                "GROUP BY ut.username"
            )
        }
        group_tags = {
            str(row["group_id"]): str(row["tag_id"])
            for row in conn.execute(
                "SELECT gt.group_id, MIN(gt.tag_id) AS tag_id FROM group_tags gt "
                "JOIN tags t ON t.id=gt.tag_id WHERE t.device_binding_exempt=1 "
                "GROUP BY gt.group_id"
            )
        }
    allowed: dict[str, str] = {}
    for row in users:
        username, group_id = str(row["username"]), str(row["group_id"] or "")
        group = groups.get(group_id)
        if group is None or str(group["status"]) != "active":
            continue
        if username in direct:
            allowed[username] = "tag:" + direct[username]
        elif group_id in group_tags:
            allowed[username] = "group_tag:" + group_tags[group_id]
        elif _load_payload(str(group["payload"])).get("device_binding_exempt") is True:
            allowed[username] = "group:" + group_id
    return allowed


def sync_device_bind_exemption_snapshot(store=None) -> None:
    """生成 OpenVPN 可读的精简名单；绝不放开 SQLite 或 config.json 权限。"""
    from app.storage.sqlite_store import get_default_store
    if os.name == "nt":
        return
    with _SYNC_LOCK:
        resolved_store = store if store is not None else get_default_store()
        data = {"version": 1, "users": build_device_bind_exemptions(resolved_store)}
        _write_atomic(
            DEVICE_BIND_EXEMPTIONS_FILE,
            json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n",
        )


def refresh_device_bind_exemption_snapshot(store) -> None:
    """数据库提交关联变更后立即重建；全局关闭或仅记录模式无需刷新。"""
    if os.name == "nt":
        return
    from app.core.config import load_config
    config = load_config()
    if not (
        config.get("initialized")
        and config.get("device_bind_exemption_enabled")
        and config.get("device_bind_mode") in {"weak_fingerprint", "strict_hwaddr"}
    ):
        return
    sync_device_bind_exemption_snapshot(store)


def sync_device_bind_mode_file(mode: str, *, exemption_enabled: bool | None = None) -> None:
    """首行保留原有模式格式；第二行提供独立的豁免开关。

    启用时先发布名单，再打开运行时总闸；关闭时保留组、标签配置。
    """
    m = (mode or "").strip()
    if m not in _ALLOWED:
        m = "weak_fingerprint"
    if os.name == "nt":
        logger.debug("Windows 环境跳过设备绑定运行时策略")
        return
    if exemption_enabled is None:
        from app.core.config import load_config
        exemption_enabled = bool(load_config().get("device_bind_exemption_enabled"))
    effective = bool(exemption_enabled and m != "weak_log")
    with _SYNC_LOCK:
        if effective:
            sync_device_bind_exemption_snapshot()
        _write_atomic(
            DEVICE_BIND_MODE_FILE,
            m + "\n" + f"exemptions_enabled={int(effective)}\n",
        )
    logger.info("已同步设备绑定策略到 %s: %s, exempt=%s", DEVICE_BIND_MODE_FILE, m, effective)
