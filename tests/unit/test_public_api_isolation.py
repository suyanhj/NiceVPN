# -*- coding: utf-8 -*-
"""公网 API 与管理端分端口部署的隔离契约。"""

from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.server import create_api_app, run_api


def test_public_api_only_exposes_vpn_and_token_download() -> None:
    app = create_api_app()
    paths = {route.path for route in app.routes}

    assert "/api/vpn/users" in paths
    assert "/download/{token}" in paths
    assert "/users" not in paths
    assert "/login" not in paths
    assert "/settings" not in paths
    assert "/_nicegui" not in paths
    assert "/static" not in paths
    assert "/docs" not in paths
    assert "/redoc" not in paths
    assert "/openapi.json" not in paths

    client = TestClient(app)
    for path in ("/", "/users", "/settings", "/login", "/docs", "/openapi.json", "/_nicegui"):
        assert client.get(path).status_code == 404
    assert client.post("/api/vpn/users", json={"username": "test"}).status_code == 401


def test_api_runner_respects_independent_listen_environment(monkeypatch) -> None:
    called: dict = {}
    monkeypatch.setenv("NICEVPN_API_HOST", "127.0.0.1")
    monkeypatch.setenv("NICEVPN_API_PORT", "19483")

    def fake_uvicorn_run(app, **kwargs):
        called.update(app=app, **kwargs)

    monkeypatch.setattr("app.api.server.uvicorn.run", fake_uvicorn_run)
    run_api()

    assert isinstance(called["app"], FastAPI)
    assert called["host"] == "127.0.0.1"
    assert called["port"] == 19483


def test_management_app_no_longer_registers_public_vpn_router() -> None:
    main_source = (Path(__file__).resolve().parents[2] / "main.py").read_text(encoding="utf-8")
    assert 'app.include_router(vpn_ops_router' not in main_source
