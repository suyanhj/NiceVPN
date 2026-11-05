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


class SqliteStore(UserRepositoryMixin, GroupRepositoryMixin):
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

    @staticmethod
    def _migrate_v4_tags(conn: sqlite3.Connection) -> None:
        """标签元数据和用户/组关联；外键级联只删除关联，不删除用户/组。"""
        execute_sql_file(conn, "004_firewall_tags.sql")
        conn.execute(
            "INSERT INTO schema_migrations(version, name, applied_at) VALUES (?, ?, ?)",
            (4, "firewall-tags", _now_iso()),
        )

    # ---- tags ----

    def create_tag(self, tag_id: str, name: str, description: str = "") -> None:
        tag_id, name = str(tag_id).strip(), str(name).strip()
        if not tag_id or not name:
            raise ValueError("标签 ID 与名称不能为空")
        now = _now_iso()
        with self.connection(write=True) as conn:
            conn.execute(
                "INSERT INTO tags(id,name,description,created_at,updated_at) VALUES(?,?,?,?,?)",
                (tag_id, name, str(description), now, now),
            )

    def list_tags(self) -> list[dict[str, Any]]:
        with self.connection() as conn:
            rows = conn.execute(
                "SELECT id,name,description,created_at,updated_at FROM tags ORDER BY name,id"
            ).fetchall()
        return [dict(row) for row in rows]

    def get_tag(self, tag_id: str) -> dict[str, Any] | None:
        with self.connection() as conn:
            row = conn.execute(
                "SELECT id,name,description,created_at,updated_at FROM tags WHERE id=?",
                (str(tag_id),),
            ).fetchone()
        return dict(row) if row else None

    def update_tag(self, tag_id: str, *, name: str, description: str = "") -> None:
        name = str(name).strip()
        if not name:
            raise ValueError("标签名称不能为空")
        with self.connection(write=True) as conn:
            cur = conn.execute(
                "UPDATE tags SET name=?,description=?,updated_at=? WHERE id=?",
                (name, str(description), _now_iso(), str(tag_id)),
            )
            if cur.rowcount != 1:
                raise ValueError("标签不存在")

    def delete_tag(self, tag_id: str) -> None:
        with self.connection(write=True) as conn:
            conn.execute("DELETE FROM tags WHERE id=?", (str(tag_id),))

    def set_tag_members(
        self, tag_id: str, *, usernames: Sequence[str] = (), group_ids: Sequence[str] = (),
    ) -> None:
        """整体替换关联，单事务提交；非法成员拒绝并保持原关联。"""
        tid = str(tag_id)
        users = sorted({str(v).strip() for v in usernames if str(v).strip()})
        groups = sorted({str(v).strip() for v in group_ids if str(v).strip()})
        with self.connection(write=True) as conn:
            if conn.execute("SELECT 1 FROM tags WHERE id=?", (tid,)).fetchone() is None:
                raise ValueError("标签不存在")
            if users:
                placeholders = ",".join("?" for _ in users)
                found = {r[0] for r in conn.execute(
                    f"SELECT username FROM users WHERE username IN ({placeholders})", users
                )}
                if len(found) != len(users):
                    raise ValueError("关联用户不存在")
            if groups:
                placeholders = ",".join("?" for _ in groups)
                found = {r[0] for r in conn.execute(
                    f"SELECT id FROM groups WHERE id IN ({placeholders})", groups
                )}
                if len(found) != len(groups):
                    raise ValueError("关联用户组不存在")
            conn.execute("DELETE FROM user_tags WHERE tag_id=?", (tid,))
            conn.execute("DELETE FROM group_tags WHERE tag_id=?", (tid,))
            conn.executemany(
                "INSERT INTO user_tags(tag_id,username) VALUES (?,?)",
                [(tid, username) for username in users],
            )
            conn.executemany(
                "INSERT INTO group_tags(tag_id,group_id) VALUES (?,?)",
                [(tid, gid) for gid in groups],
            )

    def list_entity_tag_ids(self, entity_type: str, entity_id: str) -> list[str]:
        if entity_type == "user":
            table, column = "user_tags", "username"
        elif entity_type == "group":
            table, column = "group_tags", "group_id"
        else:
            raise ValueError("仅支持 user/group")
        with self.connection() as conn:
            rows = conn.execute(
                f"SELECT tag_id FROM {table} WHERE {column}=? ORDER BY tag_id",
                (str(entity_id),),
            ).fetchall()
        return [str(row[0]) for row in rows]

    def replace_entity_tags(self, entity_type: str, entity_id: str, tag_ids: Sequence[str]) -> None:
        """单个 SQLite 事务整体替换一个用户/组的所有标签，杜绝关联写到一半。"""
        if entity_type == "user":
            table, column, entity_table, entity_key = "user_tags", "username", "users", "username"
        elif entity_type == "group":
            table, column, entity_table, entity_key = "group_tags", "group_id", "groups", "id"
        else:
            raise ValueError("仅支持 user/group")
        eid = str(entity_id).strip()
        ids = sorted({str(value).strip() for value in tag_ids if str(value).strip()})
        with self.connection(write=True) as conn:
            if conn.execute(f"SELECT 1 FROM {entity_table} WHERE {entity_key}=?", (eid,)).fetchone() is None:
                raise ValueError("关联用户或组不存在")
            if ids:
                placeholders = ",".join("?" for _ in ids)
                count = conn.execute(
                    f"SELECT COUNT(*) FROM tags WHERE id IN ({placeholders})", ids,
                ).fetchone()[0]
                if int(count) != len(ids):
                    raise ValueError("存在未知标签")
            conn.execute(f"DELETE FROM {table} WHERE {column}=?", (eid,))
            conn.executemany(
                f"INSERT INTO {table}(tag_id,{column}) VALUES (?,?)",
                [(tid, eid) for tid in ids],
            )

    def batch_change_entity_tags(
        self, entity_type: str, entity_ids: Sequence[str],
        tag_ids: Sequence[str], *, operation: str,
    ) -> dict[str, list[str]]:
        """批量增删关联：单事务校验全部对象，返回每个对象修改前的标签。

        批量失败时全部回滚，不覆盖不相关标签；后续可按快照恢复。
        """
        if entity_type == "user":
            table, column, entity_table, entity_key = "user_tags", "username", "users", "username"
        elif entity_type == "group":
            table, column, entity_table, entity_key = "group_tags", "group_id", "groups", "id"
        else:
            raise ValueError("仅支持 user/group")
        if operation not in ("add", "remove", "replace"):
            raise ValueError("不支持的标签批量操作")
        ids = sorted({str(v).strip() for v in entity_ids if str(v).strip()})
        tags = sorted({str(v).strip() for v in tag_ids if str(v).strip()})
        if not ids:
            raise ValueError("请先选择用户或组")
        if not tags and operation != "replace":
            raise ValueError("请先选择标签")
        with self.connection(write=True) as conn:
            slots = ",".join("?" for _ in ids)
            found = {str(r[0]) for r in conn.execute(
                f"SELECT {entity_key} FROM {entity_table} WHERE {entity_key} IN ({slots})", ids
            )}
            if len(found) != len(ids):
                raise ValueError("所选用户或组有不存在的记录")
            if tags:
                marks = ",".join("?" for _ in tags)
                existing = {str(r[0]) for r in conn.execute(
                    f"SELECT id FROM tags WHERE id IN ({marks})", tags
                )}
                if len(existing) != len(tags):
                    raise ValueError("包含不存在的标签")
            previous: dict[str, list[str]] = {}
            for eid in ids:
                old = [str(r[0]) for r in conn.execute(
                    f"SELECT tag_id FROM {table} WHERE {column}=? ORDER BY tag_id", (eid,)
                )]
                previous[eid] = old
                if operation == "add":
                    desired = sorted(set(old) | set(tags))
                elif operation == "remove":
                    desired = sorted(set(old) - set(tags))
                else:
                    desired = tags
                if desired != old:
                    conn.execute(f"DELETE FROM {table} WHERE {column}=?", (eid,))
                    conn.executemany(
                        f"INSERT INTO {table}(tag_id,{column}) VALUES (?,?)",
                        [(tag, eid) for tag in desired],
                    )
        return previous

    def restore_entity_tag_snapshots(
        self, entity_type: str, snapshots: dict[str, list[str]],
    ) -> None:
        """一次事务恢复批量关联修改前的全部对象。"""
        if entity_type == "user":
            table, column = "user_tags", "username"
        elif entity_type == "group":
            table, column = "group_tags", "group_id"
        else:
            raise ValueError("仅支持 user/group")
        with self.connection(write=True) as conn:
            for eid, tags in snapshots.items():
                conn.execute(f"DELETE FROM {table} WHERE {column}=?", (eid,))
                conn.executemany(
                    f"INSERT INTO {table}(tag_id,{column}) VALUES (?,?)",
                    [(tag, eid) for tag in tags],
                )

    def get_tag_members(self, tag_id: str) -> dict[str, list[str]]:
        with self.connection() as conn:
            users = conn.execute(
                "SELECT username FROM user_tags WHERE tag_id=? ORDER BY username",
                (str(tag_id),),
            ).fetchall()
            groups = conn.execute(
                "SELECT group_id FROM group_tags WHERE tag_id=? ORDER BY group_id",
                (str(tag_id),),
            ).fetchall()
        return {
            "usernames": [r[0] for r in users],
            "group_ids": [r[0] for r in groups],
        }

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
                    COALESCE(json_extract(payload, '$.description'), '') AS description_text,
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
                    COALESCE(json_extract(payload, '$.center_forward_rule_description'), '') AS description_text,
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
        text_filters: Mapping[str, tuple[Sequence[str], str, str]] | None = None,
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

        text_filter_columns = {
            "id": "row_id",
            "source": "source_text",
            "dest": "dest_ip",
            "dest_port": "dest_port",
            "description": "description_text",
        }
        for field, spec in (text_filters or {}).items():
            expression = text_filter_columns.get(str(field))
            if expression is None or len(spec) != 3:
                continue
            values, match_type, operator = spec
            _append_multi_text_clause(
                clauses,
                params,
                expression,
                values,
                match_type=str(match_type),
                operator=str(operator),
            )

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
            "description": "description_text COLLATE NOCASE",
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
                    source_text, dest_ip, dest_port, description_text, kind_key, payload
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
                    "description_text": str(row["description_text"]),
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
