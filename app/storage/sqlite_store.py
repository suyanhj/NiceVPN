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
from typing import Any, Iterator, Sequence

from app.core.constants import SQLITE_DB_FILE

SCHEMA_VERSION = 1

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    applied_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS storage_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS users (
    username TEXT PRIMARY KEY,
    group_id TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'active',
    created_at TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT '',
    payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_users_group_status
    ON users(group_id, status);
CREATE INDEX IF NOT EXISTS idx_users_status_username
    ON users(status, username COLLATE NOCASE);

CREATE TABLE IF NOT EXISTS groups (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    created_at TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT '',
    payload TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_groups_name
    ON groups(name);
CREATE INDEX IF NOT EXISTS idx_groups_created_at
    ON groups(created_at, id);

CREATE TABLE IF NOT EXISTS peers (
    id TEXT PRIMARY KEY,
    bound_username TEXT NOT NULL,
    center_forward_priority INTEGER NOT NULL DEFAULT 500000,
    center_forward_enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT '',
    payload TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_peers_bound_username
    ON peers(bound_username);
CREATE INDEX IF NOT EXISTS idx_peers_priority
    ON peers(center_forward_priority, id);

CREATE TABLE IF NOT EXISTS firewall_rules (
    id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL,
    owner_type TEXT NOT NULL,
    priority INTEGER NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT '',
    payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_firewall_owner_priority
    ON firewall_rules(owner_id, priority, id);
CREATE INDEX IF NOT EXISTS idx_firewall_priority
    ON firewall_rules(priority, id);
CREATE INDEX IF NOT EXISTS idx_firewall_owner_type
    ON firewall_rules(owner_type, owner_id);
"""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _dump_payload(data: dict[str, Any]) -> str:
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _load_payload(raw: str) -> dict[str, Any]:
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("SQLite payload 必须为 JSON object")
    return value


class SqliteStore:
    """单机 SQLite repository；每次操作使用短连接，写操作使用 BEGIN IMMEDIATE。"""

    def __init__(self, path: str | Path | None = None):
        configured = path or os.environ.get("NICEVPN_DB_PATH") or SQLITE_DB_FILE
        self.path = Path(configured)
        self._initialized = False
        self._init_lock = threading.RLock()

    def _fix_permissions(self) -> None:
        """数据库包含用户与 SSH 凭据；POSIX 下 DB/WAL/SHM 仅允许 owner 读写。"""
        if os.name == "nt":
            return
        for path in (
            self.path,
            Path(str(self.path) + "-wal"),
            Path(str(self.path) + "-shm"),
        ):
            if not path.exists():
                continue
            try:
                os.chmod(path, 0o600)
            except OSError:
                # 权限修正失败不能静默暴露敏感数据。
                raise RuntimeError(f"无法设置 SQLite 文件权限为 0600: {path}")

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
                conn.executescript(_SCHEMA_SQL)
                self._fix_permissions()
                row = conn.execute(
                    "SELECT MAX(version) AS version FROM schema_migrations"
                ).fetchone()
                current = int(row["version"] or 0)
                if current > SCHEMA_VERSION:
                    raise RuntimeError(
                        f"数据库 schema v{current} 高于当前程序支持的 v{SCHEMA_VERSION}"
                    )
                if current < 1:
                    conn.execute(
                        "INSERT INTO schema_migrations(version, name, applied_at) VALUES (?, ?, ?)",
                        (1, "initial-structured-metadata", _now_iso()),
                    )
                conn.commit()
            self._initialized = True

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

    # ---- users ----

    def upsert_user(self, data: dict[str, Any]) -> None:
        username = str(data.get("username") or "").strip()
        if not username:
            raise ValueError("user.username 不能为空")
        payload = dict(data)
        payload["username"] = username
        with self.connection(write=True) as conn:
            conn.execute(
                """
                INSERT INTO users(username, group_id, status, created_at, updated_at, payload)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(username) DO UPDATE SET
                    group_id = excluded.group_id,
                    status = excluded.status,
                    created_at = excluded.created_at,
                    updated_at = excluded.updated_at,
                    payload = excluded.payload
                """,
                (
                    username,
                    str(payload.get("group_id") or ""),
                    str(payload.get("status") or "active"),
                    str(payload.get("created_at") or ""),
                    str(payload.get("updated_at") or ""),
                    _dump_payload(payload),
                ),
            )

    def get_user(self, username: str) -> dict[str, Any] | None:
        with self.connection() as conn:
            row = conn.execute(
                "SELECT payload FROM users WHERE username = ?",
                (str(username),),
            ).fetchone()
        return _load_payload(row["payload"]) if row else None

    def user_exists(self, username: str, *, include_deleted: bool = False) -> bool:
        sql = "SELECT 1 FROM users WHERE username = ?"
        params: list[Any] = [str(username)]
        if not include_deleted:
            sql += " AND status <> 'deleted'"
        sql += " LIMIT 1"
        with self.connection() as conn:
            return conn.execute(sql, params).fetchone() is not None

    def list_users(self, *, include_deleted: bool = False) -> list[dict[str, Any]]:
        where = "" if include_deleted else "WHERE status <> 'deleted'"
        with self.connection() as conn:
            rows = conn.execute(
                f"SELECT payload FROM users {where} ORDER BY username COLLATE NOCASE"
            ).fetchall()
        return [_load_payload(row["payload"]) for row in rows]

    def list_users_page(
        self,
        *,
        offset: int = 0,
        limit: int = 50,
        search: str = "",
        group_ids: Sequence[str] | None = None,
        statuses: Sequence[str] | None = None,
    ) -> tuple[list[dict[str, Any]], int]:
        clauses = ["status <> 'deleted'"]
        params: list[Any] = []
        text = str(search or "").strip()
        if text:
            clauses.append("username LIKE ? ESCAPE '\\'")
            escaped = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            params.append(f"%{escaped}%")
        gids = [str(v) for v in (group_ids or []) if str(v)]
        if gids:
            clauses.append("group_id IN (" + ",".join("?" for _ in gids) + ")")
            params.extend(gids)
        sts = [str(v) for v in (statuses or []) if str(v)]
        if sts:
            clauses.append("status IN (" + ",".join("?" for _ in sts) + ")")
            params.extend(sts)
        where = " AND ".join(clauses)
        with self.connection() as conn:
            total = int(
                conn.execute(
                    f"SELECT COUNT(*) AS n FROM users WHERE {where}", params
                ).fetchone()["n"]
            )
            rows = conn.execute(
                f"""
                SELECT payload FROM users
                WHERE {where}
                ORDER BY username COLLATE NOCASE
                LIMIT ? OFFSET ?
                """,
                [*params, max(1, int(limit)), max(0, int(offset))],
            ).fetchall()
        return [_load_payload(row["payload"]) for row in rows], total

    def list_active_usernames_by_group(self, group_id: str) -> list[str]:
        with self.connection() as conn:
            rows = conn.execute(
                """
                SELECT username FROM users
                WHERE group_id = ? AND status = 'active'
                ORDER BY username
                """,
                (str(group_id),),
            ).fetchall()
        return [str(row["username"]) for row in rows]

    def count_users(self, *, include_deleted: bool = False) -> int:
        where = "" if include_deleted else " WHERE status <> 'deleted'"
        with self.connection() as conn:
            row = conn.execute(f"SELECT COUNT(*) AS n FROM users{where}").fetchone()
        return int(row["n"])

    # ---- groups ----

    def upsert_group(self, data: dict[str, Any]) -> None:
        group_id = str(data.get("id") or "").strip()
        if not group_id:
            raise ValueError("group.id 不能为空")
        payload = dict(data)
        payload["id"] = group_id
        with self.connection(write=True) as conn:
            conn.execute(
                """
                INSERT INTO groups(id, name, status, created_at, updated_at, payload)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    name = excluded.name,
                    status = excluded.status,
                    created_at = excluded.created_at,
                    updated_at = excluded.updated_at,
                    payload = excluded.payload
                """,
                (
                    group_id,
                    str(payload.get("name") or ""),
                    str(payload.get("status") or "active"),
                    str(payload.get("created_at") or ""),
                    str(payload.get("updated_at") or ""),
                    _dump_payload(payload),
                ),
            )

    def get_group(self, group_id: str) -> dict[str, Any] | None:
        with self.connection() as conn:
            row = conn.execute(
                "SELECT payload FROM groups WHERE id = ?", (str(group_id),)
            ).fetchone()
        return _load_payload(row["payload"]) if row else None

    def list_groups(self) -> list[dict[str, Any]]:
        with self.connection() as conn:
            rows = conn.execute(
                "SELECT payload FROM groups ORDER BY created_at, id"
            ).fetchall()
        return [_load_payload(row["payload"]) for row in rows]

    def delete_group(self, group_id: str) -> None:
        with self.connection(write=True) as conn:
            conn.execute("DELETE FROM groups WHERE id = ?", (str(group_id),))

    def group_name_exists(self, name: str, *, exclude_id: str | None = None) -> bool:
        sql = "SELECT 1 FROM groups WHERE name = ?"
        params: list[Any] = [str(name)]
        if exclude_id:
            sql += " AND id <> ?"
            params.append(str(exclude_id))
        sql += " LIMIT 1"
        with self.connection() as conn:
            return conn.execute(sql, params).fetchone() is not None

    def count_groups(self) -> int:
        with self.connection() as conn:
            return int(conn.execute("SELECT COUNT(*) AS n FROM groups").fetchone()["n"])

    # ---- peers ----

    def upsert_peer(self, data: dict[str, Any]) -> None:
        peer_id = str(data.get("id") or "").strip()
        if not peer_id:
            raise ValueError("peer.id 不能为空")
        payload = dict(data)
        payload["id"] = peer_id
        with self.connection(write=True) as conn:
            conn.execute(
                """
                INSERT INTO peers(
                    id, bound_username, center_forward_priority,
                    center_forward_enabled, created_at, updated_at, payload
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    bound_username = excluded.bound_username,
                    center_forward_priority = excluded.center_forward_priority,
                    center_forward_enabled = excluded.center_forward_enabled,
                    created_at = excluded.created_at,
                    updated_at = excluded.updated_at,
                    payload = excluded.payload
                """,
                (
                    peer_id,
                    str(payload.get("bound_username") or ""),
                    int(payload.get("center_forward_priority") or 500_000),
                    1 if bool(payload.get("center_forward_enabled", True)) else 0,
                    str(payload.get("created_at") or ""),
                    str(payload.get("updated_at") or ""),
                    _dump_payload(payload),
                ),
            )

    def get_peer(self, peer_id: str) -> dict[str, Any] | None:
        with self.connection() as conn:
            row = conn.execute(
                "SELECT payload FROM peers WHERE id = ?", (str(peer_id),)
            ).fetchone()
        return _load_payload(row["payload"]) if row else None

    def list_peers(self) -> list[dict[str, Any]]:
        with self.connection() as conn:
            rows = conn.execute(
                "SELECT payload FROM peers ORDER BY created_at, id"
            ).fetchall()
        return [_load_payload(row["payload"]) for row in rows]

    def delete_peer(self, peer_id: str) -> None:
        with self.connection(write=True) as conn:
            conn.execute("DELETE FROM peers WHERE id = ?", (str(peer_id),))

    def list_bound_usernames(self, *, exclude_peer_id: str | None = None) -> set[str]:
        sql = "SELECT bound_username FROM peers WHERE bound_username <> ''"
        params: list[Any] = []
        if exclude_peer_id:
            sql += " AND id <> ?"
            params.append(str(exclude_peer_id))
        with self.connection() as conn:
            rows = conn.execute(sql, params).fetchall()
        return {str(row["bound_username"]) for row in rows if str(row["bound_username"])}

    def count_peers(self) -> int:
        with self.connection() as conn:
            return int(conn.execute("SELECT COUNT(*) AS n FROM peers").fetchone()["n"])

    # ---- firewall rules ----

    @staticmethod
    def _firewall_values(owner_id: str, rule: dict[str, Any]) -> tuple[Any, ...]:
        payload = dict(rule)
        payload["owner_id"] = str(payload.get("owner_id") or owner_id)
        rule_id = str(payload.get("id") or "").strip()
        if not rule_id:
            raise ValueError("firewall rule id 不能为空")
        return (
            rule_id,
            str(payload.get("owner_id") or owner_id),
            str(payload.get("owner_type") or ""),
            int(payload.get("priority") or 0),
            1 if bool(payload.get("enabled", True)) else 0,
            str(payload.get("created_at") or ""),
            str(payload.get("updated_at") or ""),
            _dump_payload(payload),
        )

    def replace_firewall_owner(
        self,
        owner_id: str,
        rules: Sequence[dict[str, Any]],
    ) -> None:
        oid = str(owner_id)
        with self.connection(write=True) as conn:
            conn.execute("DELETE FROM firewall_rules WHERE owner_id = ?", (oid,))
            conn.executemany(
                """
                INSERT INTO firewall_rules(
                    id, owner_id, owner_type, priority, enabled,
                    created_at, updated_at, payload
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [self._firewall_values(oid, rule) for rule in rules],
            )

    def get_firewall_owner(self, owner_id: str) -> list[dict[str, Any]]:
        with self.connection() as conn:
            rows = conn.execute(
                """
                SELECT payload FROM firewall_rules
                WHERE owner_id = ?
                ORDER BY priority, id
                """,
                (str(owner_id),),
            ).fetchall()
        return [_load_payload(row["payload"]) for row in rows]

    def firewall_owner_exists(self, owner_id: str) -> bool:
        with self.connection() as conn:
            row = conn.execute(
                "SELECT 1 FROM firewall_rules WHERE owner_id = ? LIMIT 1",
                (str(owner_id),),
            ).fetchone()
        return row is not None

    def list_firewall_owner_ids(self) -> list[str]:
        with self.connection() as conn:
            rows = conn.execute(
                "SELECT DISTINCT owner_id FROM firewall_rules ORDER BY owner_id"
            ).fetchall()
        return [str(row["owner_id"]) for row in rows]

    def list_firewall_rules(self) -> list[dict[str, Any]]:
        with self.connection() as conn:
            rows = conn.execute(
                "SELECT payload FROM firewall_rules ORDER BY priority, owner_id, id"
            ).fetchall()
        return [_load_payload(row["payload"]) for row in rows]

    def find_firewall_rule_owner(
        self, rule_id: str
    ) -> tuple[str | None, list[dict[str, Any]]]:
        with self.connection() as conn:
            row = conn.execute(
                "SELECT owner_id FROM firewall_rules WHERE id = ?",
                (str(rule_id),),
            ).fetchone()
        if row is None:
            return None, []
        owner_id = str(row["owner_id"])
        return owner_id, self.get_firewall_owner(owner_id)

    def firewall_priority_exists(
        self,
        priority: int,
        *,
        exclude_rule_id: str | None = None,
    ) -> bool:
        sql = "SELECT 1 FROM firewall_rules WHERE priority = ?"
        params: list[Any] = [int(priority)]
        if exclude_rule_id:
            sql += " AND id <> ?"
            params.append(str(exclude_rule_id))
        sql += " LIMIT 1"
        with self.connection() as conn:
            return conn.execute(sql, params).fetchone() is not None

    def count_firewall_rules(self) -> int:
        with self.connection() as conn:
            return int(
                conn.execute("SELECT COUNT(*) AS n FROM firewall_rules").fetchone()["n"]
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
