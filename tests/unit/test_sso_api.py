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
