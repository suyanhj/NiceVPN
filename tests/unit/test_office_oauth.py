from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from app.services.auth.office_oauth import (
    EnterpriseIdentity, OfficeAuthError, OfficeProvider,
    authorization_url, exchange_code,
)


def _provider(kind="dingtalk"):
    return OfficeProvider(id="work", kind=kind, enterprise_id="corp-good",
                          app_id="app-key", secret="secret")


def test_dingtalk_authorization_requests_corporate_scope():
    url = authorization_url(_provider(), "https://vpn.example.org/auth/cb", "s" * 40)
    parts = urlsplit(url)
    params = parse_qs(parts.query)
    assert parts.hostname == "login.dingtalk.com"
    assert params["scope"] == ["openid corpid"]
    assert params["redirect_uri"] == ["https://vpn.example.org/auth/cb"]
    assert params["state"] == ["s" * 40]


def test_wecom_qr_login_contains_enterprise_and_agent():
    url = authorization_url(_provider("wecom"), "https://vpn.example.org/auth/cb", "s" * 40)
    assert urlsplit(url).hostname == "open.work.weixin.qq.com"
    assert parse_qs(urlsplit(url).query)["appid"] == ["corp-good"]
    assert parse_qs(urlsplit(url).query)["agentid"] == ["app-key"]


@pytest.mark.asyncio
async def test_dingtalk_rejects_missing_or_wrong_corp_id():
    def response(request):
        if request.url.path.endswith("userAccessToken"):
            return httpx.Response(200, json={"corpId": "corp-bad", "accessToken": "token"})
        return httpx.Response(500)
    async with httpx.AsyncClient(transport=httpx.MockTransport(response)) as client:
        with pytest.raises(OfficeAuthError):
            await exchange_code(_provider(), "code", client=client)


@pytest.mark.asyncio
async def test_dingtalk_correct_enterprise_allows_without_vpn_user_lookup():
    def response(request):
        if request.url.path.endswith("userAccessToken"):
            return httpx.Response(200, json={"corpId": "corp-good", "accessToken": "valid"})
        return httpx.Response(200, json={"unionId": "external-office-user"})
    async with httpx.AsyncClient(transport=httpx.MockTransport(response)) as client:
        ident = await exchange_code(_provider(), "code", client=client)
    assert ident == EnterpriseIdentity("dingtalk", "corp-good", "external-office-user")


@pytest.mark.asyncio
async def test_wecom_only_accepts_internal_enterprise_member():
    def response(request):
        if request.url.path.endswith("gettoken"):
            return httpx.Response(200, json={"errcode": 0, "access_token": "valid"})
        if request.url.path.endswith("getuserinfo"):
            return httpx.Response(200, json={"errcode": 0, "userid": "employee"})
        return httpx.Response(404)
    async with httpx.AsyncClient(transport=httpx.MockTransport(response)) as client:
        ident = await exchange_code(_provider("wecom"), "code", client=client)
    assert ident.external_user_id == "employee"


@pytest.mark.asyncio
async def test_wecom_external_contact_is_rejected():
    def response(request):
        if request.url.path.endswith("gettoken"):
            return httpx.Response(200, json={"errcode": 0, "access_token": "valid"})
        return httpx.Response(200, json={"errcode": 0, "openid": "external"})
    async with httpx.AsyncClient(transport=httpx.MockTransport(response)) as client:
        with pytest.raises(OfficeAuthError):
            await exchange_code(_provider("wecom"), "code", client=client)


def test_oauth_redirect_accepts_http_https_ip_and_port():
    for callback in (
        "http://127.0.0.1:8881/auth/sso/dingtalk/callback",
        "http://192.168.1.20:8881/auth/sso/dingtalk/callback",
        "http://vpn.example.org/auth/sso/dingtalk/callback",
        "https://vpn.example.org/auth/sso/dingtalk/callback",
    ):
        url = authorization_url(_provider(), callback, "s" * 40)
        assert parse_qs(urlsplit(url).query)["redirect_uri"] == [callback]


def test_oauth_redirect_rejects_malformed_or_unsafe_urls():
    for url in ("ftp://vpn.example.org/callback",
                "https://user:pass@vpn.example.org/cb",
                "https://vpn.example.org/cb?untrusted=x",
                "http://127.0.0.1:bad/callback",
                "http://127.0.0.1/cb#fragment",
                "http://127.0.0.1/cb\nmalicious"):
        with pytest.raises(OfficeAuthError):
            authorization_url(_provider(), url, "s" * 40)
