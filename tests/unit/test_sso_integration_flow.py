"""In-process WebAuth integration: OpenVPN CONNECT -> browser -> OAuth -> allow.

No real VPN daemon or external office API needed; only explicitly mocked trust
boundaries. This is NOT a replacement for real-client POC acceptance tests.
"""
from urllib.parse import parse_qs, urlsplit

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.sso import router
from app.services.auth.enterprise_sso_policy import SsoPolicyService
from app.services.auth.office_oauth import EnterpriseIdentity
from app.services.auth.openvpn_webauth import ClientNotification
from app.services.auth.provider_store import SsoProviderStore
from app.services.auth.sso_runtime import SsoRuntime
from app.services.auth.sso_transactions import WebAuthTransactionStore
from app.storage.sqlite_store import SqliteStore


class FakeManager:
    instance_id = "server"

    def __init__(self):
        self.commands = []

    def send(self, command):
        self.commands.append(command)


def test_browser_approval_unblocks_exact_pending_client(monkeypatch, tmp_path):
    store = SqliteStore(tmp_path / "sso-integration.db")
    store.upsert_group({
        "id": "eng", "name": "研发", "status": "active", "subnet": "10.8.0.0/24",
    })
    store.upsert_user({
        "username": "vpn-cert", "group_id": "eng", "status": "active",
    })
    policy = SsoPolicyService(store)
    policy.set_group_required("eng", required=True)
    providers = SsoProviderStore(store, key_path=tmp_path / "master.key")
    providers.save(
        provider_id="office", kind="dingtalk",
        enterprise_id="company-1", app_id="ding-key", secret="secret",
        enabled=True,
    )
    runtime = SsoRuntime(
        instances=["server"], public_base_url="https://vpn.example.org",
        policy=policy, providers=providers,
        transactions=WebAuthTransactionStore(store),
    )
    from app.services.auth.sso_audit import SsoAudit
    runtime.audit = SsoAudit(log_path=tmp_path / "integration-sso-auth.jsonl")
    manager = FakeManager()
    runtime.instances["server"] = manager

    event = ClientNotification(
        "CONNECT", 100, 0,
        {
            "common_name": "vpn-cert", "IV_SSO": "webauth",
            "untrusted_ip": "203.0.113.3", "untrusted_port": "58000",
        },
    )
    runtime.on_event(manager, event)
    assert len(manager.commands) == 1
    assert manager.commands[0].startswith(
        "client-pending-auth 100 0 WEB_AUTH:external:https://"
    )
    url = manager.commands[0].split("WEB_AUTH:external:", 1)[1].rsplit(" ", 1)[0]
    token = parse_qs(urlsplit(url).query)["t"][0]

    import app.api.sso as sso
    import app.services.auth.sso_runtime as runtime_module

    monkeypatch.setattr(sso, "get_sso_runtime", lambda: runtime)
    monkeypatch.setattr(runtime_module, "write_sso_grant", lambda *a, **kw: None)

    async def fake_exchange(provider, code):
        assert provider.enterprise_id == "company-1"
        assert code == "company-authcode"
        return EnterpriseIdentity("dingtalk", "company-1", "external-user-55")

    monkeypatch.setattr(sso, "exchange_code", fake_exchange)
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)

    redirect = client.get("/auth/sso/start?t=" + token, follow_redirects=False)
    assert redirect.status_code == 303
    login_params = parse_qs(urlsplit(redirect.headers["location"]).query)
    assert login_params["scope"] == ["openid corpid"]
    state = login_params["state"][0]
    assert not any(c.startswith("client-auth-nt") for c in manager.commands)
    done = client.get(
        "/auth/sso/dingtalk/callback?state=" + state + "&authCode=company-authcode"
    )
    assert done.status_code == 200
    assert manager.commands[-1] == "client-auth-nt 100 0\n"
    import json
    events = [json.loads(line) for line in (tmp_path / "integration-sso-auth.jsonl").read_text(encoding="utf-8").splitlines()]
    assert len(events) >= 2
    assert events[-1]["external_user_id"] == "external-user-55"
    repeated = client.get(
        "/auth/sso/dingtalk/callback?state=" + state + "&authCode=company-authcode"
    )
    assert repeated.status_code == 410
    assert manager.commands.count("client-auth-nt 100 0\n") == 1


def test_webauth_provider_choice_only_accepts_enabled_office_apps(monkeypatch, tmp_path):
    store = SqliteStore(tmp_path / "sso-choice.db")
    store.upsert_group({
        "id": "eng", "name": "研发", "status": "active", "subnet": "10.8.0.0/24",
    })
    store.upsert_user({"username": "vpn-cert", "group_id": "eng", "status": "active"})
    policy = SsoPolicyService(store)
    policy.set_group_required("eng", required=True)
    providers = SsoProviderStore(store, key_path=tmp_path / "master.key")
    providers.save(
        provider_id="dd", kind="dingtalk", enterprise_id="corp-ding",
        app_id="ding-key", secret="private1", enabled=True,
    )
    providers.save(
        provider_id="wx", kind="wecom", enterprise_id="corp-wecom",
        app_id="agent", secret="private2", enabled=True,
    )
    providers.save(
        provider_id="disabled", kind="dingtalk", enterprise_id="bad-corp",
        app_id="agent2", secret="private3", enabled=False,
    )
    runtime = SsoRuntime(
        instances=["server"], public_base_url="https://vpn.example.org",
        policy=policy, providers=providers,
        transactions=WebAuthTransactionStore(store),
    )
    manager = FakeManager()
    runtime.instances["server"] = manager
    runtime.on_event(manager, ClientNotification(
        "CONNECT", 200, 1, {
            "common_name": "vpn-cert", "IV_SSO": "webauth",
            "untrusted_ip": "203.0.113.2", "untrusted_port": "49000",
        },
    ))
    url = manager.commands[0].split("WEB_AUTH:external:", 1)[1].rsplit(" ", 1)[0]
    token = parse_qs(urlsplit(url).query)["t"][0]
    import app.api.sso as sso
    monkeypatch.setattr(sso, "get_sso_runtime", lambda: runtime)
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)

    picker = client.get("/auth/sso/start?t=" + token)
    assert picker.status_code == 200
    assert "钉钉" in picker.text
    assert "企业微信" in picker.text
    assert "dd" in picker.text
    assert "wx" in picker.text
    assert "bad-corp" not in picker.text
    denied = client.post(
        "/auth/sso/provider",
        data={"t": token, "provider_id": "disabled"},
        follow_redirects=False,
    )
    assert denied.status_code == 403
    selected = client.post(
        "/auth/sso/provider",
        data={"t": token, "provider_id": "wx"},
        follow_redirects=False,
    )
    assert selected.status_code == 303
    assert urlsplit(selected.headers["location"]).hostname == "open.work.weixin.qq.com"
    assert runtime.transactions.get_pending_by_token(token).provider_id == "wx"
    assert not any(cmd.startswith("client-auth-nt") for cmd in manager.commands)
