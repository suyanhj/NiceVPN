# -*- coding: utf-8 -*-
"""OAuth adapters for enterprise-only membership verification.

Never treat an OAuth callback query parameter as an enterprise identity:
DingTalk's corpId is validated from the *server-side token exchange*, and
WeCom's member UserID is validated using an enterprise app token.
"""
from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import Callable
from urllib.parse import urlencode, urlsplit

import httpx

logger = logging.getLogger(__name__)


class OfficeAuthError(ValueError):
    """Safe, non-secret authentication error."""


@dataclass(frozen=True)
class OfficeProvider:
    id: str
    kind: str                 # dingtalk or wecom
    enterprise_id: str        # approved CorpId
    app_id: str               # DingTalk AppKey or WeCom AgentId
    secret: str               # server-only app secret
    read_user_profile: bool = False  # legacy persisted column, no longer queried

    def validate(self) -> None:
        if self.kind not in ("dingtalk", "wecom"):
            raise OfficeAuthError("Unsupported office SSO provider")
        if not all((self.id, self.enterprise_id, self.app_id, self.secret)):
            raise OfficeAuthError("Incomplete office SSO provider")


@dataclass(frozen=True)
class EnterpriseIdentity:
    provider_kind: str
    enterprise_id: str
    external_user_id: str
    enterprise_name: str = ""  # verified by provider API, never browser input


def _callback_url(callback: str) -> None:
    parsed = urlsplit(callback)
    try:
        valid_callback = (
            parsed.scheme in {"http", "https"} and parsed.hostname
            and parsed.username is None and parsed.password is None
            and not parsed.fragment and not parsed.query
            and not any(ch.isspace() or ord(ch) < 32 for ch in callback)
            and (parsed.port is None or 1 <= parsed.port <= 65535)
        )
    except ValueError:
        valid_callback = False
    if not valid_callback:
        raise OfficeAuthError("Office SSO callback must be an HTTP or HTTPS URL")


def authorization_url(provider: OfficeProvider, callback: str, state: str) -> str:
    """State MUST be a CSPRNG-generated, per-connection, one-time secret."""
    provider.validate()
    _callback_url(callback)
    if not state or len(state) < 32 or any(ord(c) < 33 for c in state):
        raise OfficeAuthError("Invalid OAuth state")
    if provider.kind == "dingtalk":
        params = {
            "client_id": provider.app_id,
            "redirect_uri": callback,
            "response_type": "code",
            # DingTalk's web OAuth documents only openid / openid corpid.
            # Contact.User.Read is a delegated API permission, not a login scope.
            "scope": "openid corpid",
            "state": state,
            "prompt": "consent",
        }
        return "https://login.dingtalk.com/oauth2/auth?" + urlencode(params)
    params = {
        "appid": provider.enterprise_id,
        "agentid": provider.app_id,
        "redirect_uri": callback,
        "state": state,
    }
    return "https://open.work.weixin.qq.com/wwopen/sso/qrConnect?" + urlencode(params)


def _safe_provider_error_value(value, *, limit: int = 240) -> str:
    """Bound and sanitize provider error fields before writing them to logs."""
    import re

    text = str(value or "").replace("\r", " ").replace("\n", " ").strip()
    text = re.sub(
        r"(?i)(access[_-]?token|client[_-]?secret|app[_-]?secret|authorization)"
        r"\s*[:=]\s*[^,;\s]+",
        r"\1=<redacted>",
        text,
    )
    return text[:limit]


def _log_provider_http_error(response: httpx.Response) -> None:
    """Log only allow-listed error metadata; never dump provider response bodies."""
    payload = {}
    try:
        candidate = response.json()
        if isinstance(candidate, dict):
            payload = candidate
    except (ValueError, TypeError):
        pass

    code = next((
        payload.get(key) for key in ("code", "errcode", "errorCode", "subCode")
        if payload.get(key) not in (None, "")
    ), "")
    message = next((
        payload.get(key) for key in ("message", "errmsg", "errorMessage", "subMessage")
        if payload.get(key) not in (None, "")
    ), "")
    request_id = next((
        payload.get(key) for key in ("requestid", "requestId", "request_id", "traceId")
        if payload.get(key) not in (None, "")
    ), "")
    if not request_id:
        request_id = next((
            response.headers.get(key) for key in (
                "x-ca-request-id", "x-acs-request-id", "request-id", "trace-id",
            ) if response.headers.get(key)
        ), "")
    scopes = next((
        payload.get(key) for key in ("requiredScopes", "requiredScope", "required_scopes")
        if payload.get(key) not in (None, "")
    ), "")
    if isinstance(scopes, (list, tuple)):
        scopes = ",".join(str(item) for item in scopes[:12])
    request = response.request
    logger.warning(
        "Office provider API HTTP error: host=%s path=%s http_status=%s "
        "api_code=%r api_message=%r request_id=%r required_scopes=%r",
        request.url.host if request else "<unknown>",
        request.url.path if request else "<unknown>",
        response.status_code,
        _safe_provider_error_value(code, limit=100),
        _safe_provider_error_value(message),
        _safe_provider_error_value(request_id, limit=120),
        _safe_provider_error_value(scopes, limit=240),
    )


async def _get_json(response: httpx.Response) -> dict:
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError:
        _log_provider_http_error(response)
        raise
    payload = response.json()
    if not isinstance(payload, dict):
        raise OfficeAuthError("Unexpected office provider response")
    return payload


async def exchange_code(
    provider: OfficeProvider, code: str, *,
    client: httpx.AsyncClient | None = None,
    fetch_enterprise_name: bool | Callable[[str], bool] = False,
) -> EnterpriseIdentity:
    """Validate employee and organization via trusted HTTPS provider APIs.

    HTTP client injection is for deterministic unit tests. Provider hostnames
    are fixed; no HTTP callback/user input may override their addresses.
    """
    provider.validate()
    if not code or len(code) > 2048 or any(ord(c) < 32 for c in code):
        raise OfficeAuthError("Invalid OAuth code")
    managed = client is None
    http = client or httpx.AsyncClient(timeout=8.0, follow_redirects=False)
    try:
        if provider.kind == "dingtalk":
            token = await _get_json(await http.post(
                "https://api.dingtalk.com/v1.0/oauth2/userAccessToken",
                json={
                    "clientId": provider.app_id,
                    "clientSecret": provider.secret,
                    "code": code,
                    "grantType": "authorization_code",
                },
            ))
            # corpId is not supplied by the browser; it comes from DingTalk
            # only when the login requested the "openid corpid" scope.
            corp_id = str(token.get("corpId") or "")
            access_token = str(token.get("accessToken") or "")
            is_unified = provider.enterprise_id == "__unified__"
            logger.info(
                "SSO DingTalk token verified: provider=%s app_id=%s corp_id=%s "
                "legacy_corp_match=%s unified_whitelist_check_pending=%s token_present=%s",
                provider.id, provider.app_id, corp_id or "<empty>",
                (corp_id == provider.enterprise_id) if not is_unified else None,
                is_unified, bool(access_token),
            )
            if not corp_id or not access_token:
                raise OfficeAuthError("DingTalk enterprise identity unavailable")
            # Unified mode checks the exact configured CorpId allow-list in
            # SsoRuntime.finish, with the verified token result, not browser input.
            if not is_unified and corp_id != provider.enterprise_id:
                raise OfficeAuthError("DingTalk enterprise verification failed")
            # Optional organization-name lookup is separate from personal
            # directory access. Never trust a browser-supplied display name
            # or an undocumented field from the OAuth token response.
            name = ""
            name_source = "not_requested"
            needs_name = (
                fetch_enterprise_name(corp_id)
                if callable(fetch_enterprise_name) else fetch_enterprise_name
            )
            if needs_name:
                name_source = "unavailable"
                organization = {}
                try:
                    organization = await _get_json(await http.get(
                        "https://api.dingtalk.com/v1.0/contact/organizations/authInfos",
                        params={"targetCorpId": corp_id},
                        headers={"x-acs-dingtalk-access-token": access_token},
                    ))
                except (httpx.HTTPError, ValueError, TypeError) as exc:
                    logger.warning(
                        "SSO DingTalk organization name lookup failed: provider=%s "
                        "corp_id=%s credential=user_access_token error=%s",
                        provider.id, corp_id, type(exc).__name__,
                    )
                possible = organization.get("orgName")
                if (isinstance(possible, str) and possible.strip()
                        and len(possible.strip()) <= 80
                        and not any(ord(ch) < 32 for ch in possible)):
                    name = possible.strip()
                    name_source = "user_access_token"

                if not name:
                    # Org APIs can require an application access token rather
                    # than the login user's token. Internal applications can
                    # obtain one using the already configured AppKey/Secret.
                    try:
                        app_token = await _get_json(await http.post(
                            "https://api.dingtalk.com/v1.0/oauth2/accessToken",
                            json={"appKey": provider.app_id,
                                  "appSecret": provider.secret},
                        ))
                        app_access = str(app_token.get("accessToken") or "")
                        if app_access:
                            organization = await _get_json(await http.get(
                                "https://api.dingtalk.com/v1.0/contact/organizations/authInfos",
                                params={"targetCorpId": corp_id},
                                headers={"x-acs-dingtalk-access-token": app_access},
                            ))
                            possible = organization.get("orgName")
                            if (isinstance(possible, str) and possible.strip()
                                    and len(possible.strip()) <= 80
                                    and not any(ord(ch) < 32 for ch in possible)):
                                name = possible.strip()
                                name_source = "app_access_token"
                    except (httpx.HTTPError, ValueError, TypeError) as exc:
                        logger.warning(
                            "SSO DingTalk organization name lookup failed: provider=%s "
                            "corp_id=%s credential=app_access_token error=%s",
                            provider.id, corp_id, type(exc).__name__,
                        )
                if not name:
                    # CorpId-only entries still work without this permission;
                    # name-only entries must fail closed in SsoRuntime.
                    logger.warning(
                        "SSO DingTalk organization name unavailable: provider=%s "
                        "corp_id=%s name_source=%s",
                        provider.id, corp_id, name_source,
                    )
            logger.info(
                "SSO DingTalk verified enterprise identity: provider=%s corp_id=%s "
                "enterprise_name=%r name_source=%s name_lookup_requested=%s",
                provider.id, corp_id, name or "<empty>", name_source, bool(needs_name),
            )
            return EnterpriseIdentity("dingtalk", corp_id, "", name)

        app_token_data = await _get_json(await http.get(
            "https://qyapi.weixin.qq.com/cgi-bin/gettoken",
            params={"corpid": provider.enterprise_id, "corpsecret": provider.secret},
        ))
        if (app_token_data.get("errcode") != 0 or
                not app_token_data.get("access_token")):
            raise OfficeAuthError("WeCom app authentication failed")
        employee = await _get_json(await http.get(
            "https://qyapi.weixin.qq.com/cgi-bin/auth/getuserinfo",
            params={
                "access_token": app_token_data["access_token"],
                "code": code,
            },
        ))
        # WeCom returns openid for non-enterprise external contacts: reject.
        userid = str(employee.get("userid") or "")
        if employee.get("errcode") != 0 or not userid:
            raise OfficeAuthError("WeCom employee verification failed")
        logger.info("SSO WeCom corporate employee verified: provider=%s corp_id=%s external_user_id=%s",
                    provider.id, provider.enterprise_id, userid)
        return EnterpriseIdentity("wecom", provider.enterprise_id, userid)
    except (httpx.HTTPError, ValueError, TypeError) as exc:
        # Never include token payloads / HTTP query params in logs or errors.
        if isinstance(exc, OfficeAuthError):
            raise
        raise OfficeAuthError("Office identity verification unavailable") from None
    finally:
        if managed:
            await http.aclose()
