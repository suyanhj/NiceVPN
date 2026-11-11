"""SSO file audit: structured safe events and bounded daily rotation."""
import json

import pytest

from app.services.auth.sso_audit import SsoAudit, _file_handler


def test_write_and_read_sso_audit_file(tmp_path):
    path = tmp_path / "sso-auth.jsonl"
    audit = SsoAudit(log_path=path)
    audit.log(
        event_type="approved", outcome="success",
        instance_id="vpn", common_name="cn-1",
        provider_kind="dingtalk", enterprise_id="corp",
        external_user_id="dd-uid", reason_code="enterprise_match",
    )
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert rows[0]["common_name"] == "cn-1"
    assert rows[0]["enterprise_id"] == "corp"
    assert rows[0]["external_user_id"] == "dd-uid"
    assert rows[0]["reason_code"] == "enterprise_match"
    assert "access_token" not in path.read_text(encoding="utf-8")
    assert _file_handler(path).backupCount >= 1


def test_never_allow_unbounded_or_unknown_audit_events(tmp_path):
    audit = SsoAudit(log_path=tmp_path / "audit.jsonl")
    with pytest.raises(ValueError):
        audit.log(
            event_type="oauthCode=SECRET", outcome="failure",
            instance_id="vpn", common_name="cn",
        )
    with pytest.raises(ValueError):
        audit.log(
            event_type="denied", outcome="failure",
            instance_id="vpn", common_name="cn\nsecret",
        )
    assert not (tmp_path / "audit.jsonl").exists()


def test_sqlite_store_argument_does_not_retain_audit_rows(tmp_path):
    from app.storage.sqlite_store import SqliteStore
    store = SqliteStore(tmp_path / "legacy.db")
    audit = SsoAudit(store, log_path=tmp_path / "sso-auth.jsonl")
    audit.log(event_type="pending", outcome="pending",
              instance_id="vpn", common_name="cn")
    with store.connection() as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM sqlite_master "
            "WHERE type='table' AND name='sso_auth_audit'"
        ).fetchone()[0] == 0
