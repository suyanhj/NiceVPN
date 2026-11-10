# -*- coding: utf-8 -*-
"""Bounded SSO security audit events. Do not record OAuth codes or secrets."""
from __future__ import annotations

from datetime import datetime, timezone

from app.storage.sqlite_store import SqliteStore, get_default_store

_EVENT_TYPES = frozenset({
    "pending", "approved", "denied", "disconnected", "timeout",
    "provider_error", "no_webauth", "policy_rejected", "session_reauth",
})


class SsoAudit:
    def __init__(self, store: SqliteStore | None = None):
        self.store = store if store is not None else get_default_store()

    def log(
        self, *, event_type: str, instance_id: str, common_name: str,
        outcome: str, provider_kind: str = "", enterprise_id: str = "",
        external_user_id: str = "", reason_code: str = "",
    ) -> None:
        if event_type not in _EVENT_TYPES:
            raise ValueError("Invalid SSO audit event")
        if outcome not in {"success", "failure", "pending", "cancelled"}:
            raise ValueError("Invalid audit outcome")
        fields = [
            instance_id, common_name, provider_kind, enterprise_id,
            external_user_id, reason_code,
        ]
        if any(
            not isinstance(v, str) or len(v) > 255
            or any(ord(ch) < 32 for ch in v) for v in fields
        ):
            raise ValueError("Invalid audit field")
        with self.store.connection(write=True) as conn:
            conn.execute(
                """
                INSERT INTO sso_auth_audit (
                    event_time, instance_id, common_name, provider_kind,
                    enterprise_id, external_user_id, event_type, outcome,
                    reason_code
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    datetime.now(timezone.utc).isoformat(),
                    instance_id, common_name, provider_kind, enterprise_id,
                    external_user_id, event_type, outcome, reason_code,
                ),
            )

    def recent(self, *, limit: int = 50) -> list[dict]:
        if not (1 <= int(limit) <= 500):
            raise ValueError("Invalid SSO audit page size")
        with self.store.connection() as conn:
            rows = conn.execute(
                """
                SELECT event_time,instance_id,common_name,provider_kind,
                       enterprise_id,external_user_id,event_type,outcome,
                       reason_code
                FROM sso_auth_audit ORDER BY id DESC LIMIT ?
                """, (int(limit),),
            ).fetchall()
        return [dict(row) for row in rows]
