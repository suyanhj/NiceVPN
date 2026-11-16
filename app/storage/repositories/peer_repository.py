# -*- coding: utf-8 -*-
"""SQLite peer repository mixin."""
from __future__ import annotations

from typing import Any, Sequence
from app.storage.repository_helpers import (
    _dump_payload, _load_payload, _peer_index_values, _like_pattern, _now_iso,
)


class PeerRepositoryMixin:
    # ---- peers ----

    def upsert_peer(self, data: dict[str, Any]) -> None:
        peer_id = str(data.get("id") or "").strip()
        if not peer_id:
            raise ValueError("peer.id 不能为空")
        payload = dict(data)
        payload["id"] = peer_id
        with self.connection(write=True) as conn:
            existing = conn.execute(
                "SELECT payload FROM peers WHERE id = ?", (peer_id,)
            ).fetchone()
            if existing is not None and "remote_status" not in payload:
                previous = _load_payload(existing["payload"])
                remote_status = previous.get("remote_status")
                if isinstance(remote_status, dict):
                    payload["remote_status"] = remote_status
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

    def update_peer_remote_status(self, peer_id: str, **updates: Any) -> bool:
        """原子更新单个对端的 SSH 连通性和最近远程操作状态。"""
        allowed = {
            "ssh_status",
            "ssh_checked_at",
            "ssh_error",
            "last_operation",
            "last_operation_status",
            "last_operation_at",
            "last_operation_error",
        }
        values = {key: value for key, value in updates.items() if key in allowed}
        if not values:
            return False
        pid = str(peer_id or "").strip()
        if not pid:
            return False
        with self.connection(write=True) as conn:
            row = conn.execute(
                "SELECT payload FROM peers WHERE id = ?", (pid,)
            ).fetchone()
            if row is None:
                return False
            payload = _load_payload(row["payload"])
            remote_status = payload.get("remote_status")
            if not isinstance(remote_status, dict):
                remote_status = {}
            remote_status.update(values)
            remote_status["updated_at"] = _now_iso()
            payload["remote_status"] = remote_status
            conn.execute(
                "UPDATE peers SET payload = ? WHERE id = ?",
                (_dump_payload(payload), pid),
            )
        return True

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
