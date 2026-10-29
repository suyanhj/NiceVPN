# -*- coding: utf-8 -*-
"""旧 JSON -> SQLite 的一次性启动迁移。"""

from __future__ import annotations

import json
import logging
import os
import shutil
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.core.constants import (
    BACKUPS_DIR,
    FIREWALL_DIR,
    GROUPS_DIR,
    PEERS_DIR,
    SQLITE_DB_FILE,
    USERS_DIR,
)
from app.models.firewall import FirewallRule
from app.models.group import Group
from app.models.peer_instance import PeerInstance
from app.models.user import User
from app.storage.sqlite_store import SCHEMA_VERSION, SqliteStore

logger = logging.getLogger(__name__)

_MIGRATION_LOCK = threading.RLock()
_READY_PATHS: set[str] = set()


@dataclass(frozen=True)
class StorageMigrationResult:
    database: Path
    migrated: bool
    users: int = 0
    groups: int = 0
    peers: int = 0
    firewall_rules: int = 0
    backup_dir: Path | None = None


def _utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _read_json_value(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"读取 legacy JSON 失败: {path}: {exc}") from exc


def _read_json_object(path: Path) -> dict[str, Any]:
    raw = _read_json_value(path)
    if not isinstance(raw, dict):
        raise RuntimeError(f"legacy JSON 顶层必须为 object: {path}")
    return raw


def _legacy_json_files(directory: Path) -> list[Path]:
    if not directory.is_dir():
        return []
    return sorted(p for p in directory.glob("*.json") if p.is_file())


def _has_legacy_json() -> bool:
    return any(
        _legacy_json_files(path)
        for path in (USERS_DIR, GROUPS_DIR, PEERS_DIR, FIREWALL_DIR)
    )


def _import_users(store: SqliteStore) -> int:
    count = 0
    for path in _legacy_json_files(USERS_DIR):
        data = _read_json_object(path)
        try:
            normalized = User(**data).model_dump()
        except Exception as exc:
            raise RuntimeError(f"用户 JSON 校验失败: {path}: {exc}") from exc
        if normalized["username"] != path.stem:
            raise RuntimeError(
                f"用户 JSON 文件名与 username 不一致: {path.name} != {normalized['username']}"
            )
        store.upsert_user(normalized)
        count += 1
    return count


def _import_groups(store: SqliteStore) -> int:
    count = 0
    for path in _legacy_json_files(GROUPS_DIR):
        data = _read_json_object(path)
        try:
            normalized = Group(**data).model_dump()
        except Exception as exc:
            raise RuntimeError(f"用户组 JSON 校验失败: {path}: {exc}") from exc
        if normalized["id"] != path.stem:
            raise RuntimeError(
                f"用户组 JSON 文件名与 id 不一致: {path.name} != {normalized['id']}"
            )
        store.upsert_group(normalized)
        count += 1
    return count


def _import_peers(store: SqliteStore) -> int:
    count = 0
    for path in _legacy_json_files(PEERS_DIR):
        data = _read_json_object(path)
        try:
            normalized = PeerInstance(**data).model_dump()
        except Exception as exc:
            raise RuntimeError(f"对端 JSON 校验失败: {path}: {exc}") from exc
        if normalized["id"] != path.stem:
            raise RuntimeError(
                f"对端 JSON 文件名与 id 不一致: {path.name} != {normalized['id']}"
            )
        store.upsert_peer(normalized)
        count += 1
    return count


def _import_firewall(store: SqliteStore) -> int:
    count = 0
    seen_ids: set[str] = set()
    for path in _legacy_json_files(FIREWALL_DIR):
        raw = _read_json_value(path)
        if isinstance(raw, list):
            rows = raw
        elif isinstance(raw, dict):
            rows = raw.get("rules", [])
        else:
            raise RuntimeError(f"防火墙 JSON 顶层必须为 object/list: {path}")
        if not isinstance(rows, list):
            raise RuntimeError(f"防火墙 JSON rules 必须为数组: {path}")
        owner_id = path.stem
        normalized_rows: list[dict[str, Any]] = []
        for item in rows:
            if not isinstance(item, dict):
                raise RuntimeError(f"防火墙规则必须为 object: {path}")
            candidate = dict(item)
            candidate["owner_id"] = str(candidate.get("owner_id") or owner_id)
            if candidate["owner_id"] != owner_id:
                raise RuntimeError(
                    f"防火墙规则 owner_id 与文件名不一致: {path.name}: {candidate['owner_id']}"
                )
            try:
                normalized = FirewallRule(**candidate).model_dump()
            except Exception as exc:
                raise RuntimeError(
                    f"防火墙规则 JSON 校验失败: {path}: {candidate.get('id', '?')}: {exc}"
                ) from exc
            rid = str(normalized["id"])
            if rid in seen_ids:
                raise RuntimeError(f"防火墙规则 ID 重复: {rid}")
            seen_ids.add(rid)
            normalized_rows.append(normalized)
        store.replace_firewall_owner(owner_id, normalized_rows)
        count += len(normalized_rows)
    return count


def _verify_counts(
    store: SqliteStore,
    *,
    users: int,
    groups: int,
    peers: int,
    firewall_rules: int,
) -> None:
    actual = {
        "users": store.count_users(include_deleted=True),
        "groups": store.count_groups(),
        "peers": store.count_peers(),
        "firewall_rules": store.count_firewall_rules(),
    }
    expected = {
        "users": users,
        "groups": groups,
        "peers": peers,
        "firewall_rules": firewall_rules,
    }
    if actual != expected:
        raise RuntimeError(
            f"SQLite 迁移数量校验失败 expected={expected} actual={actual}"
        )


def _checkpoint(path: Path) -> None:
    conn = sqlite3.connect(str(path), timeout=5.0)
    try:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        conn.close()


def _archive_legacy_files(*, label: str = "storage-migration") -> Path | None:
    """归档 legacy JSON/lock/prepush；其它历史文件（如旧 ovpn）保持原路径。"""

    candidates: list[tuple[Path, Path]] = []
    for source_dir in (USERS_DIR, GROUPS_DIR, PEERS_DIR, FIREWALL_DIR):
        if not source_dir.is_dir():
            continue
        names: set[Path] = set()
        for path in source_dir.glob("*.json"):
            if path.is_file():
                names.add(path)
                lock = Path(str(path) + ".lock")
                if lock.is_file():
                    names.add(lock)
        if source_dir == FIREWALL_DIR:
            for path in source_dir.glob("*.prepush.bak"):
                if path.is_file():
                    names.add(path)
        for path in sorted(names):
            candidates.append((source_dir, path))

    if not candidates:
        return None

    backup_dir = BACKUPS_DIR / f"{label}-{_utc_stamp()}"
    backup_dir.mkdir(parents=True, exist_ok=False)
    for source_dir, path in candidates:
        target_dir = backup_dir / source_dir.name
        target_dir.mkdir(parents=True, exist_ok=True)
        shutil.move(str(path), str(target_dir / path.name))
    return backup_dir


def ensure_storage_ready(
    database: str | Path | None = None,
) -> StorageMigrationResult:
    """确保 SQLite 可用；首次升级时执行 legacy JSON 一次性迁移。"""

    configured = database or os.environ.get("NICEVPN_DB_PATH") or SQLITE_DB_FILE
    db_path = Path(configured)
    key = str(db_path.resolve())

    with _MIGRATION_LOCK:
        if key in _READY_PATHS:
            return StorageMigrationResult(database=db_path, migrated=False)

        if db_path.is_file():
            store = SqliteStore(db_path)
            store.initialize()
            if store.schema_version() != SCHEMA_VERSION:
                raise RuntimeError(
                    f"不支持的 SQLite schema version: {store.schema_version()}"
                )
            backup_dir = None
            if _has_legacy_json():
                try:
                    backup_dir = _archive_legacy_files(
                        label="storage-migration-leftovers"
                    )
                    logger.warning(
                        "检测到 SQLite 已存在但仍有 legacy JSON，已归档且不会参与运行: %s",
                        backup_dir,
                    )
                except OSError as exc:
                    logger.warning(
                        "SQLite 已可用，但归档残留 legacy JSON 失败；运行时不会读取这些 JSON: %s",
                        exc,
                    )
            _READY_PATHS.add(key)
            return StorageMigrationResult(
                database=db_path,
                migrated=False,
                backup_dir=backup_dir,
            )

        db_path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = db_path.with_name(db_path.name + ".migrating")
        for stale in (
            temp_path,
            Path(str(temp_path) + "-wal"),
            Path(str(temp_path) + "-shm"),
        ):
            stale.unlink(missing_ok=True)

        store = SqliteStore(temp_path)
        try:
            store.initialize()
            groups = _import_groups(store)
            users = _import_users(store)
            peers = _import_peers(store)
            firewall_rules = _import_firewall(store)
            _verify_counts(
                store,
                users=users,
                groups=groups,
                peers=peers,
                firewall_rules=firewall_rules,
            )
            store.set_meta("legacy_json_migrated_at", datetime.now(timezone.utc).isoformat())
            store.set_meta("storage_backend", "sqlite")
            _checkpoint(temp_path)
            os.replace(temp_path, db_path)
        except Exception:
            for stale in (
                temp_path,
                Path(str(temp_path) + "-wal"),
                Path(str(temp_path) + "-shm"),
            ):
                stale.unlink(missing_ok=True)
            raise

        backup_dir = None
        try:
            backup_dir = _archive_legacy_files()
        except OSError as exc:
            # 数据库已原子启用；旧 JSON 仅作为残留备份，运行时不会再访问。
            logger.warning(
                "SQLite 迁移成功，但归档 legacy JSON 失败；请稍后手工归档: %s",
                exc,
            )

        _READY_PATHS.add(key)
        logger.info(
            "SQLite 存储已启用 users=%s groups=%s peers=%s firewall_rules=%s backup=%s",
            users,
            groups,
            peers,
            firewall_rules,
            backup_dir or "-",
        )
        return StorageMigrationResult(
            database=db_path,
            migrated=True,
            users=users,
            groups=groups,
            peers=peers,
            firewall_rules=firewall_rules,
            backup_dir=backup_dir,
        )


def reset_storage_ready_for_tests() -> None:
    """测试专用：清除进程内 readiness cache。"""

    with _MIGRATION_LOCK:
        _READY_PATHS.clear()
