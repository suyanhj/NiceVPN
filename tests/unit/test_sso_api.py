"""The public SSO router must never expose management identity parameters."""
from unittest.mock import Mock

from fastapi.testclient import TestClient

from app.api.sso import router


def test_public_webauth_unavailable_is_503(monkeypatch):
    import app.api.sso as sso
    monkeypatch.setattr(sso, "get_sso_runtime", lambda: None)
    from fastapi import FastAPI
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)
    assert client.get("/auth/sso/start").status_code == 503
    assert client.get("/auth/sso/dingtalk/callback?code=x&state=s").status_code == 503


def test_callback_rejects_unsupported_provider_without_disclosing_query(monkeypatch):
    import app.api.sso as sso
    runtime = Mock()
    runtime.stop_event.is_set.return_value = False
    monkeypatch.setattr(sso, "get_sso_runtime", lambda: runtime)
    from fastapi import FastAPI
    app = FastAPI()
    app.include_router(router)
    result = TestClient(app).get("/auth/sso/evil/callback?code=SECRET&state=STATE")
    assert result.status_code == 404
    assert "SECRET" not in result.text


def test_start_without_transaction_is_expired(monkeypatch):
    import app.api.sso as sso
    runtime = Mock()
    runtime.stop_event.is_set.return_value = False
    runtime.transactions.get_pending_by_token.return_value = None
    monkeypatch.setattr(sso, "get_sso_runtime", lambda: runtime)
    from fastapi import FastAPI
    app = FastAPI()
    app.include_router(router)
    response = TestClient(app).get("/auth/sso/start?t=random")
    assert response.status_code == 410
    assert response.headers.get("Cache-Control") == "no-store, max-age=0"
    assert "random" not in response.text


def test_public_browser_oauth_uses_one_time_tokens_while_admin_api_requires_basic(
    monkeypatch,
):
    import app.api.sso as sso
    from app.api.server import create_api_app

    runtime = Mock()
    runtime.stop_event.is_set.return_value = False
    runtime.transactions.get_pending_by_token.return_value = None
    monkeypatch.setattr(sso, "get_sso_runtime", lambda: runtime)
    client = TestClient(create_api_app())

    browser = client.get("/auth/sso/start?t=invalid")
    assert browser.status_code == 410  # Token rejected; no Basic login prompt.
    assert "WWW-Authenticate" not in browser.headers
    admin = client.post("/api/vpn/users", json={"username": "new-user"})
    assert admin.status_code == 401
    assert admin.headers["WWW-Authenticate"] == "Basic"



def test_dingtalk_enterprise_lookup_start_redirects_to_org_chooser(monkeypatch):
    import app.api.sso as sso
    from app.services.auth.dingtalk_enterprise_lookup import DingTalkEnterpriseLookupStore
    from app.services.auth.office_oauth import OfficeProvider
    from fastapi import FastAPI

    store = DingTalkEnterpriseLookupStore()
    state = store.create(
        "https://vpn.example.org/auth/sso/dingtalk/enterprise-lookup/callback",
        "ding-app",
    )

    class FakeProviderStore:
        def get(self, provider_id, *, require_enabled=True):
            assert provider_id == "dingtalk_unified"
            assert require_enabled is False
            return OfficeProvider(
                id="dingtalk_unified", kind="dingtalk",
                enterprise_id="__unified__", app_id="ding-app", secret="secret",
            )

    monkeypatch.setattr(sso, "get_dingtalk_enterprise_lookup_store", lambda: store)
    monkeypatch.setattr(sso, "SsoProviderStore", FakeProviderStore)
    app = FastAPI()
    app.include_router(router)
    response = TestClient(app).get(
        f"/auth/sso/dingtalk/enterprise-lookup/start?state={state}",
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"].startswith("https://login.dingtalk.com/oauth2/auth?")
    assert "scope=openid+corpid" in response.headers["location"]
    assert store.get(state).status == "started"


def test_dingtalk_enterprise_lookup_callback_returns_verified_corp_id(monkeypatch):
    import app.api.sso as sso
    from app.services.auth.dingtalk_enterprise_lookup import DingTalkEnterpriseLookupStore
    from app.services.auth.office_oauth import EnterpriseIdentity, OfficeProvider
    from fastapi import FastAPI

    store = DingTalkEnterpriseLookupStore()
    state = store.create(
        "https://vpn.example.org/auth/sso/dingtalk/enterprise-lookup/callback",
        "ding-app",
    )
    store.begin_start(state)

    class FakeProviderStore:
        def get(self, provider_id, *, require_enabled=True):
            return OfficeProvider(
                id="dingtalk_unified", kind="dingtalk",
                enterprise_id="__unified__", app_id="ding-app", secret="secret",
            )

    async def fake_exchange(provider, code, *, fetch_enterprise_name=False):
        assert provider.app_id == "ding-app"
        assert code == "one-time-code"
        assert fetch_enterprise_name is True
        return EnterpriseIdentity(
            "dingtalk", "dingCorpVerified", "", "查询到的企业名称",
        )

    monkeypatch.setattr(sso, "get_dingtalk_enterprise_lookup_store", lambda: store)
    monkeypatch.setattr(sso, "SsoProviderStore", FakeProviderStore)
    monkeypatch.setattr(sso, "exchange_code", fake_exchange)
    app = FastAPI()
    app.include_router(router)
    response = TestClient(app).get(
        f"/auth/sso/dingtalk/enterprise-lookup/callback"
        f"?state={state}&authCode=one-time-code",
    )

    assert response.status_code == 200
    assert "dingCorpVerified" in response.text
    assert "查询到的企业名称" in response.text
    assert "one-time-code" not in response.text
    result = store.get(state)
    assert result is not None
    assert result.status == "completed"
    assert result.corp_id == "dingCorpVerified"
    assert result.enterprise_name == "查询到的企业名称"
