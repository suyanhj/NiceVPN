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
UNIFIED_DINGTALK_ID = "dingtalk_unified"
UNIFIED_CORP_MARKER = "__unified__"
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
                "SELECT id,kind,enterprise_id,app_id,display_name,enabled,read_user_profile,created_at,updated_at "
                "FROM sso_auth_providers ORDER BY kind,id"
            ).fetchall()
        return [
            {**dict(row), "enabled": bool(row["enabled"]),
             "read_user_profile": bool(row["read_user_profile"])}
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
            read_user_profile=bool(row["read_user_profile"]),
        )
        result.validate()
        return result

    def get_unified(self) -> OfficeProvider | None:
        return self.get(UNIFIED_DINGTALK_ID)

    def has_unified(self) -> bool:
        return any(row["id"] == UNIFIED_DINGTALK_ID for row in self.list_metadata())

    def enabled_metadata(self) -> list[dict]:
        rows = self.list_metadata()
        unified = next((row for row in rows if row["id"] == UNIFIED_DINGTALK_ID), None)
        if unified is not None:
            return [unified] if unified["enabled"] else []
        return [row for row in rows if row["enabled"]]

    def first_enabled(self) -> OfficeProvider | None:
        enabled = self.enabled_metadata()
        if not enabled:
            return None
        return self.get(enabled[0]["id"])

    def save_unified(self, *, app_id: str, secret: str | None = None,
                     enabled: bool = True, read_user_profile: bool = False) -> None:
        self.save(
            provider_id=UNIFIED_DINGTALK_ID, kind="dingtalk",
            enterprise_id=UNIFIED_CORP_MARKER, app_id=app_id,
            secret=secret, enabled=enabled,
            display_name="统一钉钉登录", read_user_profile=read_user_profile,
        )

    def legacy_dingtalk_companies(self) -> list[dict]:
        """Safe migration preview: no secrets or credentials are returned."""
        return [
            {"corp_id": row["enterprise_id"], "name": row["display_name"]}
            for row in self.list_metadata()
            if row["kind"] == "dingtalk" and row["id"] != UNIFIED_DINGTALK_ID
            and row["enabled"]
        ]

    def import_legacy_dingtalk(self) -> list[dict]:
        """Explicit admin-triggered copy; leave old provider rows untouched."""
        if self.has_unified():
            raise ValueError("统一钉钉应用已配置，不覆盖现有凭据")
        old_rows = [
            row for row in self.list_metadata()
            if row["kind"] == "dingtalk" and row["id"] != UNIFIED_DINGTALK_ID
            and row["enabled"]
        ]
        if not old_rows:
            raise ValueError("没有可迁移的钉钉应用")
        provider = self.get(old_rows[0]["id"])
        if provider is None:
            raise ValueError("历史钉钉应用不可用")
        self.save_unified(app_id=provider.app_id, secret=provider.secret,
                          read_user_profile=provider.read_user_profile)
        return self.legacy_dingtalk_companies()

    def save(self, *, provider_id: str, kind: str, enterprise_id: str,
             app_id: str, secret: str | None = None, enabled: bool = False,
             display_name: str = "", read_user_profile: bool = False) -> None:
        """Admin-only service method, never exposed on anonymous Public API."""
        if not _PROVIDER_ID.fullmatch(provider_id):
            raise ValueError("Invalid SSO provider ID")
        if kind not in ("dingtalk", "wecom"):
            raise ValueError("Unknown SSO provider kind")
        if not enterprise_id.strip() or not app_id.strip():
            raise ValueError("Enterprise ID and application ID required")
        display_name = str(display_name or "").strip()
        with self.store.connection() as conn:
            old = conn.execute(
                "SELECT secret_ref,display_name FROM sso_auth_providers WHERE id=?",
                (provider_id,),
            ).fetchone()
        if not display_name:
            display_name = str(old["display_name"] or provider_id) if old else provider_id
        if len(display_name) > 80 or any(ord(ch) < 32 for ch in display_name):
            raise ValueError("Invalid enterprise display name")
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
                    (id,kind,enterprise_id,app_id,secret_ref,enabled,display_name,read_user_profile,created_at,updated_at)
                VALUES (?,?,?,?,?,?,?,?,datetime('now'),datetime('now'))
                ON CONFLICT(id) DO UPDATE SET
                    kind=excluded.kind,
                    enterprise_id=excluded.enterprise_id,
                    app_id=excluded.app_id,
                    secret_ref=excluded.secret_ref,
                    enabled=excluded.enabled,
                    display_name=excluded.display_name,
                    read_user_profile=excluded.read_user_profile,
                    updated_at=excluded.updated_at
                """,
                (provider_id,kind,enterprise_id.strip(),app_id.strip(),encrypted,int(enabled),display_name,int(bool(read_user_profile))),
            )

    def cleanup_unified_legacy(self) -> int:
        """Prune obsolete per-company credentials only after unified app is enabled.

        One transaction revokes pending legacy provider logins and deletes
        encrypted legacy rows. Older installations without a unified app
        retain their legacy providers until explicitly switched.
        """
        with self.store.connection(write=True) as conn:
            unified = conn.execute(
                "SELECT enabled FROM sso_auth_providers WHERE id=?",
                (UNIFIED_DINGTALK_ID,),
            ).fetchone()
            if unified is None or not bool(unified["enabled"]):
                raise ValueError("统一钉钉认证未启用，不能清理旧企业凭证")
            conn.execute(
                "UPDATE sso_login_transactions SET status='denied', "
                "completed_at=CAST(strftime('%s','now') AS INTEGER) "
                "WHERE status='pending' AND provider_id!=?",
                (UNIFIED_DINGTALK_ID,),
            )
            changed = conn.execute(
                "DELETE FROM sso_auth_providers WHERE id!=?",
                (UNIFIED_DINGTALK_ID,),
            )
            return int(changed.rowcount)

    def delete(self, provider_id: str) -> None:
        with self.store.connection(write=True) as conn:
            conn.execute("DELETE FROM sso_auth_providers WHERE id=?", (str(provider_id),))
