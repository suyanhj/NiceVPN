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

SCHEMA_VERSION = 2

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


def _like_pattern(value: object, *, mode: str = "contains") -> str:
    """生成带 ESCAPE '\\' 的 LIKE 参数。"""

    text = str(value or "")
    escaped = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    if mode == "equals":
        return escaped
    if mode == "startsWith":
        return f"{escaped}%"
    if mode == "endsWith":
        return f"%{escaped}"
    return f"%{escaped}%"


def _peer_index_values(data: dict[str, Any]) -> tuple[Any, ...]:
    lan_cidrs = [
        str(value).strip()
        for value in (data.get("lan_cidrs") or [])
        if str(value).strip()
    ]
    return (
        str(data.get("name") or ""),
        1 if lan_cidrs else 0,
        ",".join(lan_cidrs),
        str(data.get("center_forward_protocol") or "all").strip().lower() or "all",
        str(data.get("center_forward_dest_ip") or "").strip(),
        str(data.get("center_forward_dest_port") or "").strip(),
    )


def _firewall_index_values(data: dict[str, Any]) -> tuple[Any, ...]:
    source_subnet = str(data.get("source_subnet") or "").strip()
    source_ips = [
        str(value).strip()
        for value in (data.get("source_ips") or [])
        if str(value).strip()
    ]
    source_text = source_subnet or ",".join(source_ips)
    return (
        str(data.get("protocol") or "all").strip().lower() or "all",
        str(data.get("action") or "accept").strip().lower() or "accept",
        str(data.get("deployment_target") or "center").strip().lower() or "center",
        source_text,
        str(data.get("dest_ip") or "").strip(),
        str(data.get("dest_port") or "").strip(),
    )


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
                check = conn.execute("PRAGMA quick_check").fetchone()
                if check is None or str(check[0]).lower() != "ok":
                    raise RuntimeError(
                        f"SQLite quick_check 失败: {check[0] if check else 'no result'}"
                    )
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
                    current = 1
                if current < 2:
                    self._migrate_v2_query_indexes(conn)
                    current = 2
                conn.commit()
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

        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_peers_center_enabled_priority "
            "ON peers(has_lan_cidrs, center_forward_enabled, center_forward_priority, id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_firewall_filter "
            "ON firewall_rules(enabled, protocol, action, owner_type, owner_id)"
        )
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
        search_mode: str = "contains",
        group_ids: Sequence[str] | None = None,
        statuses: Sequence[str] | None = None,
        sort_by: str = "username",
        sort_desc: bool = False,
    ) -> tuple[list[dict[str, Any]], int]:
        """按数据库条件分页读取用户；返回当前页和过滤后的总数。"""

        clauses = ["u.status <> 'deleted'"]
        params: list[Any] = []
        text = str(search or "").strip()
        if text:
            mode = str(search_mode or "contains")
            if mode in {"notContains", "notEqual"}:
                operator = "NOT LIKE" if mode == "notContains" else "<>"
            else:
                operator = "LIKE" if mode != "equals" else "="
            clauses.append(f"u.username {operator} ? ESCAPE '\\'")
            params.append(
                _like_pattern(
                    text,
                    mode=(
                        "contains"
                        if mode == "notContains"
                        else "equals"
                        if mode == "notEqual"
                        else mode
                    ),
                )
            )
        gids = [str(v) for v in (group_ids or []) if str(v)]
        if gids:
            clauses.append("u.group_id IN (" + ",".join("?" for _ in gids) + ")")
            params.extend(gids)
        sts = [str(v) for v in (statuses or []) if str(v)]
        if sts:
            clauses.append("u.status IN (" + ",".join("?" for _ in sts) + ")")
            params.extend(sts)

        sort_key = str(sort_by or "username")
        sort_sql = {
            "username": "u.username COLLATE NOCASE",
            "account_status": "u.status",
            "status": "u.status",
            "group": "g.name COLLATE NOCASE",
            "group_id": "u.group_id",
            "created_at": "u.created_at",
        }.get(sort_key, "u.username COLLATE NOCASE")
        direction = "DESC" if sort_desc else "ASC"
        where = " AND ".join(clauses)

        with self.connection() as conn:
            total = int(
                conn.execute(
                    f"""
                    SELECT COUNT(*) AS n
                    FROM users AS u
                    LEFT JOIN groups AS g ON g.id = u.group_id
                    WHERE {where}
                    """,
                    params,
                ).fetchone()["n"]
            )
            rows = conn.execute(
                f"""
                SELECT u.payload
                FROM users AS u
                LEFT JOIN groups AS g ON g.id = u.group_id
                WHERE {where}
                ORDER BY {sort_sql} {direction}, u.username COLLATE NOCASE ASC
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

    def list_users_by_group(
        self,
        group_id: str,
        *,
        include_deleted: bool = False,
    ) -> list[dict[str, Any]]:
        sql = "SELECT payload FROM users WHERE group_id = ?"
        params: list[Any] = [str(group_id)]
        if not include_deleted:
            sql += " AND status <> 'deleted'"
        sql += " ORDER BY username COLLATE NOCASE"
        with self.connection() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [_load_payload(row["payload"]) for row in rows]

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

    def list_groups_page(
        self,
        *,
        offset: int = 0,
        limit: int = 50,
        search: str = "",
        statuses: Sequence[str] | None = None,
        sort_by: str = "created_at",
        sort_desc: bool = False,
    ) -> tuple[list[dict[str, Any]], int]:
        clauses = ["1 = 1"]
        params: list[Any] = []
        text = str(search or "").strip()
        if text:
            pattern = _like_pattern(text)
            clauses.append("(name LIKE ? ESCAPE '\\' OR id LIKE ? ESCAPE '\\')")
            params.extend([pattern, pattern])
        sts = [str(v) for v in (statuses or []) if str(v)]
        if sts:
            clauses.append("status IN (" + ",".join("?" for _ in sts) + ")")
            params.extend(sts)
        sort_sql = {
            "created_at": "created_at",
            "name": "name COLLATE NOCASE",
            "status": "status",
            "id": "id",
        }.get(str(sort_by or "created_at"), "created_at")
        direction = "DESC" if sort_desc else "ASC"
        where = " AND ".join(clauses)
        with self.connection() as conn:
            total = int(
                conn.execute(
                    f"SELECT COUNT(*) AS n FROM groups WHERE {where}", params
                ).fetchone()["n"]
            )
            rows = conn.execute(
                f"""
                SELECT payload FROM groups
                WHERE {where}
                ORDER BY {sort_sql} {direction}, id ASC
                LIMIT ? OFFSET ?
                """,
                [*params, max(1, int(limit)), max(0, int(offset))],
            ).fetchall()
        return [_load_payload(row["payload"]) for row in rows], total

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
                    center_forward_enabled, created_at, updated_at, payload,
                    name, has_lan_cidrs, lan_cidrs_text, center_forward_protocol,
                    center_forward_dest_ip, center_forward_dest_port
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    bound_username = excluded.bound_username,
                    center_forward_priority = excluded.center_forward_priority,
                    center_forward_enabled = excluded.center_forward_enabled,
                    created_at = excluded.created_at,
                    updated_at = excluded.updated_at,
                    payload = excluded.payload,
                    name = excluded.name,
                    has_lan_cidrs = excluded.has_lan_cidrs,
                    lan_cidrs_text = excluded.lan_cidrs_text,
                    center_forward_protocol = excluded.center_forward_protocol,
                    center_forward_dest_ip = excluded.center_forward_dest_ip,
                    center_forward_dest_port = excluded.center_forward_dest_port
                """,
                (
                    peer_id,
                    str(payload.get("bound_username") or ""),
                    int(payload.get("center_forward_priority") or 500_000),
                    1 if bool(payload.get("center_forward_enabled", True)) else 0,
                    str(payload.get("created_at") or ""),
                    str(payload.get("updated_at") or ""),
                    _dump_payload(payload),
                    *_peer_index_values(payload),
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

    def list_peers_page(
        self,
        *,
        offset: int = 0,
        limit: int = 50,
        search: str = "",
        sort_by: str = "created_at",
        sort_desc: bool = False,
    ) -> tuple[list[dict[str, Any]], int]:
        clauses = ["1 = 1"]
        params: list[Any] = []
        text = str(search or "").strip()
        if text:
            pattern = _like_pattern(text)
            clauses.append(
                "(id LIKE ? ESCAPE '\\' OR name LIKE ? ESCAPE '\\' "
                "OR bound_username LIKE ? ESCAPE '\\' OR lan_cidrs_text LIKE ? ESCAPE '\\')"
            )
            params.extend([pattern, pattern, pattern, pattern])
        sort_sql = {
            "created_at": "created_at",
            "name": "name COLLATE NOCASE",
            "bound_username": "bound_username COLLATE NOCASE",
            "center_forward_priority": "center_forward_priority",
            "id": "id",
        }.get(str(sort_by or "created_at"), "created_at")
        direction = "DESC" if sort_desc else "ASC"
        where = " AND ".join(clauses)
        with self.connection() as conn:
            total = int(
                conn.execute(
                    f"SELECT COUNT(*) AS n FROM peers WHERE {where}", params
                ).fetchone()["n"]
            )
            rows = conn.execute(
                f"""
                SELECT payload FROM peers
                WHERE {where}
                ORDER BY {sort_sql} {direction}, id ASC
                LIMIT ? OFFSET ?
                """,
                [*params, max(1, int(limit)), max(0, int(offset))],
            ).fetchall()
        return [_load_payload(row["payload"]) for row in rows], total

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
            *_firewall_index_values(payload),
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
                    created_at, updated_at, payload,
                    protocol, action, deployment_target, source_text, dest_ip, dest_port
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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

    @staticmethod
    def _unified_firewall_cte() -> str:
        return """
            WITH unified AS (
                SELECT
                    'json' AS row_kind,
                    id AS source_id,
                    id AS row_id,
                    owner_id,
                    owner_type,
                    priority,
                    enabled,
                    protocol,
                    action,
                    deployment_target,
                    source_text,
                    dest_ip,
                    dest_port,
                    payload,
                    CASE
                        WHEN deployment_target = 'peer' THEN 'peer_marker'
                        ELSE 'center'
                    END AS kind_key
                FROM firewall_rules

                UNION ALL

                SELECT
                    'center_peer' AS row_kind,
                    id AS source_id,
                    '__center_peer__' || id AS row_id,
                    id AS owner_id,
                    'peer' AS owner_type,
                    center_forward_priority AS priority,
                    center_forward_enabled AS enabled,
                    center_forward_protocol AS protocol,
                    'accept' AS action,
                    'center' AS deployment_target,
                    lan_cidrs_text AS source_text,
                    center_forward_dest_ip AS dest_ip,
                    center_forward_dest_port AS dest_port,
                    payload,
                    'center_peer' AS kind_key
                FROM peers
                WHERE has_lan_cidrs = 1
            )
        """

    def list_firewall_unified_page(
        self,
        *,
        offset: int = 0,
        limit: int = 50,
        search: str = "",
        owner_ids: Sequence[str] | None = None,
        owner_types: Sequence[str] | None = None,
        enabled_values: Sequence[bool | int] | None = None,
        protocols: Sequence[str] | None = None,
        actions: Sequence[str] | None = None,
        kind_keys: Sequence[str] | None = None,
        sort_by: str = "priority",
        sort_desc: bool = False,
    ) -> tuple[list[dict[str, Any]], int]:
        """分页读取中心规则与 Peer 中心放行虚拟行的统一有序集合。"""

        clauses = ["1 = 1"]
        params: list[Any] = []

        text = str(search or "").strip()
        if text:
            pattern = _like_pattern(text)
            clauses.append(
                "("
                "row_id LIKE ? ESCAPE '\\' OR "
                "source_text LIKE ? ESCAPE '\\' OR "
                "dest_ip LIKE ? ESCAPE '\\' OR "
                "dest_port LIKE ? ESCAPE '\\'"
                ")"
            )
            params.extend([pattern, pattern, pattern, pattern])

        def add_in(column: str, values: Sequence[Any] | None) -> None:
            normalized = [value for value in (values or []) if str(value) != ""]
            if not normalized:
                return
            clauses.append(
                f"{column} IN (" + ",".join("?" for _ in normalized) + ")"
            )
            params.extend(normalized)

        add_in("owner_id", [str(v) for v in (owner_ids or [])])
        add_in("owner_type", [str(v) for v in (owner_types or [])])
        if enabled_values:
            add_in("enabled", [1 if bool(v) else 0 for v in enabled_values])
        add_in(
            "protocol",
            [str(v).strip().lower() for v in (protocols or []) if str(v).strip()],
        )
        add_in(
            "action",
            [str(v).strip().lower() for v in (actions or []) if str(v).strip()],
        )
        add_in(
            "kind_key",
            [str(v).strip() for v in (kind_keys or []) if str(v).strip()],
        )

        sort_sql = {
            "id": "row_id COLLATE NOCASE",
            "owner_label": "owner_id COLLATE NOCASE",
            "owner_id": "owner_id COLLATE NOCASE",
            "kind_label": "kind_key",
            "status_label": "enabled",
            "enabled": "enabled",
            "protocol": "protocol COLLATE NOCASE",
            "source": "source_text COLLATE NOCASE",
            "dest": "dest_ip COLLATE NOCASE",
            "dest_port": "dest_port COLLATE NOCASE",
            "action_label": "action COLLATE NOCASE",
            "action": "action COLLATE NOCASE",
            "priority": "priority",
        }.get(str(sort_by or "priority"), "priority")
        direction = "DESC" if sort_desc else "ASC"
        where = " AND ".join(clauses)
        cte = self._unified_firewall_cte()

        with self.connection() as conn:
            total = int(
                conn.execute(
                    f"{cte} SELECT COUNT(*) AS n FROM unified WHERE {where}",
                    params,
                ).fetchone()["n"]
            )
            rows = conn.execute(
                f"""
                {cte}
                SELECT
                    row_kind, source_id, row_id, owner_id, owner_type,
                    priority, enabled, protocol, action, deployment_target,
                    source_text, dest_ip, dest_port, kind_key, payload
                FROM unified
                WHERE {where}
                ORDER BY {sort_sql} {direction}, priority ASC, row_id ASC
                LIMIT ? OFFSET ?
                """,
                [*params, max(1, int(limit)), max(0, int(offset))],
            ).fetchall()

        records: list[dict[str, Any]] = []
        for row in rows:
            records.append(
                {
                    "row_kind": str(row["row_kind"]),
                    "source_id": str(row["source_id"]),
                    "row_id": str(row["row_id"]),
                    "owner_id": str(row["owner_id"]),
                    "owner_type": str(row["owner_type"]),
                    "priority": int(row["priority"]),
                    "enabled": bool(row["enabled"]),
                    "protocol": str(row["protocol"]),
                    "action": str(row["action"]),
                    "deployment_target": str(row["deployment_target"]),
                    "source_text": str(row["source_text"]),
                    "dest_ip": str(row["dest_ip"]),
                    "dest_port": str(row["dest_port"]),
                    "kind_key": str(row["kind_key"]),
                    "payload": _load_payload(str(row["payload"])),
                }
            )
        return records, total

    def list_firewall_unified_facets(self) -> dict[str, list[Any]]:
        """返回服务端分页 Grid 的完整筛选值集合，不读取大 JSON payload。"""

        cte = self._unified_firewall_cte()
        with self.connection() as conn:
            owner_rows = conn.execute(
                f"""
                {cte}
                SELECT DISTINCT owner_type, owner_id
                FROM unified
                ORDER BY owner_type, owner_id
                """
            ).fetchall()
            protocols = [
                str(row["protocol"])
                for row in conn.execute(
                    f"{cte} SELECT DISTINCT protocol FROM unified ORDER BY protocol"
                ).fetchall()
                if str(row["protocol"])
            ]
            actions = [
                str(row["action"])
                for row in conn.execute(
                    f"{cte} SELECT DISTINCT action FROM unified ORDER BY action"
                ).fetchall()
                if str(row["action"])
            ]
            kind_keys = [
                str(row["kind_key"])
                for row in conn.execute(
                    f"{cte} SELECT DISTINCT kind_key FROM unified ORDER BY kind_key"
                ).fetchall()
                if str(row["kind_key"])
            ]
        return {
            "owners": [
                {
                    "owner_type": str(row["owner_type"]),
                    "owner_id": str(row["owner_id"]),
                }
                for row in owner_rows
            ],
            "protocols": protocols,
            "actions": actions,
            "kind_keys": kind_keys,
        }

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
