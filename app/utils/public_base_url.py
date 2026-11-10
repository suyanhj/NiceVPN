# -*- coding: utf-8 -*-
"""从 HTTP 请求解析对外访问基础 URL，用于生成一次性下载链接等。"""
from __future__ import annotations

import os
from typing import Optional
from urllib.parse import urlsplit

from app.core.constants import DEFAULT_PORT

from starlette.requests import Request

from app.utils.listen_lan import get_listen_http_base


def _is_loopback_host(host: Optional[str]) -> bool:
    if not host:
        return False
    h = host.lower().strip("[]")
    if h in ("localhost", "::1"):
        return True
    return h.startswith("127.")


def public_base_url_from_request(request: Request) -> str:
    """
    返回无末尾斜杠的 base URL。

    - 存在 X-Forwarded-Host 时信任 X-Forwarded-Proto / X-Forwarded-Prefix，并与 ASGI root_path 拼接（反代子路径）。
    - 否则使用 Starlette 的 request.base_url（已含 root_path）。
    """
    headers = request.headers
    forwarded_host = headers.get("x-forwarded-host")
    if forwarded_host:
        host = forwarded_host.split(",")[0].strip()
        forwarded_proto = headers.get("x-forwarded-proto")
        scheme = (forwarded_proto or request.url.scheme).split(",")[0].strip()
        fwd_prefix = (headers.get("x-forwarded-prefix") or "").strip()
        root_path = (request.scope.get("root_path") or "").strip()
        prefix = (fwd_prefix + root_path).rstrip("/")
        if prefix and not prefix.startswith("/"):
            prefix = "/" + prefix
        return f"{scheme}://{host}{prefix}".rstrip("/")

    return str(request.base_url).rstrip("/")


def resolve_download_base_url(
    request: Optional[Request],
    configured: Optional[str],
) -> Optional[str]:
    """Resolve a one-time download URL from config or the startup API listener IP.

    Configured URL takes priority. If omitted, always use the API listener
    base captured at startup (local IP + API port). Do not infer it from the
    admin browser address or untrusted forwarded headers.
    `request` remains optional for compatibility with existing callers.
    """
    if configured and configured.strip():
        url = configured.strip().rstrip("/")
        try:
            if urlsplit(url).port == int(
                os.environ.get("NICEVPN_UI_PORT", str(DEFAULT_PORT))
            ):
                return None
        except ValueError:
            return None
        return url

    return get_listen_http_base()


def resolve_ui_download_base_url(
    request: Optional[Request],
    configured: Optional[str],
) -> Optional[str]:
    """UI copy/push uses exactly the same download URL policy as the API."""
    return resolve_download_base_url(request, configured)


def get_ui_request() -> Optional[Request]:
    """
    获取当前 UI 会话对应的 Starlette Request。

    在后台任务或非页面上下文中可能不可用，返回 None。
    """
    try:
        from nicegui import context

        return context.client.request
    except RuntimeError:
        return None
