# -*- coding: utf-8 -*-
"""Deterministic enterprise SSO policy resolution by verified certificate CN.

The office-system identity is deliberately NOT mapped to a NiceVPN user.
The VPN certificate CN selects existing group/tag network permissions; office
identity only proves membership in an approved enterprise.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.storage.repository_helpers import _load_payload
from app.storage.sqlite_store import SqliteStore, get_default_store


@dataclass(frozen=True)
class SsoPolicyDecision:
    # One of: legacy, required, reject
    mode: str
    sources: tuple[str, ...] = ()
    reason: str = ""

    @property
    def requires_sso(self) -> bool:
        return self.mode == "required"

    @property
    def denied(self) -> bool:
        return self.mode == "reject"


class SsoPolicyService:
    """Read certificate-scoped policy without trusting IV_PLAT/IV_SSO as identity."""

    def __init__(self, store: SqliteStore | None = None):
        self.store = store if store is not None else get_default_store()

    def resolve(
        self, common_name: str, *, enabled: bool, all_users: bool = False,
    ) -> SsoPolicyDecision:
        if not enabled:
            return SsoPolicyDecision("legacy", reason="global_disabled")
        cn = str(common_name or "").strip()
        if not cn or "\n" in cn or "\r" in cn:
            return SsoPolicyDecision("reject", reason="invalid_certificate_cn")
        if all_users:
            # Global opt-in is explicit; do not silently exempt unmanaged CNs.
            return SsoPolicyDecision("required", ("global",), "global_all_users")
        with self.store.connection() as conn:
            # Certificate issuer/CRL validation belongs to OpenVPN; this query
            # only resolves the existing NiceVPN policy owner for that CN.
            user = conn.execute(
                "SELECT group_id, status FROM users WHERE username=?", (cn,)
            ).fetchone()
            if user is None:
                # No matching group/tag policy: preserve pre-SSO behavior.
                # OpenVPN certificate/CRL/CCD and device rules still apply.
                return SsoPolicyDecision("legacy", reason="unmanaged_certificate")
            if str(user["status"]) != "active":
                return SsoPolicyDecision("legacy", reason="inactive_user_original_policy")
            gid = str(user["group_id"] or "")
            group = conn.execute(
                "SELECT status, payload FROM groups WHERE id=?", (gid,)
            ).fetchone()
            if group is None or str(group["status"]) != "active":
                return SsoPolicyDecision("legacy", reason="inactive_group_original_policy")
            group_payload = _load_payload(str(group["payload"]))
            sources = []
            if group_payload.get("sso_required") is True:
                sources.append(f"group:{gid}")
            tags = conn.execute(
                """
                SELECT DISTINCT t.id
                FROM tags t
                WHERE t.sso_required=1 AND (
                    EXISTS (SELECT 1 FROM user_tags ut
                            WHERE ut.tag_id=t.id AND ut.username=?)
                    OR EXISTS (SELECT 1 FROM group_tags gt
                               WHERE gt.tag_id=t.id AND gt.group_id=?)
                )
                ORDER BY t.id
                """,
                (cn, gid),
            ).fetchall()
            sources.extend(f"tag:{str(row['id'])}" for row in tags)
        return (SsoPolicyDecision("required", tuple(sources), "policy_required")
                if sources else SsoPolicyDecision("legacy", reason="no_sso_requirement"))

    def set_group_required(self, group_id: str, *, required: bool) -> None:
        """Update existing JSON payload, preserving all other group settings."""
        group = self.store.get_group(group_id)
        if group is None:
            raise ValueError("Group does not exist")
        group["sso_required"] = bool(required)
        self.store.upsert_group(group)

    def set_tag_required(self, tag_id: str, *, required: bool) -> None:
        with self.store.connection(write=True) as conn:
            changed = conn.execute(
                "UPDATE tags SET sso_required=?, updated_at=datetime('now') WHERE id=?",
                (int(bool(required)), str(tag_id)),
            )
            if changed.rowcount != 1:
                raise ValueError("Tag does not exist")
