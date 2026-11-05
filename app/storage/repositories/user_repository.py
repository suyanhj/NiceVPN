# -*- coding: utf-8 -*-
"""SQLite user repository; SqliteStore inherits this mixin for API compatibility."""
from __future__ import annotations

from typing import Any, Sequence
from app.storage.repository_helpers import (
    _dump_payload, _load_payload, _append_multi_text_clause, _like_pattern,
)


class UserRepositoryMixin:
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
        search_values: Sequence[str] | None = None,
        search_operator: str = "AND",
        group_ids: Sequence[str] | None = None,
        statuses: Sequence[str] | None = None,
        sort_by: str = "username",
        sort_desc: bool = False,
    ) -> tuple[list[dict[str, Any]], int]:
        """按数据库条件分页读取用户；返回当前页和过滤后的总数。"""

        clauses = ["u.status <> 'deleted'"]
        params: list[Any] = []
        username_values = (
            [str(value) for value in search_values]
            if search_values is not None
            else ([str(search)] if str(search or "").strip() else [])
        )
        _append_multi_text_clause(
            clauses,
            params,
            "u.username",
            username_values,
            match_type=search_mode,
            operator=search_operator,
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

    def list_usernames(self, *, include_deleted: bool = False) -> list[str]:
        """轻量返回用户名列表，不加载 JSON payload。"""

        where = "" if include_deleted else " WHERE status <> 'deleted'"
        with self.connection() as conn:
            rows = conn.execute(
                f"SELECT username FROM users{where} ORDER BY username COLLATE NOCASE"
            ).fetchall()
        return [str(row["username"]) for row in rows]

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
