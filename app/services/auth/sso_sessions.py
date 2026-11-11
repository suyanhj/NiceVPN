# -*- coding: utf-8 -*-
"""Durable OAuth sessions with a one-hour *offline* reconnect grace.

This module DOES NOT validate certificates, check CA/CRL or decide VPN access.
OpenVPN does that before sending Management CONNECT/REAUTH. We only use the
OpenVPN-exported tls_digest_sha256_0 as an opaque, stable session cache key.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Mapping

from app.storage.sqlite_store import SqliteStore, get_default_store

SSO_SESSION_TTL_SECONDS = 3600  # Offline grace, refreshed only by verified online status.
_SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")


def verified_peer_fingerprint(env: Mapping[str, str]) -> str:
    """Normalize OpenVPN Management's certificate SHA-256 fingerprint.

    Never use an IV_* value or HTTP/browser parameter here. A missing or
    malformed fingerprint disables persistent login reuse (fail closed).
    """
    raw = env.get("tls_digest_sha256_0", "")
    if not isinstance(raw, str):
        return ""
    value = raw.strip().lower().replace(":", "")
    return value if _SHA256_HEX.fullmatch(value) else ""


@dataclass(frozen=True)
class SsoSession:
    instance_id: str
    common_name: str
    cert_fingerprint: str
    provider_id: str
    provider_app_id: str
    enterprise_id: str
    expires_at: int
    enterprise_name: str = ""


@dataclass(frozen=True)
class OnlineSsoBinding:
    """Identity authenticated by SSO, joined to trusted OpenVPN status."""

    common_name: str
    cert_fingerprint: str
    provider_id: str
    provider_app_id: str
    enterprise_id: str
    client_id: int
    connected_at: int
    enterprise_name: str = ""


class SsoSessionStore:
    """One mutable session row per certificate/provider, shared across restarts."""

    def __init__(self, store: SqliteStore | None = None) -> None:
        self.store = store if store is not None else get_default_store()

    def remember(
        self, *, instance_id: str, common_name: str, cert_fingerprint: str,
        provider_id: str, provider_app_id: str, enterprise_id: str,
        enterprise_name: str = "",
        now: int | None = None,
    ) -> SsoSession:
        if not _SHA256_HEX.fullmatch(cert_fingerprint):
            raise ValueError("OpenVPN certificate fingerprint not available")
        values = (instance_id, common_name, provider_id, provider_app_id, enterprise_id)
        if any(
            not isinstance(value, str) or not value or len(value) > 255
            or any(ord(char) < 32 for char in value)
            for value in values
        ):
            raise ValueError("Invalid SSO session identity")
        if (not isinstance(enterprise_name, str) or len(enterprise_name) > 80
                or any(ord(char) < 32 for char in enterprise_name)):
            raise ValueError("Invalid verified enterprise name")
        issued_at = int(time.time() if now is None else now)
        expires_at = issued_at + SSO_SESSION_TTL_SECONDS
        with self.store.connection(write=True) as conn:
            conn.execute(
                """INSERT INTO sso_auth_sessions (
                    instance_id, common_name, cert_fingerprint, provider_id,
                    provider_app_id, enterprise_id, issued_at, expires_at,
                    last_used_at, enterprise_name
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(instance_id, common_name, cert_fingerprint, provider_id)
                DO UPDATE SET
                    provider_app_id=excluded.provider_app_id,
                    enterprise_id=excluded.enterprise_id,
                    enterprise_name=excluded.enterprise_name,
                    issued_at=excluded.issued_at,
                    expires_at=excluded.expires_at,
                    last_used_at=excluded.last_used_at,
                    active_client_id=NULL,
                    active_connected_at=NULL
                """,
                (instance_id, common_name, cert_fingerprint, provider_id,
                 provider_app_id, enterprise_id, issued_at, expires_at, issued_at,
                 enterprise_name),
            )
            conn.execute(
                "DELETE FROM sso_auth_sessions WHERE expires_at<=?",
                (issued_at,),
            )
        return SsoSession(
            instance_id, common_name, cert_fingerprint, provider_id,
            provider_app_id, enterprise_id, expires_at, enterprise_name,
        )

    def get(
        self, *, instance_id: str, common_name: str, cert_fingerprint: str,
        provider_id: str, provider_app_id: str,
        now: int | None = None,
    ) -> SsoSession | None:
        if not _SHA256_HEX.fullmatch(cert_fingerprint):
            return None
        current = int(time.time() if now is None else now)
        with self.store.connection() as conn:
            row = conn.execute(
                """SELECT instance_id,common_name,cert_fingerprint,provider_id,
                          provider_app_id,enterprise_id,expires_at,enterprise_name
                   FROM sso_auth_sessions
                   WHERE instance_id=? AND common_name=? AND cert_fingerprint=?
                     AND provider_id=? AND provider_app_id=? AND expires_at>?""",
                (instance_id, common_name, cert_fingerprint, provider_id,
                 provider_app_id, current),
            ).fetchone()
        return SsoSession(**dict(row)) if row else None

    def refresh_online(
        self, *, instance_id: str, bindings: list[OnlineSsoBinding],
        now: int | None = None,
    ) -> set[tuple[str, str]]:
        """Extend only existing, unexpired records for proven live connections.

        The caller must match CID, CN and server-side connected_at from a
        completed Management status snapshot. This method never INSERTs.
        """
        current = int(time.time() if now is None else now)
        updated: set[tuple[str, str]] = set()
        if not bindings:
            return updated
        with self.store.connection(write=True) as conn:
            for item in bindings:
                if not (_SHA256_HEX.fullmatch(item.cert_fingerprint)
                        and item.client_id >= 0 and item.connected_at > 0):
                    continue
                result = conn.execute(
                    """UPDATE sso_auth_sessions
                       SET last_used_at=MAX(last_used_at, ?),
                           expires_at=MAX(expires_at, ?),
                           active_client_id=?, active_connected_at=?
                       WHERE instance_id=? AND common_name=? AND cert_fingerprint=?
                         AND provider_id=? AND provider_app_id=?
                         AND enterprise_id=? AND enterprise_name=? AND expires_at>?""",
                    (current, current + SSO_SESSION_TTL_SECONDS,
                     item.client_id, item.connected_at,
                     instance_id, item.common_name, item.cert_fingerprint,
                     item.provider_id, item.provider_app_id, item.enterprise_id,
                     item.enterprise_name,
                     current),
                )
                if result.rowcount:
                    updated.add((item.common_name, item.cert_fingerprint))
        return updated

    def recover_online(
        self, *, instance_id: str, common_name: str,
        client_id: int, connected_at: int,
        provider_id: str, provider_app_id: str,
        now: int | None = None,
    ) -> SsoSession | None:
        """Recover a session after NiceVPN restart without a CN-only bypass.

        CID alone can be reused by a restarted OpenVPN, so its trusted
        connected-since timestamp must match the persisted binding too.
        """
        current = int(time.time() if now is None else now)
        with self.store.connection() as conn:
            rows = conn.execute(
                """SELECT instance_id,common_name,cert_fingerprint,provider_id,
                          provider_app_id,enterprise_id,expires_at,enterprise_name
                   FROM sso_auth_sessions
                   WHERE instance_id=? AND common_name=? AND provider_id=?
                     AND provider_app_id=? AND active_client_id=?
                     AND active_connected_at=? AND expires_at>?""",
                (instance_id, common_name, provider_id, provider_app_id,
                 client_id, connected_at, current),
            ).fetchmany(2)
        # Multiple matching cert rows indicate an ambiguous binding; fail closed.
        return SsoSession(**dict(rows[0])) if len(rows) == 1 else None

    def disconnected(
        self, *, instance_id: str, client_id: int, now: int | None = None,
    ) -> None:
        """Start the full offline grace when an authenticated tunnel disconnects.

        Only a prior Management-confirmed, still-valid row may be extended.
        A revoked or already expired session is never resurrected.
        """
        current = int(time.time() if now is None else now)
        with self.store.connection(write=True) as conn:
            conn.execute(
                """UPDATE sso_auth_sessions
                   SET active_client_id=NULL, active_connected_at=NULL,
                       expires_at=MAX(expires_at, ?)
                   WHERE instance_id=? AND active_client_id=?
                     AND expires_at>?""",
                (current + SSO_SESSION_TTL_SECONDS,
                 instance_id, client_id, current),
            )

    def prune_expired(self, *, now: int | None = None) -> int:
        """Bound long-term row count to recently authenticated identities."""
        current = int(time.time() if now is None else now)
        with self.store.connection(write=True) as conn:
            return int(conn.execute(
                "DELETE FROM sso_auth_sessions WHERE expires_at<=?", (current,),
            ).rowcount)

    def revoke(
        self, *, instance_id: str, common_name: str,
        cert_fingerprint: str | None = None,
    ) -> int:
        """Administrator action: immediately revoke cached SSO reconnects.

        Subsequent CONNECT/REAUTH will require OAuth. Does not kill an
        already-established OpenVPN tunnel; that is OpenVPN's responsibility.
        """
        with self.store.connection(write=True) as conn:
            if cert_fingerprint is None:
                result = conn.execute(
                    "DELETE FROM sso_auth_sessions WHERE instance_id=? AND common_name=?",
                    (instance_id, common_name),
                )
            elif _SHA256_HEX.fullmatch(cert_fingerprint):
                result = conn.execute(
                    "DELETE FROM sso_auth_sessions "
                    "WHERE instance_id=? AND common_name=? AND cert_fingerprint=?",
                    (instance_id, common_name, cert_fingerprint),
                )
            else:
                raise ValueError("Invalid certificate fingerprint")
            return int(result.rowcount)
