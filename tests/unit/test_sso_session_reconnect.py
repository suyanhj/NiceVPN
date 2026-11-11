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
    SSO_SESSION_TTL_SECONDS, OnlineSsoBinding, SsoSessionStore,
    verified_peer_fingerprint,
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


def test_offline_expiry_and_revoke_without_certificate_validation(tmp_path):
    assert SSO_SESSION_TTL_SECONDS == 3600
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


def test_admin_reset_revokes_all_user_sessions_and_forces_new_oauth(setup):
    store, providers, policy, root, _, _ = setup
    runtime, manager = make_runtime(store, providers, policy, root)
    backup_manager = FakeManager()
    runtime.instances["backup"] = backup_manager
    approve(runtime, manager, 12)
    runtime.session_cache.remember(
        instance_id="backup", common_name="alice",
        cert_fingerprint=FP_B, provider_id="dingtalk_unified",
        provider_app_id="app1", enterprise_id="dingAllowed",
    )
    runtime.session_cache.remember(
        instance_id="server", common_name="bob",
        cert_fingerprint=FP_B, provider_id="dingtalk_unified",
        provider_app_id="app1", enterprise_id="dingAllowed",
    )

    # A browser window may already be in the middle of another OAuth flow.
    runtime.on_event(manager, connect(13, fingerprint=FP_B))
    assert runtime.pending
    revoked, canceled, sent = runtime.revoke_user_session("alice")
    assert (revoked, canceled, sent) == (2, 1, True)
    assert not runtime.pending
    assert not runtime.sessions
    assert "kill alice\n" in manager.commands
    assert "kill alice\n" in backup_manager.commands
    assert "client-deny 13 0" in "".join(manager.commands)
    with store.connection() as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM sso_auth_sessions WHERE common_name='alice'"
        ).fetchone()[0] == 0
        assert conn.execute(
            "SELECT COUNT(*) FROM sso_auth_sessions WHERE common_name='bob'"
        ).fetchone()[0] == 1
        assert conn.execute(
            "SELECT status FROM sso_login_transactions WHERE client_id=13"
        ).fetchone()[0] == "denied"

    runtime.on_event(manager, connect(14))
    assert manager.commands[-1].startswith("client-pending-auth 14 0 WEB_AUTH:")


def test_offline_user_reset_revokes_cache_without_running_runtime(setup, monkeypatch):
    from app.services.user.crud import UserService

    store, _, _, _, _, _ = setup
    sessions = SsoSessionStore(store)
    sessions.remember(
        instance_id="server", common_name="alice",
        cert_fingerprint=FP_A, provider_id="dingtalk_unified",
        provider_app_id="app1", enterprise_id="dingAllowed",
    )
    monkeypatch.setattr(
        "app.services.auth.sso_runtime.get_sso_runtime", lambda: None,
    )
    service = UserService(store)
    monkeypatch.setattr(service.audit, "log", lambda *args, **kwargs: None)
    assert service.reset_sso_session("alice") == (1, 0, False)
    assert service.reset_sso_session("alice") == (0, 0, False)
    assert sessions.get(
        instance_id="server", common_name="alice",
        cert_fingerprint=FP_A, provider_id="dingtalk_unified",
        provider_app_id="app1",
    ) is None
    with pytest.raises(ValueError, match="用户不存在"):
        service.reset_sso_session("missing")


def test_online_heartbeat_updates_existing_row_not_append(tmp_path):
    store = SsoSessionStore(SqliteStore(tmp_path / "rolling.db"))
    identity = dict(instance_id="server", common_name="alice",
                    cert_fingerprint=FP_A, provider_id="dingtalk_unified",
                    provider_app_id="app1")
    store.remember(**identity, enterprise_id="dingAllowed", now=1000)
    binding = OnlineSsoBinding("alice", FP_A, "dingtalk_unified", "app1",
                               "dingAllowed", 55, 900)
    for now in (1001, 2000, 3000, 4000, 5000):
        assert store.refresh_online(
            instance_id="server", bindings=[binding], now=now,
        ) == {("alice", FP_A)}
        assert store.get(**identity, now=now + 1) is not None
    with store.store.connection() as conn:
        row = conn.execute("SELECT COUNT(*), MAX(last_used_at), MAX(expires_at), "
                           "MAX(active_client_id), MAX(active_connected_at) "
                           "FROM sso_auth_sessions").fetchone()
        assert tuple(row) == (1, 5000, 5000 + SSO_SESSION_TTL_SECONDS, 55, 900)
    assert store.recover_online(
        instance_id="server", common_name="alice", client_id=55,
        connected_at=900, provider_id="dingtalk_unified",
        provider_app_id="app1", now=5001,
    ) is not None
    assert store.recover_online(
        instance_id="server", common_name="alice", client_id=55,
        connected_at=901, provider_id="dingtalk_unified",
        provider_app_id="app1", now=5001,
    ) is None
    store.disconnected(instance_id="server", client_id=55, now=5001)
    assert store.recover_online(
        instance_id="server", common_name="alice", client_id=55,
        connected_at=900, provider_id="dingtalk_unified",
        provider_app_id="app1", now=5001,
    ) is None
    assert store.get(**identity, now=5001) is not None  # One-hour offline grace.
    assert store.get(**identity, now=5000 + SSO_SESSION_TTL_SECONDS) is not None
    assert store.get(**identity, now=5001 + SSO_SESSION_TTL_SECONDS) is None
    assert store.prune_expired(now=5001 + SSO_SESSION_TTL_SECONDS) == 1


def test_heartbeat_never_resurrects_expired_or_revoked_rows(tmp_path):
    store = SsoSessionStore(SqliteStore(tmp_path / "expired.db"))
    kwargs = dict(instance_id="server", common_name="alice",
                  cert_fingerprint=FP_A, provider_id="dingtalk_unified",
                  provider_app_id="app1", enterprise_id="dingAllowed")
    binding = OnlineSsoBinding("alice", FP_A, "dingtalk_unified", "app1",
                               "dingAllowed", 3, 1000)
    store.remember(**kwargs, now=1000)
    assert not store.refresh_online(instance_id="server", bindings=[binding],
                                    now=1000 + SSO_SESSION_TTL_SECONDS)
    store.remember(**kwargs, now=1000)
    assert store.revoke(instance_id="server", common_name="alice") == 1
    assert not store.refresh_online(instance_id="server", bindings=[binding], now=1001)
    with store.store.connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM sso_auth_sessions").fetchone()[0] == 0


def test_disconnect_does_not_renew_expired_or_revoked_session(tmp_path):
    store = SsoSessionStore(SqliteStore(tmp_path / "disconnect.db"))
    identity = dict(
        instance_id="server", common_name="alice",
        cert_fingerprint=FP_A, provider_id="dingtalk_unified",
        provider_app_id="app1", enterprise_id="dingAllowed",
    )
    binding = OnlineSsoBinding(
        "alice", FP_A, "dingtalk_unified", "app1",
        "dingAllowed", 19, 100,
    )
    store.remember(**identity, now=1000)
    assert store.refresh_online(
        instance_id="server", bindings=[binding], now=1001,
    )
    store.disconnected(instance_id="server", client_id=19, now=5000)
    assert store.get(
        instance_id="server", common_name="alice", cert_fingerprint=FP_A,
        provider_id="dingtalk_unified", provider_app_id="app1", now=5000,
    ) is None

    store.remember(**identity, now=6000)
    assert store.refresh_online(
        instance_id="server", bindings=[binding], now=6001,
    )
    assert store.revoke(instance_id="server", common_name="alice") == 1
    store.disconnected(instance_id="server", client_id=19, now=6002)
    with store.store.connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM sso_auth_sessions").fetchone()[0] == 0


def test_management_online_status_refreshes_and_recovers_after_restart(setup):
    db, providers, policy, tmp_path, _, _ = setup
    runtime, manager = make_runtime(db, providers, policy, tmp_path)
    approve(runtime, manager, 70)
    runtime.on_event(manager, ClientNotification("ESTABLISHED", 70))
    runtime.on_status(manager, {70: ("alice", 100000)})
    with db.connection() as conn:
        row = conn.execute(
            "SELECT active_client_id, active_connected_at FROM sso_auth_sessions"
        ).fetchone()
        assert tuple(row) == (70, 100000)

    # A new NiceVPN process can reattach to the same running OpenVPN tunnel.
    runtime2, manager2 = make_runtime(db, providers, policy, tmp_path)
    runtime2.on_status(manager2, {70: ("alice", 100001)})
    assert not runtime2.sessions  # CID alone cannot restore the identity.
    runtime2.on_status(manager2, {70: ("alice", 100000)})
    assert runtime2.sessions[("server", 70)].cert_fingerprint == FP_A
    assert len(runtime2.sessions) == 1
    for _ in range(30):
        runtime2.on_status(manager2, {70: ("alice", 100000)})
    with db.connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM sso_auth_sessions").fetchone()[0] == 1

    runtime2.on_event(manager2, ClientNotification("DISCONNECT", 70))
    runtime3, manager3 = make_runtime(db, providers, policy, tmp_path)
    runtime3.on_status(manager3, {70: ("alice", 100000)})
    assert not runtime3.sessions


def test_verified_enterprise_name_is_required_for_name_only_cached_reconnect(
    setup, monkeypatch,
):
    db, providers, policy, tmp_path, _, _ = setup
    name_config = {"enterprise_sso_allowed_corps": [
        {"corp_id": "", "name": "可信公司"},
    ]}
    monkeypatch.setattr(
        "app.services.auth.sso_runtime.load_config", lambda: name_config,
    )
    runtime, manager = make_runtime(db, providers, policy, tmp_path)
    runtime.on_event(manager, connect(80))
    token_url = manager.commands[-1].split(
        "WEB_AUTH:external:", 1,
    )[1].rsplit(" ", 1)[0]
    token = parse_qs(urlsplit(token_url).query)["t"][0]
    transaction = runtime.transactions.get_pending_by_token(token)
    assert runtime.finish(
        transaction, WebAuthTransactionStore.state_for_token(token),
        EnterpriseIdentity("dingtalk", "dingNew", "", "可信公司"),
    )
    cached = runtime.session_cache.get(
        instance_id="server", common_name="alice", cert_fingerprint=FP_A,
        provider_id="dingtalk_unified", provider_app_id="app1",
    )
    assert cached.enterprise_name == "可信公司"
    runtime.clear_instance("server")
    restarted, mgr2 = make_runtime(db, providers, policy, tmp_path)
    restarted.on_event(mgr2, connect(81))
    assert mgr2.commands == ["client-auth-nt 81 0\n"]

    # A policy edit revokes name-only reuse even while the row is unexpired.
    name_config["enterprise_sso_allowed_corps"] = [
        {"corp_id": "", "name": "另一家公司"},
    ]
    restarted.on_event(mgr2, connect(82))
    assert mgr2.commands[-1].startswith("client-pending-auth 82 0")


def test_first_management_heartbeat_persists_when_monotonic_is_below_interval(
    setup, monkeypatch,
):
    db, providers, policy, tmp_path, _, _ = setup
    runtime, manager = make_runtime(db, providers, policy, tmp_path)
    approve(runtime, manager, 27)
    runtime.on_event(manager, ClientNotification("ESTABLISHED", 27))
    # Recently started hosts have a monotonic uptime below the 5-minute
    # throttling window; their first heartbeat must not be skipped.
    monkeypatch.setattr(
        "app.services.auth.sso_runtime.time.monotonic", lambda: 123.0,
    )
    runtime.on_status(manager, {27: ("alice", 456789)})
    with db.connection() as conn:
        row = conn.execute(
            "SELECT active_client_id, active_connected_at FROM sso_auth_sessions"
        ).fetchone()
        assert tuple(row) == (27, 456789)
    assert runtime.online_written_at[("server", 27)] == 123.0
    runtime.on_status(manager, {27: ("alice", 456789)})
    with db.connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM sso_auth_sessions").fetchone()[0] == 1

def test_status_poll_does_not_hit_sqlite_per_unknown_client_every_few_seconds(
    setup, monkeypatch,
):
    db, providers, policy, tmp_path, _, _ = setup
    runtime, manager = make_runtime(db, providers, policy, tmp_path)
    queries = []
    original = runtime.session_cache.recover_online

    def counted(**kwargs):
        queries.append(kwargs["connected_at"])
        return original(**kwargs)

    monkeypatch.setattr(runtime.session_cache, "recover_online", counted)
    for _ in range(50):
        runtime.on_status(manager, {12: ("alice", 100000)})
    assert queries == [100000]
    runtime.on_status(manager, {12: ("alice", 100001)})
    assert queries == [100000, 100001]
    runtime.on_event(manager, ClientNotification("DISCONNECT", 12))
    runtime.on_status(manager, {12: ("alice", 100001)})
    assert queries == [100000, 100001, 100001]


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
