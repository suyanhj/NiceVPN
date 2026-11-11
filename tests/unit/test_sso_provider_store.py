"""Provider app secrets are encrypted at rest and withheld from UI metadata."""
import os

import pytest

from app.services.auth.provider_store import (
    ProviderSecretError, SsoProviderStore, UNIFIED_DINGTALK_ID, UNIFIED_WECOM_ID,
)
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

def test_multiple_subsidiaries_have_independent_credentials_and_labels(tmp_path):
    store = SsoProviderStore(
        SqliteStore(tmp_path / "group.db"),
        key_path=tmp_path / "company-secrets.key",
    )
    store.save(
        provider_id="guangzhou", display_name="广州子公司", kind="dingtalk",
        enterprise_id="corp-gz", app_id="app-gz", secret="secret-gz", enabled=True,
    )
    store.save(
        provider_id="shenzhen", display_name="深圳分公司", kind="dingtalk",
        enterprise_id="corp-sz", app_id="app-sz", secret="secret-sz", enabled=True,
    )
    metadata = store.list_metadata()
    assert {item["display_name"] for item in metadata} == {"广州子公司", "深圳分公司"}
    assert all("secret_ref" not in item for item in metadata)
    assert store.get("guangzhou").enterprise_id == "corp-gz"
    assert store.get("shenzhen").enterprise_id == "corp-sz"
    assert store.get("guangzhou").secret == "secret-gz"
    assert store.get("shenzhen").secret == "secret-sz"
    assert store.get("guangzhou").read_user_profile is False
    store.save(
        provider_id="guangzhou", display_name="广州子公司", kind="dingtalk",
        enterprise_id="corp-gz", app_id="app-gz", enabled=True,
        read_user_profile=True,
    )
    assert store.get("guangzhou").read_user_profile is True
    assert next(row for row in store.list_metadata() if row["id"] == "guangzhou")["read_user_profile"] is True
    store.save(
        provider_id="shenzhen", display_name="深圳分公司（停用）",
        kind="dingtalk", enterprise_id="corp-sz",
        app_id="app-sz", enabled=False,
    )
    assert store.get("shenzhen") is None
    assert store.get("shenzhen", require_enabled=False).secret == "secret-sz"
    assert store.get("guangzhou").secret == "secret-gz"


def test_only_selected_authentication_method_is_active(tmp_path):
    store = SsoProviderStore(
        SqliteStore(tmp_path / "switch.db"),
        key_path=tmp_path / "auth.key",
    )
    store.save_unified(app_id="ding-app", secret="ding-secret")
    store.activate_only(UNIFIED_DINGTALK_ID)
    assert [row["id"] for row in store.enabled_metadata()] == [UNIFIED_DINGTALK_ID]
    store.save_wecom_unified(
        corp_id="ww-corp", agent_id="10001", secret="wecom-secret",
    )
    store.activate_only(UNIFIED_WECOM_ID)
    assert [row["id"] for row in store.enabled_metadata()] == [UNIFIED_WECOM_ID]
    assert store.first_enabled().enterprise_id == "ww-corp"
    assert store.get(UNIFIED_DINGTALK_ID, require_enabled=False).secret == "ding-secret"
    store.activate_only(UNIFIED_DINGTALK_ID)
    assert [row["id"] for row in store.enabled_metadata()] == [UNIFIED_DINGTALK_ID]
    assert store.get(UNIFIED_WECOM_ID, require_enabled=False).secret == "wecom-secret"


def test_unified_dingtalk_ignores_obsolete_profile_flag(tmp_path):
    store = SsoProviderStore(
        SqliteStore(tmp_path / "profile.db"),
        key_path=tmp_path / "auth.key",
    )
    store.save_unified(
        app_id="ding-app", secret="ding-secret", read_user_profile=True,
    )
    assert store.get_unified().read_user_profile is False
