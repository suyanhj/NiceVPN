"""Unified DingTalk login: a single OAuth app, multiple allow-listed CorpIds."""
import json
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.sso import router
from app.models.config import SystemConfig
from app.services.auth.enterprise_sso_policy import SsoPolicyService
from app.services.auth.office_oauth import (
    EnterpriseIdentity, OfficeAuthError, authorization_url, exchange_code,
)
from app.services.auth.openvpn_webauth import ClientNotification
from app.services.auth.provider_store import SsoProviderStore, UNIFIED_DINGTALK_ID
from app.services.auth.sso_audit import SsoAudit
from app.services.auth.sso_runtime import SsoRuntime
from app.services.auth.sso_transactions import WebAuthTransactionStore
from app.storage.sqlite_store import SqliteStore
from app.utils.sso_enterprises import (
    format_allowed_enterprises, is_allowed_enterprise, needs_enterprise_name_lookup,
    parse_allowed_enterprises,
)


def test_enterprise_allowlist_parser_and_exact_match():
    corps = parse_allowed_enterprises("dingAAA | 总部\ndingBBB | 外部客户")
    assert corps == [
        {"corp_id": "dingAAA", "name": "总部"},
        {"corp_id": "dingBBB", "name": "外部客户"},
    ]
    assert format_allowed_enterprises(corps) == "dingAAA | 总部\ndingBBB | 外部客户"
    cfg = SystemConfig(enterprise_sso_allowed_corps=corps)
    assert is_allowed_enterprise(cfg.model_dump(), "dingAAA")
    assert not is_allowed_enterprise(cfg.model_dump(), "dingAA")
    assert not is_allowed_enterprise(cfg.model_dump(), "dingCCC")
    assert not is_allowed_enterprise(cfg.model_dump(), "")
    with pytest.raises(ValueError):
        parse_allowed_enterprises("dingAAA | 公司1\ndingAAA | 公司2")
    with pytest.raises(ValueError):
        parse_allowed_enterprises("** | 企业")

def test_enterprise_name_only_is_exact_and_never_overrides_configured_corp_id():
    companies = parse_allowed_enterprises([
        {"corp_id": "", "name": "只配置名称的企业"},
        {"corp_id": "dingActual", "name": "同时配置的企业"},
    ])
    config = SystemConfig(enterprise_sso_allowed_corps=companies).model_dump()
    assert needs_enterprise_name_lookup(config)
    assert format_allowed_enterprises(companies).startswith("| 只配置名称的企业")
    assert is_allowed_enterprise(config, "dingOther", "只配置名称的企业")
    assert not is_allowed_enterprise(config, "dingOther", "同时配置的企业")
    assert is_allowed_enterprise(config, "dingActual", "不同的名称")
    assert not is_allowed_enterprise(config, "dingOther", "")
    assert not is_allowed_enterprise(config, "", "只配置名称的企业")
    with pytest.raises(ValueError, match="至少填写"):
        parse_allowed_enterprises([{"corp_id": "", "name": ""}])
    with pytest.raises(ValueError, match="重复"):
        parse_allowed_enterprises([
            {"corp_id": "", "name": "同名"}, {"corp_id": "", "name": "同名"},
        ])


@pytest.mark.asyncio
async def test_dingtalk_name_lookup_uses_verified_corp_id_and_provider_api(tmp_path, caplog):
    caplog.set_level("INFO")
    providers = SsoProviderStore(
        SqliteStore(tmp_path / "name.db"), key_path=tmp_path / "secret.key",
    )
    providers.save_unified(app_id="app", secret="secret")
    requests_seen = []

    def response(request):
        requests_seen.append(request)
        if request.url.path.endswith("userAccessToken"):
            return httpx.Response(
                200, json={"corpId": "dingVerified", "accessToken": "USER_TOKEN"},
            )
        if request.url.path.endswith("/authInfos"):
            return httpx.Response(200, json={"orgName": "可信企业名称"})
        return httpx.Response(404)

    async with httpx.AsyncClient(transport=httpx.MockTransport(response)) as client:
        identity = await exchange_code(
            providers.get_unified(), "code", client=client,
            fetch_enterprise_name=True,
        )
    assert identity.enterprise_id == "dingVerified"
    assert identity.enterprise_name == "可信企业名称"
    assert len(requests_seen) == 2
    assert requests_seen[1].url.params["targetCorpId"] == "dingVerified"
    assert requests_seen[1].headers["x-acs-dingtalk-access-token"] == "USER_TOKEN"
    assert all("/users/me" not in request.url.path for request in requests_seen)
    assert "enterprise_name='可信企业名称'" in caplog.text
    assert "name_source=user_access_token" in caplog.text
    assert "USER_TOKEN" not in caplog.text


@pytest.mark.asyncio
async def test_corp_id_match_skips_optional_organization_lookup(tmp_path):
    providers = SsoProviderStore(
        SqliteStore(tmp_path / "skip.db"), key_path=tmp_path / "secret.key",
    )
    providers.save_unified(app_id="app", secret="secret")
    requested = []

    def response(request):
        requested.append(request.url.path)
        return httpx.Response(
            200, json={"corpId": "dingKnown", "accessToken": "USER_TOKEN"},
        )

    config = {"enterprise_sso_allowed_corps": [
        {"corp_id": "dingKnown", "name": "无需校验名称"},
        {"corp_id": "", "name": "另一个名称-only企业"},
    ]}
    async with httpx.AsyncClient(transport=httpx.MockTransport(response)) as client:
        identity = await exchange_code(
            providers.get_unified(), "code", client=client,
            fetch_enterprise_name=lambda corp_id: (
                needs_enterprise_name_lookup(config)
                and not is_allowed_enterprise(config, corp_id)
            ),
        )
    assert identity.enterprise_id == "dingKnown"
    assert identity.enterprise_name == ""
    assert requested == ["/v1.0/oauth2/userAccessToken"]


@pytest.mark.asyncio
async def test_missing_organization_permission_cannot_authorize_by_name(tmp_path, caplog):
    caplog.set_level("INFO")
    providers = SsoProviderStore(
        SqliteStore(tmp_path / "deny.db"), key_path=tmp_path / "secret.key",
    )
    providers.save_unified(app_id="app", secret="secret")

    def response(request):
        if request.url.path.endswith("userAccessToken"):
            return httpx.Response(
                200, json={"corpId": "dingVerified", "accessToken": "USER_TOKEN"},
            )
        return httpx.Response(403, json={"message": "permission denied"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(response)) as client:
        identity = await exchange_code(
            providers.get_unified(), "code", client=client,
            fetch_enterprise_name=True,
        )
    assert identity.enterprise_name == ""
    assert "SSO DingTalk organization name unavailable" in caplog.text
    assert "enterprise_name='<empty>'" in caplog.text
    config = {"enterprise_sso_allowed_corps": [
        {"corp_id": "", "name": "可信企业名称"},
    ]}
    assert not is_allowed_enterprise(
        config, identity.enterprise_id, identity.enterprise_name,
    )


@pytest.mark.asyncio
async def test_internal_app_token_can_resolve_org_name_when_user_token_denied(tmp_path):
    providers = SsoProviderStore(
        SqliteStore(tmp_path / "internal.db"), key_path=tmp_path / "secret.key",
    )
    providers.save_unified(app_id="app", secret="secret")
    called = []

    def response(request):
        called.append(request.url.path)
        if request.url.path.endswith("userAccessToken"):
            return httpx.Response(200, json={
                "corpId": "dingVerified", "accessToken": "USER_TOKEN",
            })
        if request.url.path.endswith("accessToken"):
            return httpx.Response(200, json={"accessToken": "APP_TOKEN"})
        if request.headers["x-acs-dingtalk-access-token"] == "APP_TOKEN":
            return httpx.Response(200, json={"orgName": "内部企业"})
        return httpx.Response(403, json={"code": "permission.denied"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(response)) as client:
        identity = await exchange_code(
            providers.get_unified(), "code", client=client,
            fetch_enterprise_name=True,
        )
    assert identity.enterprise_name == "内部企业"
    assert called == [
        "/v1.0/oauth2/userAccessToken",
        "/v1.0/contact/organizations/authInfos",
        "/v1.0/oauth2/accessToken",
        "/v1.0/contact/organizations/authInfos",
    ]

@pytest.mark.asyncio
async def test_global_dingtalk_token_requires_corp_but_no_single_expected_corp(tmp_path):
    providers = SsoProviderStore(SqliteStore(tmp_path / "store.db"),
                                 key_path=tmp_path / "master.key")
    providers.save_unified(app_id="unified-app", secret="private")
    provider = providers.get_unified()
    assert provider.id == UNIFIED_DINGTALK_ID
    assert provider.enterprise_id == "__unified__"
    assert parse_qs(urlsplit(authorization_url(
        provider, "https://vpn.example.org/auth/sso/dingtalk/callback", "s" * 40,
    )).query)["scope"] == ["openid corpid"]

    def reply(request):
        return httpx.Response(200, json={
            "corpId": "dingExternal", "accessToken": "private-user-token",
        })
    async with httpx.AsyncClient(transport=httpx.MockTransport(reply)) as client:
        identity = await exchange_code(provider, "code", client=client)
    assert identity.enterprise_id == "dingExternal"

    def missing(request):
        return httpx.Response(200, json={"accessToken": "private-user-token"})
    async with httpx.AsyncClient(transport=httpx.MockTransport(missing)) as client:
        with pytest.raises(OfficeAuthError):
            await exchange_code(provider, "code", client=client)


class FakeManager:
    instance_id = "server"

    def __init__(self):
        self.commands = []

    def send(self, command):
        self.commands.append(command)


def _runtime(tmp_path):
    store = SqliteStore(tmp_path / "unified.db")
    store.upsert_group({
        "id": "group1", "name": "SSO组", "status": "active",
        "subnet": "10.8.0.0/24",
    })
    store.upsert_user({
        "username": "alice", "group_id": "group1", "status": "active",
    })
    policy = SsoPolicyService(store)
    policy.set_group_required("group1", required=True)
    providers = SsoProviderStore(store, key_path=tmp_path / "key")
    providers.save(provider_id="legacy", kind="dingtalk", enterprise_id="dingOLD",
                   app_id="old", secret="old-secret", enabled=True)
    providers.save_unified(app_id="global-app", secret="new-secret")
    runtime = SsoRuntime(
        instances=["server"], public_base_url="https://vpn.example.org",
        policy=policy, providers=providers,
        transactions=WebAuthTransactionStore(store),
    )
    runtime.audit = SsoAudit(log_path=tmp_path / "sso.jsonl")
    manager = FakeManager()
    runtime.instances["server"] = manager
    return runtime, manager


def test_unified_webauth_skips_company_picker_and_enforces_whitelist(
    tmp_path, monkeypatch,
):
    runtime, manager = _runtime(tmp_path)
    config = SystemConfig(enterprise_sso_allowed_corps=[
        {"corp_id": "dingAllowed", "name": "供应商"},
    ])
    monkeypatch.setattr("app.services.auth.sso_runtime.load_config", lambda: config.model_dump())
    monkeypatch.setattr("app.services.auth.sso_runtime.write_sso_grant",
                        lambda *_args, **_kwargs: None)
    runtime.on_event(manager, ClientNotification(
        "CONNECT", 111, 0, {
            "common_name": "alice", "IV_SSO": "webauth",
            "untrusted_ip": "203.0.113.4", "untrusted_port": "41000",
        },
    ))
    url = manager.commands[0].split("WEB_AUTH:external:", 1)[1].rsplit(" ", 1)[0]
    token = parse_qs(urlsplit(url).query)["t"][0]
    assert runtime.transactions.get_pending_by_token(token).provider_id == UNIFIED_DINGTALK_ID

    import app.api.sso as sso
    monkeypatch.setattr(sso, "get_sso_runtime", lambda: runtime)
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)
    login = client.get("/auth/sso/start?t=" + token, follow_redirects=False)
    assert login.status_code == 303
    assert urlsplit(login.headers["location"]).hostname == "login.dingtalk.com"
    assert parse_qs(urlsplit(login.headers["location"]).query)["client_id"] == ["global-app"]

    state = WebAuthTransactionStore.state_for_token(token)
    transaction = runtime.transactions.get_pending_by_token(token)
    assert runtime.finish(transaction, state, EnterpriseIdentity(
        "dingtalk", "dingUnexpected", "uid",
    )) is False
    assert manager.commands[-1].startswith("client-deny")
    assert not any(cmd.startswith("client-auth-nt") for cmd in manager.commands)

    runtime.on_event(manager, ClientNotification(
        "CONNECT", 112, 1, {
            "common_name": "alice", "IV_SSO": "webauth",
            "untrusted_ip": "203.0.113.4", "untrusted_port": "41001",
        },
    ))
    new_url = manager.commands[-1].split("WEB_AUTH:external:", 1)[1].rsplit(" ", 1)[0]
    token2 = parse_qs(urlsplit(new_url).query)["t"][0]
    assert runtime.finish(
        runtime.transactions.get_pending_by_token(token2),
        WebAuthTransactionStore.state_for_token(token2),
        EnterpriseIdentity("dingtalk", "dingAllowed", "uid123"),
    ) is True
    assert manager.commands[-1] == "client-auth-nt 112 1\n"
    records = [json.loads(line) for line in
               (tmp_path / "sso.jsonl").read_text(encoding="utf-8").splitlines()]
    assert records[-1]["enterprise_id"] == "dingAllowed"


def test_explicit_legacy_import_keeps_old_apps_and_secret_encrypted(tmp_path):
    store = SsoProviderStore(SqliteStore(tmp_path / "db.sqlite"),
                             key_path=tmp_path / "secret.key")
    store.save(provider_id="legacy1", kind="dingtalk", enterprise_id="dingA",
               app_id="app-a", secret="secret-a", enabled=True,
               display_name="公司A")
    store.save(provider_id="legacy2", kind="dingtalk", enterprise_id="dingB",
               app_id="app-b", secret="secret-b", enabled=True,
               display_name="公司B")
    assert not store.has_unified()
    assert len(store.enabled_metadata()) == 2
    companies = store.import_legacy_dingtalk()
    assert {r["corp_id"] for r in companies} == {"dingA", "dingB"}
    assert store.has_unified()
    assert store.first_enabled().id == UNIFIED_DINGTALK_ID
    assert store.first_enabled().app_id == "app-a"
    assert store.get("legacy2").secret == "secret-b"
    assert len(store.enabled_metadata()) == 1
    assert "secret-a" not in repr(store.list_metadata())
    assert store.cleanup_unified_legacy() == 2
    assert store.get("legacy1") is None
    assert store.get("legacy2") is None
    assert {row["id"] for row in store.list_metadata()} == {UNIFIED_DINGTALK_ID}
    assert store.cleanup_unified_legacy() == 0
    with pytest.raises(ValueError):
        store.import_legacy_dingtalk()


def test_callback_denied_enterprise_shows_403_not_expired(
    tmp_path, monkeypatch, caplog,
):
    caplog.set_level("INFO")
    runtime, manager = _runtime(tmp_path)
    config = SystemConfig(enterprise_sso_allowed_corps=[
        {"corp_id": "", "name": "后台配置企业名"},
    ])
    monkeypatch.setattr("app.services.auth.sso_runtime.load_config",
                        lambda: config.model_dump())
    runtime.on_event(manager, ClientNotification(
        "CONNECT", 320, 1, {
            "common_name": "alice", "IV_SSO": "webauth",
            "untrusted_ip": "203.0.113.5", "untrusted_port": "54000",
        },
    ))
    url = manager.commands[0].split("WEB_AUTH:external:", 1)[1].rsplit(" ", 1)[0]
    token = parse_qs(urlsplit(url).query)["t"][0]
    state = WebAuthTransactionStore.state_for_token(token)

    import app.api.sso as sso

    monkeypatch.setattr(sso, "get_sso_runtime", lambda: runtime)

    async def fake_exchange(_provider, _code, **_kwargs):
        return EnterpriseIdentity(
            "dingtalk", "dingBlocked", "external-uid", "钉钉接口返回企业名",
        )

    monkeypatch.setattr(sso, "exchange_code", fake_exchange)
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)
    callback_url = (
        f"/auth/sso/dingtalk/callback?state={state}&authCode=oauth-code"
    )
    response = client.get(callback_url)
    assert response.status_code == 403
    assert "verified_enterprise_name='钉钉接口返回企业名'" in caplog.text
    assert "configured_name_only=['后台配置企业名']" in caplog.text
    assert "corp_id_match=False name_match=False" in caplog.text
    assert "不允许访问 VPN" in response.text
    assert "认证事务已失效" not in response.text
    assert "可以返回 VPN 客户端" not in response.text
    assert manager.commands[-1].startswith("client-deny")
    assert client.get(callback_url).status_code == 410
