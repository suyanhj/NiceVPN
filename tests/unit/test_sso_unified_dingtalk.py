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
    format_allowed_enterprises, is_allowed_enterprise, parse_allowed_enterprises,
)


def test_enterprise_allowlist_parser_and_exact_match():
    corps = parse_allowed_enterprises("dingAAA | 总部\ndingBBB | 外部客户")
    assert corps == [
        {"corp_id": "dingAAA", "name": "总部"},
        {"corp_id": "dingBBB", "name": "外部客户"},
    ]
    assert format_allowed_enterprises(corps) == "dingAAA | 总部\ndingBBB | 外部客户"
    cfg = SystemConfig(enterprise_sso_allowed_corps=corps)
    assert is_allowed_enterprise(cfg, "dingAAA")
    assert not is_allowed_enterprise(cfg, "dingAA")
    assert not is_allowed_enterprise(cfg, "dingCCC")
    assert not is_allowed_enterprise(cfg, "")
    with pytest.raises(ValueError):
        parse_allowed_enterprises("dingAAA | 公司1\ndingAAA | 公司2")
    with pytest.raises(ValueError):
        parse_allowed_enterprises("** | 企业")


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
    monkeypatch.setattr("app.services.auth.sso_runtime.load_config", lambda: config)
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
    with pytest.raises(ValueError):
        store.import_legacy_dingtalk()
