# -*- coding: utf-8 -*-
"""public_base_url 单元测试。"""
from starlette.requests import Request

from app.utils.public_base_url import (
    public_base_url_from_request,
    resolve_download_base_url,
    resolve_ui_download_base_url,
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


def test_forwarded_host_without_protocol_uses_request_scheme():
    http_request = _make_request(forwarded_host="192.168.10.5:8881", scheme="http")
    assert public_base_url_from_request(http_request) == "http://192.168.10.5:8881"
    https_request = _make_request(forwarded_host="vpn.example.org", scheme="https")
    assert public_base_url_from_request(https_request) == "https://vpn.example.org"


def test_resolve_configured_overrides(monkeypatch):
    monkeypatch.setattr(
        "app.utils.public_base_url.get_listen_http_base",
        lambda: "http://192.168.1.30:8881",
    )
    r = _make_request()
    assert resolve_download_base_url(r, "https://fixed.example/download") == (
        "https://fixed.example/download"
    )


def test_resolve_unconfigured_uses_startup_ip_for_any_browser_request(monkeypatch):
    monkeypatch.setattr(
        "app.utils.public_base_url.get_listen_http_base",
        lambda: "http://172.16.22.247:8881",
    )
    for r in (
        _make_request(host=b"192.168.1.5:8880"),
        _make_request(host=b"localhost:8880"),
        _make_request(host=b"admin.example.com", scheme="https"),
        _make_request(
            host=b"127.0.0.1:8880",
            forwarded_host="admin.example.com",
            forwarded_proto="https",
        ),
    ):
        assert resolve_download_base_url(r, None) == "http://172.16.22.247:8881"
        assert resolve_ui_download_base_url(r, None) == "http://172.16.22.247:8881"

    assert resolve_download_base_url(None, None) == "http://172.16.22.247:8881"
    assert resolve_ui_download_base_url(None, None) == "http://172.16.22.247:8881"


def test_no_startup_address_and_no_config_returns_none(monkeypatch):
    monkeypatch.setattr(
        "app.utils.public_base_url.get_listen_http_base",
        lambda: None,
    )
    assert resolve_download_base_url(_make_request(), None) is None
    assert resolve_download_base_url(None, None) is None


def test_configured_ip_overrides_startup_ip_without_domain(monkeypatch):
    monkeypatch.setattr(
        "app.utils.public_base_url.get_listen_http_base",
        lambda: "http://172.16.22.247:8881",
    )
    assert resolve_download_base_url(None, "http://10.0.0.11:18881/") == (
        "http://10.0.0.11:18881"
    )
    assert resolve_ui_download_base_url(
        _make_request(host=b"admin.example.com", scheme="https"),
        "http://10.0.0.11:18881/",
    ) == "http://10.0.0.11:18881"


def test_configured_management_port_is_not_used(monkeypatch):
    monkeypatch.setenv("NICEVPN_UI_PORT", "8880")
    assert resolve_download_base_url(
        None, "http://192.168.1.10:8880",
    ) is None
