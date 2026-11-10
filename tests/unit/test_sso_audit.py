"""SSO audit stores only approved field set; no token fields."""
import pytest

from app.services.auth.sso_audit import SsoAudit
from app.storage.sqlite_store import SqliteStore


def test_write_and_read_sso_audit(tmp_path):
    audit = SsoAudit(SqliteStore(tmp_path / "audit.db"))
    audit.log(
        event_type="approved", outcome="success",
        instance_id="vpn", common_name="cn-1",
        provider_kind="dingtalk", enterprise_id="corp",
        external_user_id="dd-uid", reason_code="enterprise_match",
    )
    rows = audit.recent()
    assert rows[0]["common_name"] == "cn-1"
    assert rows[0]["enterprise_id"] == "corp"
    assert rows[0]["reason_code"] == "enterprise_match"


def test_never_allow_unbounded_or_unknown_audit_events(tmp_path):
    audit = SsoAudit(SqliteStore(tmp_path / "audit.db"))
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
