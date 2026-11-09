# -*- coding: utf-8 -*-
"""公网 API 与管理端分端口部署的隔离契约。"""

from pathlib import Path
import socket
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.server import PublicApiListener, create_api_app


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


def test_one_process_api_listener_starts_and_stops_with_no_admin_routes() -> None:
    """使用真实 Uvicorn 验证第二端口启动、路由隔离及线程正常退出。"""
    with socket.socket() as reserved:
        reserved.bind(("127.0.0.1", 0))
        port = int(reserved.getsockname()[1])

    listener = PublicApiListener(host="127.0.0.1", port=port)
    opener = build_opener(ProxyHandler({}))  # 仅测试本机回环端口，不使用环境代理。
    try:
        listener.start()
        assert listener.thread is not None and listener.thread.is_alive()
        for path in ("/users", "/docs"):
            try:
                opener.open(f"http://127.0.0.1:{port}{path}", timeout=3)
            except HTTPError as exc:
                assert exc.code == 404
            else:
                raise AssertionError(f"管理路由暴露在 API 端口：{path}")
        request = Request(
            f"http://127.0.0.1:{port}/api/vpn/users",
            data=b'{"username":"test"}',
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            opener.open(request, timeout=3)
        except HTTPError as exc:
            assert exc.code == 401
        else:
            raise AssertionError("公网 API 未校验 Basic 凭据")
    finally:
        listener.stop()
    assert listener.thread is not None and not listener.thread.is_alive()


def test_api_listener_does_not_create_a_second_process() -> None:
    listener = PublicApiListener(host="127.0.0.1", port=8881)
    assert isinstance(listener.server.config.app, FastAPI)
    assert listener.thread is None


def test_management_app_no_longer_registers_public_vpn_router() -> None:
    main_source = (Path(__file__).resolve().parents[2] / "main.py").read_text(encoding="utf-8")
    assert 'app.include_router(vpn_ops_router' not in main_source
    assert 'app.include_router(download_router' not in main_source


def test_single_web_run_manages_both_listeners(monkeypatch) -> None:
    """一个入口函数同时启动 Web 与 API，退出时停止 API。"""
    import main

    events: list[tuple] = []
    monkeypatch.setenv("NICEVPN_UI_HOST", "127.0.0.1")
    monkeypatch.setenv("NICEVPN_UI_PORT", "18880")
    monkeypatch.setenv("NICEVPN_API_HOST", "127.0.0.1")
    monkeypatch.setenv("NICEVPN_API_PORT", "18881")
    monkeypatch.setattr(main, "init", lambda: {"admin_username": "", "admin_password_hash": ""})
    monkeypatch.setattr(main, "setup_logging", lambda: None)
    monkeypatch.setattr(main, "set_listen_http_base", lambda *_args: events.append(("download_base", *_args)))
    monkeypatch.setattr(main, "_shutdown_background_services", lambda: None)
    monkeypatch.setattr(main.logging, "shutdown", lambda: None)
    monkeypatch.setattr(main.ui, "run", lambda **kwargs: events.append(("web", kwargs["port"])))

    class FakeApiListener:
        def __init__(self, *, host: str, port: int) -> None:
            assert host == "127.0.0.1"
            events.append(("api_config", port))

        def start(self) -> None:
            events.append(("api_start",))

        def stop(self) -> None:
            events.append(("api_stop",))

    monkeypatch.setattr(main, "PublicApiListener", FakeApiListener)
    main.run_web()
    assert events == [
        ("download_base", "http", "127.0.0.1", 18881),
        ("api_config", 18881),
        ("api_start",),
        ("web", 18880),
        ("api_stop",),
    ]


def test_api_token_download_is_private_and_one_time(tmp_path, monkeypatch) -> None:
    """8881 下载只凭令牌；不向日志暴露明文 Token，并禁止缓存。"""
    from app.core import constants
    from app.services.download.link_mgr import create_link
    import app.api.download as download_mod

    monkeypatch.setattr(constants, "DOWNLOAD_LINKS_DIR", tmp_path)
    profile = tmp_path / "alice.ovpn"
    profile.write_text("client\n", encoding="utf-8")
    url = create_link("alice", str(profile), "https://download.example.com")
    token = url.rsplit("/", 1)[-1]
    records = []

    class FakeAudit:
        def log(self, *args):
            records.append(args)

    monkeypatch.setattr(download_mod, "AuditLogger", FakeAudit)
    client = TestClient(create_api_app())
    response = client.get("/download/" + token)
    assert response.status_code == 200
    assert response.content == b"client\n"
    assert response.headers["cache-control"].startswith("no-store")
    assert response.headers["referrer-policy"] == "no-referrer"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert records and all(token not in str(record) for record in records)
    replay = client.get("/download/" + token)
    assert replay.status_code == 410
    assert client.get("/download/not_a_valid_token").status_code == 404
