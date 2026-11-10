# -*- coding: utf-8 -*-
"""OAuth adapters for enterprise-only membership verification.

Never treat an OAuth callback query parameter as an enterprise identity:
DingTalk's corpId is validated from the *server-side token exchange*, and
WeCom's member UserID is validated using an enterprise app token.
"""
from __future__ import annotations

from dataclasses import dataclass
import logging
import re
from urllib.parse import urlencode, urlsplit

import httpx

logger = logging.getLogger(__name__)
_SAFE_API_CODE = re.compile(r"^[A-Za-z0-9_.-]{1,120}$")


def _safe_error_detail(response: httpx.Response) -> tuple[str, str]:
    """Extract only documented error *identifiers*, never an API body or token."""
    try:
        body = response.json()
    except (ValueError, UnicodeDecodeError):
        return "unavailable", ""
    if not isinstance(body, dict):
        return "unavailable", ""
    raw_code = str(body.get("code") or body.get("errcode") or "")
    code = raw_code if _SAFE_API_CODE.fullmatch(raw_code) else "unavailable"
    access_detail = body.get("accessdenieddetail") or body.get("accessDeniedDetail") or {}
    raw_scopes = access_detail.get("requiredScopes", []) if isinstance(access_detail, dict) else []
    if not isinstance(raw_scopes, list):
        raw_scopes = []
    scopes = [scope for scope in raw_scopes[:12]
              if isinstance(scope, str) and _SAFE_API_CODE.fullmatch(scope)]
    return code, ",".join(scopes)


class OfficeAuthError(ValueError):
    """Safe, non-secret authentication error."""


@dataclass(frozen=True)
class OfficeProvider:
    id: str
    kind: str                 # dingtalk or wecom
    enterprise_id: str        # approved CorpId
    app_id: str               # DingTalk AppKey or WeCom AgentId
    secret: str               # server-only app secret
    read_user_profile: bool = False  # per-company, requires Contact.User.Read grant

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
            "scope": ("openid corpid Contact.User.Read" if provider.read_user_profile
                      else "openid corpid"),
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


async def _get_json(response: httpx.Response) -> dict:
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise OfficeAuthError("Unexpected office provider response")
    return payload


async def exchange_code(
    provider: OfficeProvider, code: str, *,
    client: httpx.AsyncClient | None = None,
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
            logger.info(
                "SSO DingTalk enterprise check: provider=%s app_id=%s expected_corp_id=%s "
                "verified_corp_id=%s corp_match=%s has_access_token=%s",
                provider.id, provider.app_id, provider.enterprise_id,
                corp_id or "<empty>", corp_id == provider.enterprise_id,
                bool(access_token),
            )
            if corp_id != provider.enterprise_id or not access_token:
                raise OfficeAuthError("DingTalk enterprise verification failed")
            # A user access token already proves OAuth authorization for this
            # organization. Personal identity is optional for the permission
            # decision but used for audit when the app has Contact.User.Read.
            user_id = ""
            if not provider.read_user_profile:
                logger.info("SSO DingTalk user detail skipped: provider=%s; "
                            "enable Contact.User.Read in provider settings after granting it",
                            provider.id)
                return EnterpriseIdentity("dingtalk", corp_id, user_id)
            try:
                detail = await _get_json(await http.get(
                    "https://api.dingtalk.com/v1.0/contact/users/me",
                    headers={"x-acs-dingtalk-access-token": access_token},
                ))
                user_id = str(detail.get("unionId") or detail.get("openId") or "")
                logger.info(
                    "SSO DingTalk user detail: provider=%s corp_id=%s external_user_id=%s "
                    "identity_source=%s",
                    provider.id, corp_id, user_id or "<unavailable>",
                    "unionId" if detail.get("unionId") else "openId" if detail.get("openId") else "none",
                )
                if detail.get("nick") or detail.get("name"):
                    # Names are personal data. Only emit at DEBUG level;
                    # never log the entire provider response.
                    logger.debug("SSO DingTalk user display name: provider=%s name=%s",
                                 provider.id, str(detail.get("nick") or detail.get("name"))[:80])
            except httpx.HTTPStatusError as exc:
                code, required_scopes = _safe_error_detail(exc.response)
                logger.warning(
                    "SSO DingTalk user detail unavailable: provider=%s corp_id=%s "
                    "status=%s error_code=%s required_scopes=%s; corporate login remains valid",
                    provider.id, corp_id, exc.response.status_code, code,
                    required_scopes or "<not_provided>",
                )
            except (httpx.HTTPError, ValueError, TypeError) as exc:
                logger.warning(
                    "SSO DingTalk user detail unavailable: provider=%s corp_id=%s reason=%s; "
                    "corporate login remains valid",
                    provider.id, corp_id, type(exc).__name__,
                )
            return EnterpriseIdentity("dingtalk", corp_id, user_id)

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
