# -*- coding: utf-8 -*-
"""NiceVPN SQLite v1 repository。

首版采用“索引列 + 完整 JSON payload”，让现有 Service dict 语义保持稳定，
同时把目录扫描、小文件读取、字段筛选和后续分页下推到 SQLite。
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence

from app.core.constants import SQLITE_DB_FILE
from app.utils.cidr import normalize_cidr

from app.storage.schema_manager import SCHEMA_VERSION, ensure_schema, execute_sql_file
from app.storage.repository_helpers import (
    _now_iso, _dump_payload, _load_payload, _like_pattern,
    _append_multi_text_clause, _normalize_group_payload,
    _peer_index_values, _firewall_index_values,
)
from app.storage.repositories.user_repository import UserRepositoryMixin
from app.storage.repositories.group_repository import GroupRepositoryMixin
from app.storage.repositories.peer_repository import PeerRepositoryMixin
from app.storage.repositories.tag_repository import TagRepositoryMixin
from app.storage.repositories.firewall_repository import FirewallRepositoryMixin


class SqliteStore(
    UserRepositoryMixin, GroupRepositoryMixin,
    PeerRepositoryMixin, TagRepositoryMixin, FirewallRepositoryMixin,
):
    """单机 SQLite repository；每次操作使用短连接，写操作使用 BEGIN IMMEDIATE。"""

    def __init__(self, path: str | Path | None = None):
        configured = path or os.environ.get("NICEVPN_DB_PATH") or SQLITE_DB_FILE
        self.path = Path(configured)
        self._initialized = False
        self._init_lock = threading.RLock()

    def _fix_permissions(self) -> None:
        """数据库包含用户与 SSH 凭据；POSIX 下 DB/WAL/SHM 仅允许 owner 读写。

        WAL/SHM 是 SQLite 运行期瞬时文件。并发短连接可能在 exists() 与 chmod()
        之间删除或重建它们；这种 ENOENT 竞态不应把已经成功的数据查询判成失败。
        真实的权限错误仍然必须抛出。
        """
        if os.name == "nt":
            return
        paths = (
            (self.path, False),
            (Path(str(self.path) + "-wal"), True),
            (Path(str(self.path) + "-shm"), True),
        )
        for path, transient in paths:
            if not path.exists():
                continue
            try:
                os.chmod(path, 0o600)
            except FileNotFoundError:
                if transient:
                    continue
                raise RuntimeError(f"无法设置 SQLite 文件权限为 0600: {path}")
            except OSError as exc:
                # 权限修正失败不能静默暴露敏感数据。
                raise RuntimeError(
                    f"无法设置 SQLite 文件权限为 0600: {path}"
                ) from exc

    def _open(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.path), timeout=5.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("PRAGMA foreign_keys=ON")
        self._fix_permissions()
        return conn

    def initialize(self) -> None:
        if self._initialized:
            return
        with self._init_lock:
            if self._initialized:
                return
            with self._open() as conn:
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("PRAGMA synchronous=NORMAL")
                ensure_schema(
                    conn,
                    migrate_v2=self._migrate_v2_query_indexes,
                    migrate_v3=self._migrate_v3_normalize_group_cidrs,
                    migrate_v4=self._migrate_v4_tags,
                    now_iso=_now_iso,
                )
                self._fix_permissions()
            self._initialized = True

    @staticmethod
    def _migrate_v2_query_indexes(conn: sqlite3.Connection) -> None:
        """补充分页、筛选所需的反规范化索引列，并回填现有 payload。"""

        peer_columns = {
            str(row["name"]) for row in conn.execute("PRAGMA table_info(peers)").fetchall()
        }
        for name, ddl in (
            ("name", "TEXT NOT NULL DEFAULT ''"),
            ("has_lan_cidrs", "INTEGER NOT NULL DEFAULT 0"),
            ("lan_cidrs_text", "TEXT NOT NULL DEFAULT ''"),
            ("center_forward_protocol", "TEXT NOT NULL DEFAULT 'all'"),
            ("center_forward_dest_ip", "TEXT NOT NULL DEFAULT ''"),
            ("center_forward_dest_port", "TEXT NOT NULL DEFAULT ''"),
        ):
            if name not in peer_columns:
                conn.execute(f"ALTER TABLE peers ADD COLUMN {name} {ddl}")

        firewall_columns = {
            str(row["name"])
            for row in conn.execute("PRAGMA table_info(firewall_rules)").fetchall()
        }
        for name, ddl in (
            ("protocol", "TEXT NOT NULL DEFAULT 'all'"),
            ("action", "TEXT NOT NULL DEFAULT 'accept'"),
            ("deployment_target", "TEXT NOT NULL DEFAULT 'center'"),
            ("source_text", "TEXT NOT NULL DEFAULT ''"),
            ("dest_ip", "TEXT NOT NULL DEFAULT ''"),
            ("dest_port", "TEXT NOT NULL DEFAULT ''"),
        ):
            if name not in firewall_columns:
                conn.execute(f"ALTER TABLE firewall_rules ADD COLUMN {name} {ddl}")

        for row in conn.execute("SELECT id, payload FROM peers").fetchall():
            data = _load_payload(str(row["payload"]))
            conn.execute(
                """
                UPDATE peers
                SET name = ?, has_lan_cidrs = ?, lan_cidrs_text = ?,
                    center_forward_protocol = ?, center_forward_dest_ip = ?,
                    center_forward_dest_port = ?
                WHERE id = ?
                """,
                (*_peer_index_values(data), str(row["id"])),
            )

        for row in conn.execute("SELECT id, payload FROM firewall_rules").fetchall():
            data = _load_payload(str(row["payload"]))
            conn.execute(
                """
                UPDATE firewall_rules
                SET protocol = ?, action = ?, deployment_target = ?,
                    source_text = ?, dest_ip = ?, dest_port = ?
                WHERE id = ?
                """,
                (*_firewall_index_values(data), str(row["id"])),
            )

        execute_sql_file(conn, "002_query_indexes.sql")
        conn.execute(
            "INSERT INTO schema_migrations(version, name, applied_at) VALUES (?, ?, ?)",
            (2, "query-pagination-indexes", _now_iso()),
        )

    @contextmanager
    def connection(self, *, write: bool = False) -> Iterator[sqlite3.Connection]:
        self.initialize()
        conn = self._open()
        try:
            if write:
                conn.execute("BEGIN IMMEDIATE")
            yield conn
            if write:
                conn.commit()
        except Exception:
            if write:
                conn.rollback()
            raise
        finally:
            conn.close()
            self._fix_permissions()

    # ---- storage metadata ----

    def get_meta(self, key: str) -> str | None:
        with self.connection() as conn:
            row = conn.execute(
                "SELECT value FROM storage_meta WHERE key = ?", (str(key),)
            ).fetchone()
        return str(row["value"]) if row else None

    def set_meta(self, key: str, value: str) -> None:
        with self.connection(write=True) as conn:
            conn.execute(
                """
                INSERT INTO storage_meta(key, value) VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (str(key), str(value)),
            )

    def schema_version(self) -> int:
        with self.connection() as conn:
            row = conn.execute(
                "SELECT MAX(version) AS version FROM schema_migrations"
            ).fetchone()
        return int(row["version"] or 0)

    @staticmethod
    def _migrate_v3_normalize_group_cidrs(conn: sqlite3.Connection) -> None:
        """规范历史用户组 CIDR，只修正可合法解析的非网络地址写法。"""

        for row in conn.execute("SELECT id, payload FROM groups").fetchall():
            data = _load_payload(str(row["payload"]))
            normalized = _normalize_group_payload(data)
            if normalized == data:
                continue
            conn.execute(
                "UPDATE groups SET payload = ? WHERE id = ?",
                (_dump_payload(normalized), str(row["id"])),
            )

        conn.execute(
            "INSERT INTO schema_migrations(version, name, applied_at) VALUES (?, ?, ?)",
            (3, "normalize-group-cidrs", _now_iso()),
        )

    @staticmethod
    def _migrate_v4_tags(conn: sqlite3.Connection) -> None:
        """标签元数据和用户/组关联；外键级联只删除关联，不删除用户/组。"""
        execute_sql_file(conn, "004_firewall_tags.sql")
        conn.execute(
            "INSERT INTO schema_migrations(version, name, applied_at) VALUES (?, ?, ?)",
            (4, "firewall-tags", _now_iso()),
        )


_default_store: SqliteStore | None = None
_default_store_lock = threading.Lock()


def get_default_store() -> SqliteStore:
    """返回进程级默认 store；首次访问确保 legacy 迁移已完成。"""

    global _default_store
    if _default_store is not None:
        return _default_store
    with _default_store_lock:
        if _default_store is None:
            # 延迟导入避免 sqlite_store <-> migration 循环依赖。
            from app.storage.migration import ensure_storage_ready

            ensure_storage_ready()
            _default_store = SqliteStore()
            _default_store.initialize()
    return _default_store


def reset_default_store_for_tests() -> None:
    """测试专用：清除默认 store 缓存。"""

    global _default_store
    with _default_store_lock:
        _default_store = None
