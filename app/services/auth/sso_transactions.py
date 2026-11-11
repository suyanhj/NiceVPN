# -*- coding: utf-8 -*-
"""Short-lived, one-time, server-owned OpenVPN WebAuth transactions.

This module does not decide whether an employee belongs to an allowed office
enterprise. That check must be performed in the trusted OAuth provider adapter
before the transaction can be approved.
"""
from __future__ import annotations

import hashlib
import secrets
import time
from dataclasses import dataclass

from app.storage.sqlite_store import SqliteStore, get_default_store


def _digest(value: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 512:
        raise ValueError("Invalid SSO transaction credential")
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class AuthRequest:
    """Only trusted server state; no HTTP query may provide CID/KID."""

    instance_id: str
    cid: int
    kid: int
    common_name: str
    provider_id: str
    expires_at: int


@dataclass(frozen=True)
class CreatedAuthRequest:
    token: str
    state: str
    request: AuthRequest


class WebAuthTransactionStore:
    """SQLite-backed issuance and atomic single-consumption on callback.

    Every request is locked to the OpenVPN instance + connection key ID.
    Both secrets are random and only SHA-256 digests persist to the database.
    """

    def __init__(self, store: SqliteStore | None = None) -> None:
        self.store = store if store is not None else get_default_store()

    @staticmethod
    def state_for_token(token: str) -> str:
        # Domain-separated, unguessable OAuth state derivable only from the
        # one-time random token sent directly to the client in WEB_AUTH.
        return hashlib.sha256(
            b"nicevpn-sso-oauth-state-v1:" + token.encode("utf-8")
        ).hexdigest()

    @staticmethod
    def _as_request(row) -> AuthRequest:
        return AuthRequest(
            instance_id=str(row["instance_id"]),
            cid=int(row["client_id"]),
            kid=int(row["key_id"]),
            common_name=str(row["common_name"]),
            provider_id=str(row["provider_id"] or ""),
            expires_at=int(row["expires_at"]),
        )

    def create(
        self, *,
        instance_id: str,
        cid: int,
        kid: int,
        common_name: str,
        provider_id: str,
        ttl_seconds: int = 180,
        now: int | None = None,
    ) -> CreatedAuthRequest:
        instance_id, common_name, provider_id = (
            str(v).strip() for v in (instance_id, common_name, provider_id)
        )
        if not all((instance_id, common_name, provider_id)):
            raise ValueError("Missing SSO instance, certificate CN or provider")
        if len(instance_id) > 255 or len(common_name) > 255 or len(provider_id) > 255:
            raise ValueError("SSO transaction identity too long")
        if not (0 <= cid <= 2**31 - 1 and 0 <= kid <= 2**31 - 1):
            raise ValueError("Invalid OpenVPN CID/KID")
        if not (30 <= ttl_seconds <= 600):
            raise ValueError("Invalid SSO lifetime")
        issued_at = int(time.time() if now is None else now)
        token = secrets.token_urlsafe(32)
        state = self.state_for_token(token)
        expires_at = issued_at + ttl_seconds
        with self.store.connection(write=True) as conn:
            conn.execute(
                """
                INSERT INTO sso_login_transactions
                   (token_hash, state_hash, instance_id, client_id, key_id,
                    common_name, provider_id, status, created_at, expires_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?)
                """,
                (_digest(token), _digest(state), instance_id, cid, kid,
                 common_name, provider_id, issued_at, expires_at),
            )
        request = AuthRequest(instance_id, cid, kid, common_name, provider_id, expires_at)
        return CreatedAuthRequest(token=token, state=state, request=request)

    def get_pending_by_token(self, token: str, *, now: int | None = None) -> AuthRequest | None:
        current = int(time.time() if now is None else now)
        try:
            digest = _digest(token)
        except ValueError:
            return None
        with self.store.connection() as conn:
            row = conn.execute(
                """
                SELECT * FROM sso_login_transactions
                WHERE token_hash=? AND status='pending' AND expires_at>?
                """,
                (digest, current),
            ).fetchone()
        return self._as_request(row) if row is not None else None

    def get_pending_by_state(self, state: str, *, now: int | None = None) -> AuthRequest | None:
        current = int(time.time() if now is None else now)
        try:
            digest = _digest(state)
        except ValueError:
            return None
        with self.store.connection() as conn:
            row = conn.execute(
                """
                SELECT * FROM sso_login_transactions
                WHERE state_hash=? AND status='pending' AND expires_at>?
                """,
                (digest, current),
            ).fetchone()
        return self._as_request(row) if row is not None else None

    def consume_state(
        self, state: str, *, approved: bool,
        now: int | None = None,
    ) -> AuthRequest | None:
        """Atomic compare-and-set; only one callback can win.

        HTTP caller MUST verify provider identity and enterprise membership
        before setting approved=True. A returned request does not itself send
        a Management command: the live controller must match the same CID/KID.
        """
        current = int(time.time() if now is None else now)
        digest = _digest(state)
        with self.store.connection(write=True) as conn:
            row = conn.execute(
                """
                SELECT * FROM sso_login_transactions
                WHERE state_hash=? AND status='pending' AND expires_at>?
                """,
                (digest, current),
            ).fetchone()
            if row is None:
                return None
            new_status = "approved" if approved else "denied"
            changed = conn.execute(
                """
                UPDATE sso_login_transactions
                SET status=?, completed_at=?
                WHERE state_hash=? AND status='pending' AND expires_at>?
                """,
                (new_status, current, digest, current),
            )
            if changed.rowcount != 1:
                return None
        return self._as_request(row)

    def cancel_connection(self, instance_id: str, cid: int, kid: int) -> int:
        """Mark stale pending transactions denied when their tunnel disappears."""
        with self.store.connection(write=True) as conn:
            result = conn.execute(
                """
                UPDATE sso_login_transactions
                SET status='denied', completed_at=?
                WHERE instance_id=? AND client_id=? AND key_id=?
                  AND status='pending'
                """,
                (int(time.time()), str(instance_id), cid, kid),
            )
            return int(result.rowcount)

    def cancel_user(self, common_name: str) -> int:
        """Invalidate every uncompleted browser login for a revoked account."""
        with self.store.connection(write=True) as conn:
            return int(conn.execute(
                """UPDATE sso_login_transactions
                   SET status='denied', completed_at=?
                   WHERE common_name=? AND status='pending'""",
                (int(time.time()), common_name),
            ).rowcount)

    def expire(self, *, now: int | None = None) -> int:
        current = int(time.time() if now is None else now)
        with self.store.connection(write=True) as conn:
            result = conn.execute(
                """
                UPDATE sso_login_transactions
                SET status='expired', completed_at=?
                WHERE status='pending' AND expires_at<=?
                """,
                (current, current),
            )
            return int(result.rowcount)
