# -*- coding: utf-8 -*-
"""Anonymous, narrowly scoped OAuth callbacks for OpenVPN WEB_AUTH.

Only mounted on the separate public FastAPI listener, never on NiceGUI admin.
Callbacks never accept a VPN common name, instance ID, CID or KID from HTTP.
"""
from __future__ import annotations

import logging
from html import escape
from urllib.parse import parse_qs

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app.services.auth.office_oauth import (
    OfficeAuthError, authorization_url, exchange_code,
)
from app.services.auth.sso_runtime import SsoFinishResult, get_sso_runtime
from app.services.auth.sso_transactions import WebAuthTransactionStore

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/auth/sso", tags=["Office SSO"])

_NO_CACHE = {
    "Cache-Control": "no-store, max-age=0",
    "Pragma": "no-cache",
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
}


def _html(message: str, *, code: int = 200, show_vpn_hint: bool = False) -> HTMLResponse:
    # No JS, tracking images or third-party scripts; mobile browsers may stay
    # open after the backend approves the parallel OpenVPN connection.
    safe_message = escape(message)
    body = (
        "<!doctype html><html lang='zh-CN'><head>"
        "<meta charset='utf-8'><meta name='viewport' "
        "content='width=device-width,initial-scale=1'>"
        "<title>NiceVPN 企业认证</title></head>"
        "<body style='font:16px system-ui,sans-serif;text-align:center;"
        "padding:16vh 2rem;line-height:1.8'>"
        f"<h2>NiceVPN 企业认证</h2><p>{safe_message}</p>"
        + ("<p>可以返回 VPN 客户端查看连接状态。</p>" if show_vpn_hint else "")
        + "</body></html>"
    )
    return HTMLResponse(
        body, status_code=code,
        headers={**_NO_CACHE, "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'"},
    )


@router.get("/start", include_in_schema=False)
async def start_sso(t: str = ""):
    runtime = get_sso_runtime()
    if runtime is None or runtime.stop_event.is_set():
        raise HTTPException(503, "SSO unavailable")
    transaction = runtime.transactions.get_pending_by_token(t)
    if transaction is None:
        return _html("认证链接已过期或已经使用。请重新连接 VPN。", code=410)
    available = runtime.providers.enabled_metadata()
    if len(available) > 1:
        # Present only an administrator-approved set, no arbitrary CorpId.
        fields = []
        for provider_meta in available:
            pid = escape(str(provider_meta["id"]), quote=True)
            label = "钉钉" if provider_meta["kind"] == "dingtalk" else "企业微信"
            fields.append(
                f'<form method="POST" action="/auth/sso/provider">'
                f'<input type="hidden" name="t" value="{escape(t, quote=True)}">'
                f'<button name="provider_id" value="{pid}" type="submit">'
                f'{label} · {escape(str(provider_meta["display_name"] or provider_meta["id"]))}</button></form>'
            )
        body = (
            '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            '<title>NiceVPN 企业认证</title></head>'
            '<body style="font:16px system-ui,sans-serif;margin:12vh auto;'
            'max-width:34rem;padding:0 1.2rem;text-align:center">'
            '<h2>选择企业身份认证方式</h2>'
            + "".join(fields)
            + '</body></html>'
        )
        return HTMLResponse(body, headers={
            **_NO_CACHE,
            "Content-Security-Policy": "default-src 'none'; form-action 'self'",
        })
    provider = runtime.providers.get(transaction.provider_id)
    if provider is None:
        return _html("企业身份提供商不可用。请联系管理员。", code=503)
    state = WebAuthTransactionStore.state_for_token(t)
    callback = runtime.public_base_url + f"/auth/sso/{provider.kind}/callback"
    url = authorization_url(provider, callback, state)
    return RedirectResponse(url, status_code=303, headers=_NO_CACHE)


@router.post("/provider", include_in_schema=False)
async def choose_provider(request: Request):
    runtime = get_sso_runtime()
    if runtime is None or runtime.stop_event.is_set():
        raise HTTPException(503, "SSO unavailable")
    if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/x-www-form-urlencoded":
        return _html("请求格式错误。", code=415)
    data = await request.body()
    if len(data) > 2048:
        return _html("认证请求过长。", code=413)
    try:
        form = parse_qs(data.decode("utf-8"), keep_blank_values=True, strict_parsing=True)
    except (ValueError, UnicodeDecodeError):
        return _html("认证请求格式错误。", code=400)
    if set(form) != {"t", "provider_id"} or any(len(values) != 1 for values in form.values()):
        return _html("认证请求格式错误。", code=400)
    token, provider_id = form["t"][0], form["provider_id"][0]
    transaction = runtime.transactions.get_pending_by_token(token)
    if transaction is None:
        return _html("认证链接已经失效，请重新连接 VPN。", code=410)
    if not runtime.select_provider(
        token=token, request=transaction, provider_id=provider_id,
    ):
        return _html("认证提供商不可用或已失效。", code=403)
    provider = runtime.providers.get(provider_id)
    if provider is None:
        return _html("认证提供商不可用。", code=503)
    callback = runtime.public_base_url + f"/auth/sso/{provider.kind}/callback"
    url = authorization_url(
        provider, callback, WebAuthTransactionStore.state_for_token(token),
    )
    return RedirectResponse(url, status_code=303, headers=_NO_CACHE)


@router.get("/{provider_kind}/callback", include_in_schema=False)
async def office_callback(provider_kind: str, request: Request):
    runtime = get_sso_runtime()
    if runtime is None or runtime.stop_event.is_set():
        raise HTTPException(503, "SSO unavailable")
    if provider_kind not in {"dingtalk", "wecom"}:
        raise HTTPException(404, "Unknown SSO provider")
    state = str(request.query_params.get("state") or "")
    if not state or len(state) > 256:
        return _html("认证请求不合法或已经失效。", code=400)
    transaction = runtime.transactions.get_pending_by_state(state)
    if transaction is None:
        return _html("认证已过期，无法重复使用。", code=410)
    provider = runtime.providers.get(transaction.provider_id)
    if provider is None or provider.kind != provider_kind:
        return _html("企业身份提供商不匹配。", code=403)

    code = str(
        request.query_params.get("authCode")
        or request.query_params.get("code")
        or ""
    )
    if request.query_params.get("error") or not code:
        runtime.finish(transaction, state, None)
        return _html("办公系统登录未完成或已取消。", code=403)
    try:
        verified = await exchange_code(provider, code)
    except OfficeAuthError:
        # Do not leak the third-party code, token, provider secret or CorpId.
        runtime.finish(transaction, state, None)
        logger.info("Office SSO verification rejected")
        return _html("企业身份认证未通过。", code=403)

    result = runtime.finish_with_result(transaction, state, verified)
    if result == SsoFinishResult.DENIED:
        return _html("当前企业未获授权，不允许访问 VPN。", code=403)
    if result == SsoFinishResult.EXPIRED:
        return _html("认证事务已失效。请重新发起 VPN 连接。", code=410)
    if result == SsoFinishResult.UNAVAILABLE:
        return _html("VPN 认证服务暂时不可用。请重新连接。", code=503)
    return _html("企业身份验证成功，VPN 正在继续连接。", show_vpn_hint=True)
