# -*- coding: utf-8 -*-
"""SQLite firewall rule repository mixin."""
from __future__ import annotations

from typing import Any, Sequence, Mapping
from app.storage.repository_helpers import (
    _dump_payload, _load_payload, _firewall_index_values,
    _like_pattern, _append_multi_text_clause,
)


class FirewallRepositoryMixin:
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
