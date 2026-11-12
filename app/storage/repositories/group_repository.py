# -*- coding: utf-8 -*-
"""SQLite group repository; SqliteStore inherits this mixin for API compatibility."""
from __future__ import annotations

from typing import Any, Sequence
from app.storage.repository_helpers import (
    _dump_payload, _load_payload, _append_multi_text_clause,
    _normalize_group_payload, _like_pattern,
)


class GroupRepositoryMixin:
    # ---- groups ----

    def upsert_group(self, data: dict[str, Any]) -> None:
        group_id = str(data.get("id") or "").strip()
        if not group_id:
            raise ValueError("group.id 不能为空")
        payload = _normalize_group_payload(data)
        payload["id"] = group_id
        changed = [False]
        new_status = str(payload.get("status") or "active")
        new_exempt = bool(payload.get("device_binding_exempt", False))
        with self.connection(
            write=True, device_binding_changed=changed,
        ) as conn:
            old = conn.execute(
                "SELECT status,payload FROM groups WHERE id=?", (group_id,)
            ).fetchone()
            changed[0] = (
                old is None
                or str(old["status"]) != new_status
                or bool(_load_payload(old["payload"]).get("device_binding_exempt"))
                != new_exempt
            )
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
                    new_status,
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

    def group_summary(self) -> dict[str, int]:
        """返回组页摘要，避免为统计读取全部 group payload。"""
        with self.connection() as conn:
            group_row = conn.execute(
                """
                SELECT
                    COUNT(*) AS total,
                    SUM(CASE WHEN status = 'active' THEN 1 ELSE 0 END) AS active
                FROM groups
                """
            ).fetchone()
            user_row = conn.execute(
                "SELECT COUNT(*) AS n FROM users WHERE status <> 'deleted'"
            ).fetchone()
        total = int(group_row["total"] or 0)
        active = int(group_row["active"] or 0)
        return {
            "total": total,
            "active": active,
            "disabled": max(0, total - active),
            "users": int(user_row["n"] or 0),
        }

    def first_group_id(self) -> str:
        with self.connection() as conn:
            row = conn.execute(
                "SELECT id FROM groups ORDER BY created_at, id LIMIT 1"
            ).fetchone()
        return str(row["id"]) if row else ""

    @staticmethod
    def _group_delete_blockers_in_connection(
        conn,
        group_ids: Sequence[str],
    ) -> dict[str, dict[str, int]]:
        """在同一连接中统计组删除阻塞项；peer Mesh 范围从 peer payload 读取。"""
        wanted = list(dict.fromkeys(str(value).strip() for value in group_ids if str(value).strip()))
        result = {
            group_id: {
                "users": 0,
                "tags": 0,
                "firewall_rules": 0,
                "mesh_peers": 0,
            }
            for group_id in wanted
        }
        if not wanted:
            return result

        placeholders = ",".join("?" for _ in wanted)
        queries = (
            (
                "users",
                f"SELECT group_id AS ref_id, COUNT(*) AS n FROM users "
                f"WHERE group_id IN ({placeholders}) GROUP BY group_id",
            ),
            (
                "tags",
                f"SELECT group_id AS ref_id, COUNT(*) AS n FROM group_tags "
                f"WHERE group_id IN ({placeholders}) GROUP BY group_id",
            ),
            (
                "firewall_rules",
                f"SELECT owner_id AS ref_id, COUNT(*) AS n FROM firewall_rules "
                f"WHERE owner_type='group' AND owner_id IN ({placeholders}) GROUP BY owner_id",
            ),
        )
        for key, sql in queries:
            for row in conn.execute(sql, wanted).fetchall():
                group_id = str(row["ref_id"] or "")
                if group_id in result:
                    result[group_id][key] = int(row["n"] or 0)

        wanted_set = set(wanted)
        for row in conn.execute("SELECT payload FROM peers").fetchall():
            peer = _load_payload(row["payload"]) or {}
            raw_group_ids = peer.get("mesh_route_visible_group_ids") or []
            if not isinstance(raw_group_ids, (list, tuple, set)):
                continue
            referenced = {
                str(value).strip()
                for value in raw_group_ids
                if str(value).strip()
            }
            for group_id in referenced & wanted_set:
                result[group_id]["mesh_peers"] += 1

        return result

    def get_group_delete_blockers(
        self,
        group_ids: Sequence[str],
    ) -> dict[str, dict[str, int]]:
        """批量统计组删除阻塞项，供组列表展示禁用原因，避免逐行查询。"""
        wanted = list(dict.fromkeys(str(value).strip() for value in group_ids if str(value).strip()))
        if not wanted:
            return {}
        with self.connection() as conn:
            return self._group_delete_blockers_in_connection(conn, wanted)

    @staticmethod
    def _group_delete_blocker_message(blockers: dict[str, int]) -> str:
        parts: list[str] = []
        users = int(blockers.get("users") or 0)
        tags = int(blockers.get("tags") or 0)
        rules = int(blockers.get("firewall_rules") or 0)
        mesh_peers = int(blockers.get("mesh_peers") or 0)
        if users:
            parts.append(f"组内有 {users} 个用户")
        if tags:
            parts.append(f"仍被 {tags} 个标签关联")
        if rules:
            parts.append(f"仍被 {rules} 条中心防火墙规则引用")
        if mesh_peers:
            parts.append(f"仍被 {mesh_peers} 个对端 Mesh 路由范围引用")
        if not parts:
            return ""
        return "用户组无法删除：" + "；".join(parts) + "。请先解除关联后重试"

    def delete_group(self, group_id: str) -> None:
        """删除前在写事务内再次检查用户、标签、中心规则和对端 Mesh 引用。"""
        gid = str(group_id)
        changed = [False]
        with self.connection(
            write=True, device_binding_changed=changed,
        ) as conn:
            blockers = self._group_delete_blockers_in_connection(conn, [gid]).get(gid, {})
            reason = self._group_delete_blocker_message(blockers)
            if reason:
                raise ValueError(reason)
            result = conn.execute(
                "DELETE FROM groups WHERE id = ?", (gid,)
            )
            changed[0] = result.rowcount > 0

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
