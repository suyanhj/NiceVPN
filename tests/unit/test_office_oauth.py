from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from app.services.auth.office_oauth import (
    EnterpriseIdentity, OfficeAuthError, OfficeProvider,
    authorization_url, exchange_code,
)


def _provider(kind="dingtalk", read_user_profile=True):
    return OfficeProvider(id="work", kind=kind, enterprise_id="corp-good",
                          app_id="app-key", secret="secret",
                          read_user_profile=read_user_profile)


def test_dingtalk_authorization_requests_corporate_scope():
    url = authorization_url(_provider(read_user_profile=False), "https://vpn.example.org/auth/cb", "s" * 40)
    parts = urlsplit(url)
    params = parse_qs(parts.query)
    assert parts.hostname == "login.dingtalk.com"
    assert params["scope"] == ["openid corpid"]
    assert params["redirect_uri"] == ["https://vpn.example.org/auth/cb"]
    assert params["state"] == ["s" * 40]


def test_contact_directory_permission_is_not_a_login_scope():
    callback = "http://vpn.example.com/auth/sso/dingtalk/callback"
    minimal = authorization_url(_provider(read_user_profile=False), callback, "s" * 40)
    detailed = authorization_url(_provider(read_user_profile=True), callback, "s" * 40)
    assert parse_qs(urlsplit(minimal).query)["scope"] == ["openid corpid"]
    assert parse_qs(urlsplit(detailed).query)["scope"] == ["openid corpid"]


@pytest.mark.asyncio
async def test_contact_read_disabled_never_calls_personal_api():
    requests_seen = []
    def response(request):
        requests_seen.append(request.url.path)
        return httpx.Response(200, json={"corpId": "corp-good", "accessToken": "token"})
    async with httpx.AsyncClient(transport=httpx.MockTransport(response)) as client:
        identity = await exchange_code(_provider(read_user_profile=False), "code", client=client)
    assert identity.enterprise_id == "corp-good"
    assert identity.external_user_id == ""
    assert requests_seen == ["/v1.0/oauth2/userAccessToken"]


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


@pytest.mark.asyncio
async def test_dingtalk_403_logs_only_safe_error_code_and_scope(caplog):
    def response(request):
        if request.url.path.endswith("userAccessToken"):
            return httpx.Response(200, json={
                "corpId": "corp-good", "accessToken": "NEVER_PRINT_ACCESS_TOKEN",
                "refreshToken": "NEVER_PRINT_REFRESH_TOKEN",
            })
        return httpx.Response(403, json={
            "code": "Forbidden.AccessDenied.AccessTokenPermissionDenied",
            "accessdenieddetail": {"requiredScopes": ["Contact.User.Read"]},
            "message": "NEVER_PRINT_PRIVATE_ERROR_BODY",
        })
    with caplog.at_level("INFO", logger="app.services.auth.office_oauth"):
        async with httpx.AsyncClient(transport=httpx.MockTransport(response)) as client:
            identity = await exchange_code(
                _provider(), "NEVER_PRINT_AUTH_CODE", client=client,
            )
    assert identity.enterprise_id == "corp-good"
    assert identity.external_user_id == ""
    assert "expected_corp_id=corp-good" in caplog.text
    assert "verified_corp_id=corp-good" in caplog.text
    assert "required_scopes=Contact.User.Read" in caplog.text
    for value in ("NEVER_PRINT_ACCESS_TOKEN", "NEVER_PRINT_REFRESH_TOKEN",
                  "NEVER_PRINT_AUTH_CODE", "NEVER_PRINT_PRIVATE_ERROR_BODY", "secret"):
        assert value not in caplog.text


@pytest.mark.asyncio
async def test_dingtalk_200_logs_verified_external_identity(caplog):
    def response(request):
        if request.url.path.endswith("userAccessToken"):
            return httpx.Response(200, json={
                "corpId": "corp-good", "accessToken": "DONT_LOG_TOKEN",
            })
        return httpx.Response(200, json={"unionId": "user-union-42", "nick": "Test User"})
    with caplog.at_level("INFO", logger="app.services.auth.office_oauth"):
        async with httpx.AsyncClient(transport=httpx.MockTransport(response)) as client:
            ident = await exchange_code(_provider(), "test-code", client=client)
    assert ident.external_user_id == "user-union-42"
    assert "external_user_id=user-union-42" in caplog.text
    assert "DONT_LOG_TOKEN" not in caplog.text
    assert "Test User" not in caplog.text
