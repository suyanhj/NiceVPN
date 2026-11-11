"""NiceVPN SSO never mandates TLS or domains for its own callback URL.

Provider-hosted endpoints remain their documented addresses. These tests
only cover the configurable callback and OpenVPN WEB_AUTH destination.
"""
import pytest

from app.services.auth.office_oauth import OfficeProvider, authorization_url
from app.services.auth.openvpn_webauth import pending_webauth
from app.ui.pages.settings_sso import _http_origin


@pytest.mark.parametrize("origin", (
    "http://127.0.0.1:8881",
    "http://192.168.10.23:8881",
    "http://localhost:8000",
    "http://vpn.test.internal",
    "https://vpn.example.org",
    "https://vpn.example.org:9443",
))
def test_settings_allows_http_https_and_ip_port(origin):
    assert _http_origin(origin)


@pytest.mark.parametrize("origin", (
    "",
    "ftp://127.0.0.1",
    "192.168.10.23:8881",
    "http://",
    "http://localhost:bad",
    "http://localhost/auth/callback",
    "http://localhost?token=1",
    "http://user:pass@localhost:8881",
    "http://localhost/#section",
    "http://localhost:8881\r\nmalicious",
))
def test_settings_still_rejects_invalid_urls(origin):
    assert not _http_origin(origin)


@pytest.mark.parametrize("base", (
    "http://127.0.0.1:8881",
    "http://10.0.0.12:8881",
    "https://vpn.example.org",
))
def test_webauth_and_oauth_preserve_configured_scheme(base):
    webauth_url = base + "/auth/sso/start?t=sample"
    cmd = pending_webauth(10, 0, webauth_url)
    assert "WEB_AUTH:external:" + webauth_url in cmd
    provider = OfficeProvider(
        id="office", kind="dingtalk", enterprise_id="corp",
        app_id="app", secret="hidden",
    )
    callback = base + "/auth/sso/dingtalk/callback"
    auth_url = authorization_url(provider, callback, "a" * 40)
    from urllib.parse import urlsplit, parse_qs
    assert parse_qs(urlsplit(auth_url).query)["redirect_uri"] == [callback]
