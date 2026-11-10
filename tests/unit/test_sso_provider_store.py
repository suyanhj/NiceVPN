"""Provider app secrets are encrypted at rest and withheld from UI metadata."""
import os

import pytest

from app.services.auth.provider_store import ProviderSecretError, SsoProviderStore
from app.storage.sqlite_store import SqliteStore


def test_encrypted_provider_crud(tmp_path):
    store = SsoProviderStore(SqliteStore(tmp_path / "db.sqlite"),
                             key_path=tmp_path / "secrets.key")
    store.save(provider_id="dd", kind="dingtalk", enterprise_id="corp1",
               app_id="app1", secret="supersecret", enabled=True)
    metadata = store.list_metadata()[0]
    assert "secret" not in repr(metadata).lower()
    assert store.key_path.read_bytes() != b"supersecret"
    if os.name != "nt":
        assert (store.key_path.stat().st_mode & 0o777) == 0o600
    with store.store.connection() as conn:
        cipher = conn.execute("SELECT secret_ref FROM sso_auth_providers").fetchone()[0]
    assert "supersecret" not in cipher
    assert cipher.startswith("fernet:")
    assert store.get("dd").secret == "supersecret"
    store.save(provider_id="dd", kind="dingtalk", enterprise_id="corp1",
               app_id="app2", enabled=False)
    assert store.get("dd") is None
    assert store.get("dd", require_enabled=False).secret == "supersecret"
    store.delete("dd")
    assert store.list_metadata() == []


def test_missing_key_fails_closed(tmp_path):
    store = SsoProviderStore(SqliteStore(tmp_path / "db.sqlite"),
                             key_path=tmp_path / "sso.key")
    store.save(provider_id="wx", kind="wecom", enterprise_id="corp",
               app_id="agent", secret="secret", enabled=True)
    store.key_path.unlink()
    with pytest.raises(ProviderSecretError, match="missing"):
        store.get("wx")
    assert not store.key_path.exists()


def test_invalid_provider_does_not_create_key(tmp_path):
    store = SsoProviderStore(SqliteStore(tmp_path / "db.sqlite"),
                             key_path=tmp_path / "sso.key")
    with pytest.raises(ValueError):
        store.save(provider_id="../bad", kind="dingtalk", enterprise_id="corp",
                   app_id="app", secret="secret", enabled=True)
    assert not store.key_path.exists()
