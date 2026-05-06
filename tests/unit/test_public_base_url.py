# -*- coding: utf-8 -*-
"""public_base_url 单元测试。"""
from starlette.requests import Request

from app.utils.public_base_url import (
    public_base_url_from_request,
    resolve_download_base_url,
)


def _make_request(
    *,
    host: bytes = b"192.168.1.5:8080",
    scheme: str = "http",
    path: str = "/users",
    root_path: str = "",
    forwarded_host: str | None = None,
    forwarded_proto: str | None = None,
    forwarded_prefix: str | None = None,
) -> Request:
    headers = [(b"host", host)]
    if forwarded_host:
        headers.append((b"x-forwarded-host", forwarded_host.encode()))
    if forwarded_proto:
        headers.append((b"x-forwarded-proto", forwarded_proto.encode()))
    if forwarded_prefix:
        headers.append((b"x-forwarded-prefix", forwarded_prefix.encode()))
    server_host = host.decode().split(":")[0]
    try:
        server_port = int(host.decode().split(":")[1])
    except IndexError:
        server_port = 80
    scope = {
        "type": "http",
        "method": "GET",
        "path": path,
        "raw_path": path.encode(),
        "root_path": root_path,
        "scheme": scheme,
        "query_string": b"",
        "headers": headers,
        "server": (server_host, server_port),
        "client": ("127.0.0.1", 12345),
        "state": {},
    }
    return Request(scope)


def test_public_base_direct_host():
    r = _make_request()
    assert public_base_url_from_request(r) == "http://192.168.1.5:8080"


def test_public_base_with_root_path():
    r = _make_request(root_path="/vpn")
    assert public_base_url_from_request(r) == "http://192.168.1.5:8080/vpn"


def test_public_base_forwarded():
    r = _make_request(
        forwarded_host="vpn.example.com",
        forwarded_proto="https",
        forwarded_prefix="/app",
        root_path="/mgmt",
    )
    assert public_base_url_from_request(r) == "https://vpn.example.com/app/mgmt"


def test_resolve_configured_overrides():
    r = _make_request()
    out = resolve_download_base_url(r, "https://fixed.example/download")
    assert out == "https://fixed.example/download"


def test_resolve_from_request_non_loopback():
    r = _make_request()
    assert resolve_download_base_url(r, None) == "http://192.168.1.5:8080"


def test_resolve_localhost_with_listen_snap(monkeypatch):
    monkeypatch.setattr(
        "app.utils.public_base_url.get_listen_http_base",
        lambda: "http://172.16.22.247:8080",
    )
    r = _make_request(host=b"localhost:8080")
    assert resolve_download_base_url(r, None) == "http://172.16.22.247:8080"


def test_resolve_localhost_with_listen_snap_and_path(monkeypatch):
    monkeypatch.setattr(
        "app.utils.public_base_url.get_listen_http_base",
        lambda: "http://172.16.22.247:8080",
    )
    r = _make_request(host=b"localhost:8080", root_path="/vpn")
    assert resolve_download_base_url(r, None) == "http://172.16.22.247:8080/vpn"


def test_resolve_localhost_without_listen_snap(monkeypatch):
    monkeypatch.setattr(
        "app.utils.public_base_url.get_listen_http_base",
        lambda: None,
    )
    r = _make_request(host=b"localhost:8080")
    assert resolve_download_base_url(r, None) is None


def test_resolve_no_request_no_config():
    assert resolve_download_base_url(None, None) is None
