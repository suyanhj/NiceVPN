# -*- coding: utf-8 -*-
"""Private-at-rest OAuth provider credentials and public provider metadata.

The encryption key is a local 0600 file separate from SQLite. The backup
routine must archive/restore it alongside the database. Missing keys FAIL
CLOSED: do not generate a replacement if encrypted provider rows exist.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from app.core.constants import SSO_PROVIDER_MASTER_KEY_FILE
from app.services.auth.office_oauth import OfficeProvider
from app.storage.sqlite_store import SqliteStore, get_default_store

SSO_KEY_FILE = SSO_PROVIDER_MASTER_KEY_FILE
_PROVIDER_ID = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")


class ProviderSecretError(RuntimeError):
    pass


def _fernet(path: Path, *, create: bool = False) -> Fernet:
    if not path.exists():
        if not create:
            raise ProviderSecretError("SSO key file missing; restore it from backup")
        path.parent.mkdir(parents=True, exist_ok=True)
        key = Fernet.generate_key()
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            pass
        else:
            with os.fdopen(fd, "wb") as out:
                out.write(key)
                out.flush()
                os.fsync(out.fileno())
    if os.name != "nt" and (path.stat().st_mode & 0o077):
        raise ProviderSecretError("SSO key file permissions are too broad")
    try:
        return Fernet(path.read_bytes().strip())
    except (ValueError, TypeError) as exc:
        raise ProviderSecretError("Invalid SSO master key") from exc


class SsoProviderStore:
    def __init__(self, store: SqliteStore | None = None, *, key_path: Path | None = None):
        self.store = store if store is not None else get_default_store()
        self.key_path = key_path if key_path is not None else SSO_KEY_FILE

    def list_metadata(self) -> list[dict]:
        with self.store.connection() as conn:
            rows = conn.execute(
                "SELECT id,kind,enterprise_id,app_id,enabled,created_at,updated_at "
                "FROM sso_auth_providers ORDER BY kind,id"
            ).fetchall()
        return [
            {**dict(row), "enabled": bool(row["enabled"])}
            for row in rows
        ]

    def get(self, provider_id: str, *, require_enabled: bool = True) -> OfficeProvider | None:
        with self.store.connection() as conn:
            row = conn.execute(
                "SELECT * FROM sso_auth_providers WHERE id=?", (str(provider_id),)
            ).fetchone()
        if row is None or (require_enabled and not bool(row["enabled"])):
            return None
        try:
            encrypted = str(row["secret_ref"])
            if not encrypted.startswith("fernet:"):
                raise ProviderSecretError("Unknown SSO credential encoding")
            secret = _fernet(self.key_path).decrypt(encrypted[7:].encode()).decode()
        except (InvalidToken, UnicodeError) as exc:
            raise ProviderSecretError("SSO provider secret cannot be decrypted") from exc
        result = OfficeProvider(
            id=str(row["id"]),
            kind=str(row["kind"]),
            enterprise_id=str(row["enterprise_id"]),
            app_id=str(row["app_id"]),
            secret=secret,
        )
        result.validate()
        return result

    def first_enabled(self) -> OfficeProvider | None:
        enabled = [row for row in self.list_metadata() if row["enabled"]]
        if not enabled:
            return None
        # Deterministic first provider (UI may add explicit selection later).
        return self.get(enabled[0]["id"])

    def save(self, *, provider_id: str, kind: str, enterprise_id: str,
             app_id: str, secret: str | None = None, enabled: bool = False) -> None:
        """Admin-only service method, never exposed on anonymous Public API."""
        if not _PROVIDER_ID.fullmatch(provider_id):
            raise ValueError("Invalid SSO provider ID")
        if kind not in ("dingtalk", "wecom"):
            raise ValueError("Unknown SSO provider kind")
        if not enterprise_id.strip() or not app_id.strip():
            raise ValueError("Enterprise ID and application ID required")
        with self.store.connection() as conn:
            old = conn.execute(
                "SELECT secret_ref FROM sso_auth_providers WHERE id=?", (provider_id,)
            ).fetchone()
        if secret:
            encrypted = "fernet:" + _fernet(
                self.key_path, create=old is None
            ).encrypt(secret.encode()).decode()
        elif old is not None:
            encrypted = str(old["secret_ref"])
        else:
            raise ValueError("New SSO provider requires AppSecret")
        with self.store.connection(write=True) as conn:
            conn.execute(
                """
                INSERT INTO sso_auth_providers
                    (id,kind,enterprise_id,app_id,secret_ref,enabled,created_at,updated_at)
                VALUES (?,?,?,?,?,?,datetime('now'),datetime('now'))
                ON CONFLICT(id) DO UPDATE SET
                    kind=excluded.kind,
                    enterprise_id=excluded.enterprise_id,
                    app_id=excluded.app_id,
                    secret_ref=excluded.secret_ref,
                    enabled=excluded.enabled,
                    updated_at=excluded.updated_at
                """,
                (provider_id,kind,enterprise_id.strip(),app_id.strip(),encrypted,int(enabled)),
            )

    def delete(self, provider_id: str) -> None:
        with self.store.connection(write=True) as conn:
            conn.execute("DELETE FROM sso_auth_providers WHERE id=?", (str(provider_id),))
