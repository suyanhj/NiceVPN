# -*- coding: utf-8 -*-
"""Short-lived durable OAuth session reuse after OpenVPN has verified the peer cert.

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

SSO_SESSION_TTL_SECONDS = 8 * 3600
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


class SsoSessionStore:
    """Persistent, fixed-expiration SSO session cache shared across restarts."""

    def __init__(self, store: SqliteStore | None = None) -> None:
        self.store = store if store is not None else get_default_store()

    def remember(
        self, *, instance_id: str, common_name: str, cert_fingerprint: str,
        provider_id: str, provider_app_id: str, enterprise_id: str,
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
        issued_at = int(time.time() if now is None else now)
        expires_at = issued_at + SSO_SESSION_TTL_SECONDS
        with self.store.connection(write=True) as conn:
            conn.execute(
                """INSERT INTO sso_auth_sessions (
                    instance_id, common_name, cert_fingerprint, provider_id,
                    provider_app_id, enterprise_id, issued_at, expires_at, last_used_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(instance_id, common_name, cert_fingerprint, provider_id)
                DO UPDATE SET
                    provider_app_id=excluded.provider_app_id,
                    enterprise_id=excluded.enterprise_id,
                    issued_at=excluded.issued_at,
                    expires_at=excluded.expires_at,
                    last_used_at=excluded.last_used_at
                """,
                (instance_id, common_name, cert_fingerprint, provider_id,
                 provider_app_id, enterprise_id, issued_at, expires_at, issued_at),
            )
            conn.execute(
                "DELETE FROM sso_auth_sessions WHERE expires_at<=?",
                (issued_at,),
            )
        return SsoSession(
            instance_id, common_name, cert_fingerprint, provider_id,
            provider_app_id, enterprise_id, expires_at,
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
                          provider_app_id,enterprise_id,expires_at
                   FROM sso_auth_sessions
                   WHERE instance_id=? AND common_name=? AND cert_fingerprint=?
                     AND provider_id=? AND provider_app_id=? AND expires_at>?""",
                (instance_id, common_name, cert_fingerprint, provider_id,
                 provider_app_id, current),
            ).fetchone()
        return SsoSession(**dict(row)) if row else None

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
