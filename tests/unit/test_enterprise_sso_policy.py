"""Enterprise SSO policy uses certificate CN, groups and tags only."""
import pytest

from app.services.auth.enterprise_sso_policy import SsoPolicyService
from app.storage.sqlite_store import SqliteStore


@pytest.fixture
def policy(tmp_path):
    store = SqliteStore(tmp_path / "sso.db")
    store.upsert_group({"id": "g1", "name": "研发", "status": "active",
                        "subnet": "10.8.0.0/24"})
    store.upsert_user({"username": "alice", "group_id": "g1", "status": "active"})
    store.upsert_user({"username": "bob", "group_id": "g1", "status": "disabled"})
    return store, SsoPolicyService(store)


def test_default_off_is_legacy_even_for_unknown_cn(policy):
    _, svc = policy
    assert svc.resolve("external", enabled=False).mode == "legacy"
    assert svc.resolve("alice", enabled=True).mode == "legacy"


def test_group_can_require_sso_without_mapping_dingtalk_user(policy):
    _, svc = policy
    svc.set_group_required("g1", required=True)
    answer = svc.resolve("alice", enabled=True)
    assert answer.requires_sso
    assert answer.sources == ("group:g1",)
    svc.set_group_required("g1", required=False)
    assert svc.resolve("alice", enabled=True).mode == "legacy"


def test_user_and_group_tags_require_sso_and_dedupe(policy):
    store, svc = policy
    store.create_tag("t1", "办公")
    svc.set_tag_required("t1", required=True)
    store.set_tag_members("t1", usernames=["alice"], group_ids=["g1"])
    assert svc.resolve("alice", enabled=True).sources == ("tag:t1",)
    store.set_tag_members("t1", usernames=[], group_ids=[])
    assert svc.resolve("alice", enabled=True).mode == "legacy"


def test_group_and_tag_requirements_union(policy):
    store, svc = policy
    svc.set_group_required("g1", required=True)
    store.create_tag("t2", "高权限")
    svc.set_tag_required("t2", required=True)
    store.replace_entity_tags("user", "alice", ["t2"])
    assert svc.resolve("alice", enabled=True).sources == ("group:g1", "tag:t2")


def test_certificate_exemption_overrides_group_only_and_can_be_revoked(policy):
    store, svc = policy
    svc.set_group_required("g1", required=True)
    with pytest.raises(ValueError, match="reason"):
        svc.set_certificate_exemption("alice", exempt=True)
    svc.set_certificate_exemption("alice", exempt=True, reason="Linux CLI")
    assert svc.resolve("alice", enabled=True).mode == "exempt"
    svc.set_certificate_exemption("alice", exempt=False)
    assert svc.resolve("alice", enabled=True).mode == "required"


def test_unknown_and_inactive_certificates_fail_closed(policy):
    _, svc = policy
    assert svc.resolve("missing", enabled=True).denied
    assert svc.resolve("bob", enabled=True).denied
    assert svc.resolve("\n", enabled=True).denied
    with pytest.raises(ValueError):
        svc.set_certificate_exemption("missing", exempt=True, reason="not known")


def test_inactive_group_fails_closed_even_for_exempt_cert(policy):
    store, svc = policy
    svc.set_certificate_exemption("alice", exempt=True, reason="approved server")
    store.upsert_group({"id": "g1", "name": "研发", "status": "disabled",
                        "subnet": "10.8.0.0/24"})
    assert svc.resolve("alice", enabled=True).denied


def test_schema_v6_contains_required_policy_tables(policy):
    store, _ = policy
    assert store.schema_version() == 6
    with store.connection() as conn:
        tables = {str(row[0]) for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
        assert {"sso_auth_providers", "sso_certificate_exemptions",
                "sso_login_transactions", "sso_auth_audit"} <= tables
        tag_columns = {str(row["name"]) for row in conn.execute(
            "PRAGMA table_info(tags)"
        )}
        assert "sso_required" in tag_columns
