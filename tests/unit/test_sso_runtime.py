"""Real OpenVPN daemon not required for isolated SSO Management tests."""
import time
import threading
from pathlib import Path

import pytest

from app.services.auth.office_oauth import EnterpriseIdentity
from app.services.auth.openvpn_webauth import ClientNotification
from app.services.auth.sso_runtime import ManagementInstance, SsoRuntime
from app.services.auth.enterprise_sso_policy import SsoPolicyService
from app.services.auth.provider_store import SsoProviderStore
from app.services.auth.sso_transactions import WebAuthTransactionStore
from app.storage.sqlite_store import SqliteStore


class FakeManager:
    instance_id = "server"

    def __init__(self):
        self.commands = []

    def send(self, command):
        self.commands.append(command)


@pytest.fixture
def bundle(tmp_path):
    db = SqliteStore(tmp_path / "db.sqlite")
    db.upsert_group({"id": "eng", "name": "研发", "status": "active",
                     "subnet": "10.8.0.0/24"})
    db.upsert_user({"username": "cert-1", "group_id": "eng", "status": "active"})
    policy = SsoPolicyService(db)
    providers = SsoProviderStore(db, key_path=tmp_path / "secret.key")
    providers.save(provider_id="corp", kind="dingtalk", enterprise_id="goodcorp",
                   app_id="app", secret="secret", enabled=True)
    transactions = WebAuthTransactionStore(db)
    runtime = SsoRuntime(instances=["server"], public_base_url="https://vpn.example.org",
                         policy=policy, providers=providers, transactions=transactions)
    return runtime, policy


def test_required_sso_only_after_valid_enterprise_callback(bundle):
    runtime, policy = bundle
    policy.set_group_required("eng", required=True)
    mgr = FakeManager()
    event = ClientNotification("CONNECT", 17, 0, {
        "common_name": "cert-1", "IV_SSO": "webauth",
    })
    runtime.on_event(mgr, event)
    assert mgr.commands[0].startswith("client-pending-auth 17 0 WEB_AUTH:")
    assert not any(c.startswith("client-auth-nt") for c in mgr.commands)
    with runtime.transactions.store.connection() as conn:
        row = conn.execute("SELECT * FROM sso_login_transactions").fetchone()
        assert row["status"] == "pending"
    # The runtime cannot approve without both the exact random OAuth state
    # and the matching trusted office provider result.
    req = runtime.transactions._as_request(row)
    assert not runtime.finish(req, "untrusted", EnterpriseIdentity("dingtalk", "goodcorp", "alice"))
    assert not any(c.startswith("client-auth-nt") for c in mgr.commands)


def test_legacy_when_group_is_not_opted_in_and_unsupported_client(bundle):
    runtime, policy = bundle
    mgr = FakeManager()
    normal = ClientNotification("CONNECT", 1, 0, {"common_name": "cert-1"})
    runtime.on_event(mgr, normal)
    assert mgr.commands == ["client-auth-nt 1 0\n"]
    policy.set_group_required("eng", required=True)
    runtime.on_event(mgr, normal)
    assert mgr.commands[-1].startswith("client-deny")
    policy.set_group_required("eng", required=False)
    runtime.on_event(mgr, normal)
    assert mgr.commands[-1] == "client-auth-nt 1 0\n"

def test_all_users_switch_requires_sso_for_unmanaged_certificate(bundle, monkeypatch):
    runtime, _ = bundle
    mgr = FakeManager()
    monkeypatch.setattr(
        "app.services.auth.sso_runtime.load_config",
        lambda: {"enterprise_sso_all_users": True},
    )
    runtime.on_event(mgr, ClientNotification(
        "CONNECT", 89, 0,
        {"common_name": "unmanaged-cert", "IV_SSO": "webauth"},
    ))
    assert mgr.commands[-1].startswith("client-pending-auth 89 0 WEB_AUTH:")
    runtime.on_event(mgr, ClientNotification(
        "CONNECT", 90, 0, {"common_name": "unmanaged-cert"},
    ))
    assert mgr.commands[-1].startswith("client-deny 90 0")


def test_disconnect_drops_active_pending_state(bundle):
    runtime, policy = bundle
    policy.set_group_required("eng", required=True)
    mgr = FakeManager()
    runtime.on_event(mgr, ClientNotification(
        "CONNECT", 55, 0, {"common_name": "cert-1", "IV_SSO": "webauth"}))
    assert runtime.pending
    runtime.on_event(mgr, ClientNotification("DISCONNECT", 55))
    assert not runtime.pending
    assert not runtime.sessions


def test_public_origin_accepts_http_https_ip_and_port(bundle):
    _, policy = bundle
    for url in (
        "http://127.0.0.1:8881",
        "http://192.168.10.10:8881",
        "http://vpn.example.org",
        "https://vpn.example.org",
    ):
        runtime = SsoRuntime(instances=["server"], public_base_url=url, policy=policy)
        assert runtime.public_base_url == url


def test_public_origin_rejects_bad_urls(bundle):
    _, policy = bundle
    for url in (
        "ftp://vpn.example.org",
        "http://vpn.example.org/auth",
        "http://vpn.example.org?token=bad",
        "http://user:pass@vpn.example.org",
        "http://127.0.0.1:wrong",
        "http://127.0.0.1:8881/#fragment",
    ):
        with pytest.raises(ValueError):
            SsoRuntime(instances=["server"], public_base_url=url, policy=policy)


def test_management_disconnect_uses_client_id_and_confirms_status_absence(tmp_path):
    runtime = type("RuntimeStub", (), {"stop_event": threading.Event()})()
    manager = ManagementInstance("server", tmp_path / "management.sock", runtime)
    manager.socket = object()
    snapshots = iter([
        ({42: ("cert-1", 1700000000)}, 1.0),
        ({}, 2.0),
    ])
    commands = []
    manager.wait_for_status = lambda *, after, timeout: next(snapshots, None)
    manager.send = commands.append

    assert manager.disconnect_common_name("cert-1", timeout=1.0) == (1, 1, 0)
    assert commands == ["client-kill 42\n"]


def test_management_disconnect_reports_unconfirmed_if_client_remains_online(tmp_path):
    runtime = type("RuntimeStub", (), {"stop_event": threading.Event()})()
    manager = ManagementInstance("server", tmp_path / "management.sock", runtime)
    manager.socket = object()
    snapshots = iter([
        ({42: ("cert-1", 1700000000)}, 1.0),
        ({42: ("cert-1", 1700000000)}, 2.0),
    ])
    commands = []
    manager.wait_for_status = lambda *, after, timeout: next(snapshots, None)
    manager.send = commands.append

    assert manager.disconnect_common_name("cert-1", timeout=0.1) == (1, 0, 1)
    assert commands == ["client-kill 42\n", "client-kill 42\n"]


def test_management_disconnect_offline_user_is_noop(tmp_path):
    runtime = type("RuntimeStub", (), {"stop_event": threading.Event()})()
    manager = ManagementInstance("server", tmp_path / "management.sock", runtime)
    manager.socket = object()
    manager.wait_for_status = lambda *, after, timeout: ({}, 1.0)
    commands = []
    manager.send = commands.append

    assert manager.disconnect_common_name("offline-user", timeout=1.0) == (0, 0, 0)
    assert commands == []


def test_management_disconnect_does_not_kill_a_new_reconnect_cid(tmp_path):
    runtime = type("RuntimeStub", (), {"stop_event": threading.Event()})()
    manager = ManagementInstance("server", tmp_path / "management.sock", runtime)
    manager.socket = object()
    snapshots = iter([
        ({42: ("cert-1", 1700000000)}, 1.0),
        ({43: ("cert-1", 1700000010)}, 2.0),
    ])
    commands = []
    manager.wait_for_status = lambda *, after, timeout: next(snapshots, None)
    manager.send = commands.append

    assert manager.disconnect_common_name("cert-1", timeout=1.0) == (1, 1, 0)
    assert commands == ["client-kill 42\n"]


def test_management_disconnect_fails_fast_without_live_management_socket(tmp_path):
    runtime = type("RuntimeStub", (), {"stop_event": threading.Event()})()
    manager = ManagementInstance("server", tmp_path / "management.sock", runtime)

    assert manager.disconnect_common_name("cert-1", timeout=1.0) == (0, 0, 1)
