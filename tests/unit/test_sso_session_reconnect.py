"""Durable OAuth sessions: OpenVPN verifies certificates, NiceVPN caches only SSO."""
import json
from urllib.parse import parse_qs, urlsplit

import pytest

from app.models.config import SystemConfig
from app.services.auth.enterprise_sso_policy import SsoPolicyService
from app.services.auth.office_oauth import EnterpriseIdentity
from app.services.auth.openvpn_webauth import ClientNotification
from app.services.auth.provider_store import SsoProviderStore
from app.services.auth.sso_audit import SsoAudit
from app.services.auth.sso_runtime import SsoRuntime
from app.services.auth.sso_sessions import (
    SSO_SESSION_TTL_SECONDS, SsoSessionStore, verified_peer_fingerprint,
)
from app.services.auth.sso_transactions import WebAuthTransactionStore
from app.storage.sqlite_store import SqliteStore

FP_A = "aa" * 32
FP_B = "bb" * 32


class FakeManager:
    instance_id = "server"

    def __init__(self):
        self.commands = []

    def send(self, command):
        self.commands.append(command)


def make_runtime(store, providers, policy, tmp_path):
    runtime = SsoRuntime(
        instances=["server"], public_base_url="https://vpn.example.org",
        policy=policy, providers=providers,
        transactions=WebAuthTransactionStore(store),
    )
    runtime.audit = SsoAudit(log_path=tmp_path / "sso-events.jsonl")
    mgr = FakeManager()
    runtime.instances["server"] = mgr
    return runtime, mgr


def connect(cid, *, fingerprint=FP_A, kind="CONNECT", kid=0, port="59000",
            name="alice", webauth=True):
    env = {
        "common_name": name, "untrusted_ip": "203.0.113.2",
        "untrusted_port": port,
    }
    if fingerprint:
        env["tls_digest_sha256_0"] = fingerprint
    if webauth:
        env["IV_SSO"] = "webauth"
    return ClientNotification(kind, cid, kid, env)


@pytest.fixture
def setup(tmp_path, monkeypatch):
    store = SqliteStore(tmp_path / "store.db")
    store.upsert_group({
        "id": "g1", "name": "研发", "status": "active",
        "subnet": "10.8.0.0/24",
    })
    store.upsert_user({
        "username": "alice", "group_id": "g1", "status": "active",
    })
    policy = SsoPolicyService(store)
    policy.set_group_required("g1", required=True)
    providers = SsoProviderStore(store, key_path=tmp_path / "secret.key")
    providers.save_unified(app_id="app1", secret="secret")
    allowed = [{"corp_id": "dingAllowed", "name": "供应商"}]
    monkeypatch.setattr(
        "app.services.auth.sso_runtime.load_config",
        lambda: SystemConfig(enterprise_sso_allowed_corps=allowed).model_dump(),
    )
    grants = []
    monkeypatch.setattr(
        "app.services.auth.sso_runtime.write_sso_grant",
        lambda *args, **kwargs: grants.append((args, kwargs)),
    )
    return store, providers, policy, tmp_path, allowed, grants


def approve(runtime, manager, cid, fingerprint=FP_A):
    runtime.on_event(manager, connect(cid, fingerprint=fingerprint))
    assert manager.commands[-1].startswith("client-pending-auth")
    url = manager.commands[-1].split("WEB_AUTH:external:", 1)[1].rsplit(" ", 1)[0]
    token = parse_qs(urlsplit(url).query)["t"][0]
    state = WebAuthTransactionStore.state_for_token(token)
    req = runtime.transactions.get_pending_by_token(token)
    assert runtime.finish(
        req, state, EnterpriseIdentity("dingtalk", "dingAllowed", ""),
    )
    assert manager.commands[-1] == f"client-auth-nt {cid} 0\n"


def test_fingerprint_is_only_trusted_management_sha256_not_cn_or_iv():
    assert verified_peer_fingerprint({"tls_digest_sha256_0": FP_A}) == FP_A
    assert verified_peer_fingerprint({
        "tls_digest_sha256_0": ":".join(["AA"] * 32)
    }) == FP_A
    assert verified_peer_fingerprint({
        "IV_HWADDR": FP_A, "common_name": "alice",
    }) == ""
    assert verified_peer_fingerprint({"tls_digest_sha256_0": "invalid"}) == ""


def test_fixed_expiry_and_revoke_without_certificate_validation(tmp_path):
    store = SsoSessionStore(SqliteStore(tmp_path / "cache.db"))
    session = store.remember(
        instance_id="server", common_name="alice",
        cert_fingerprint=FP_A, provider_id="dingtalk_unified",
        provider_app_id="app1", enterprise_id="dingAllowed", now=1000,
    )
    assert session.expires_at == 1000 + SSO_SESSION_TTL_SECONDS
    assert store.get(
        instance_id="server", common_name="alice",
        cert_fingerprint=FP_A, provider_id="dingtalk_unified",
        provider_app_id="app1", now=1001,
    ) is not None
    assert store.get(
        instance_id="server", common_name="alice",
        cert_fingerprint=FP_A, provider_id="dingtalk_unified",
        provider_app_id="app1", now=1000 + SSO_SESSION_TTL_SECONDS,
    ) is None
    assert store.get(
        instance_id="server", common_name="alice",
        cert_fingerprint=FP_B, provider_id="dingtalk_unified",
        provider_app_id="app1", now=1001,
    ) is None
    assert store.get(
        instance_id="server", common_name="alice",
        cert_fingerprint=FP_A, provider_id="dingtalk_unified",
        provider_app_id="app2", now=1001,
    ) is None
    assert store.revoke(instance_id="server", common_name="alice") == 1
    assert store.revoke(instance_id="server", common_name="alice") == 0
    with pytest.raises(ValueError):
        store.remember(
            instance_id="server", common_name="alice", cert_fingerprint="bad",
            provider_id="p", provider_app_id="app", enterprise_id="corp",
        )


def test_restart_reconnect_uses_session_and_reissues_device_bypass(setup):
    db, providers, policy, tmp_path, allowed, grants = setup
    runtime, manager = make_runtime(db, providers, policy, tmp_path)
    approve(runtime, manager, 11)
    assert len(grants) == 1
    assert runtime.session_cache.get(
        instance_id="server", common_name="alice",
        cert_fingerprint=FP_A, provider_id="dingtalk_unified",
        provider_app_id="app1",
    ) is not None

    # Simulate OpenVPN/NiceVPN restart. CID, KID and source port are new.
    runtime.clear_instance("server")
    runtime2, manager2 = make_runtime(db, providers, policy, tmp_path)
    runtime2.on_event(manager2, connect(900, kid=4, port="60009"))
    assert manager2.commands == ["client-auth-nt 900 4\n"]
    assert len(grants) == 2
    assert grants[-1][1]["peer_port"] == "60009"
    assert not runtime2.pending
    runtime2.on_event(manager2, ClientNotification(
        "ESTABLISHED", 900,
    ))
    assert not runtime2.establish_timings

    # TLS renegotiation of the live connection also skips browser login,
    # including when REAUTH does not repeat the digest env field.
    runtime2.on_event(manager2, connect(
        900, kind="REAUTH", kid=5, fingerprint="",
    ))
    assert manager2.commands[-1] == "client-auth-nt 900 5\n"
    assert len(grants) == 2
    events = [json.loads(line) for line in (
        tmp_path / "sso-events.jsonl"
    ).read_text(encoding="utf-8").splitlines()]
    assert any(event["reason_code"] == "cached_connect" for event in events)


def test_wrong_cert_or_missing_fingerprint_does_not_bypass_oauth(setup):
    db, providers, policy, tmp_path, _, grants = setup
    runtime, manager = make_runtime(db, providers, policy, tmp_path)
    approve(runtime, manager, 20)
    runtime2, manager2 = make_runtime(db, providers, policy, tmp_path)
    runtime2.on_event(manager2, connect(21, fingerprint=FP_B))
    assert manager2.commands[-1].startswith("client-pending-auth")
    runtime2.on_event(manager2, connect(22, fingerprint=""))
    assert manager2.commands[-1].startswith("client-pending-auth")
    runtime2.on_event(manager2, connect(
        23, fingerprint=FP_A, webauth=False,
    ))
    assert manager2.commands[-1].startswith("client-deny")
    assert len(grants) == 1


def test_enterprise_removed_or_provider_changed_invalidates_cache(setup):
    db, providers, policy, tmp_path, allowed, grants = setup
    runtime, manager = make_runtime(db, providers, policy, tmp_path)
    approve(runtime, manager, 30)
    allowed.clear()
    runtime2, mgr2 = make_runtime(db, providers, policy, tmp_path)
    runtime2.on_event(mgr2, connect(31))
    assert mgr2.commands[-1].startswith("client-pending-auth")

    allowed.append({"corp_id": "dingAllowed", "name": "供应商"})
    providers.save_unified(app_id="app2", secret="new-secret")
    runtime3, mgr3 = make_runtime(db, providers, policy, tmp_path)
    runtime3.on_event(mgr3, connect(32))
    assert mgr3.commands[-1].startswith("client-pending-auth")
    assert len(grants) == 1


def test_revoke_clears_persisted_cache_and_blocks_live_tls_reauth(setup):
    db, providers, policy, tmp_path, _, grants = setup
    runtime, manager = make_runtime(db, providers, policy, tmp_path)
    approve(runtime, manager, 50)
    count = runtime.session_cache.revoke(instance_id="server", common_name="alice")
    assert count == 1
    runtime.on_event(manager, connect(50, kind="REAUTH", kid=1))
    assert manager.commands[-1].startswith("client-pending-auth")
    runtime2, manager2 = make_runtime(db, providers, policy, tmp_path)
    runtime2.on_event(manager2, connect(51))
    assert manager2.commands[-1].startswith("client-pending-auth")
    assert len(grants) == 1


def test_non_sso_group_keeps_original_flow_without_cache(setup):
    db, providers, policy, tmp_path, _, grants = setup
    policy.set_group_required("g1", required=False)
    runtime, manager = make_runtime(db, providers, policy, tmp_path)
    runtime.on_event(manager, connect(60))
    assert manager.commands == ["client-auth-nt 60 0\n"]
    with db.connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM sso_auth_sessions").fetchone()[0] == 0
    assert not grants
