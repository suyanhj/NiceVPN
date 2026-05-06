# -*- coding: utf-8 -*-
"""从 HTTP 请求解析对外访问基础 URL，用于生成一次性下载链接等。"""
from __future__ import annotations

from typing import Optional
from urllib.parse import urlparse

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
        scheme = (forwarded_proto or "https").split(",")[0].strip()
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
    """
    解析下载链接使用的 scheme://host[:port][/prefix]。

    优先级：
    1. 配置项 download_base_url 非空则直接使用
    2. 根据当前请求推断（与浏览器地址栏一致）
    3. 若主机为 localhost/127.0.0.1/::1，使用 Web 启动时缓存的本机监听侧地址（ifaddr，优先 eth0），并拼接请求中的 path 前缀（子路径反代）
    4. 无缓存则返回 None

    Args:
        request: 当前 NiceGUI 客户端请求；不可用时传 None
        configured: 系统配置中的 download_base_url

    Returns:
        无末尾斜杠的基础 URL；无法确定时 None
    """
    if configured and configured.strip():
        return configured.strip().rstrip("/")

    if request is None:
        return None

    base = public_base_url_from_request(request)
    parsed = urlparse(base + "/")
    host = parsed.hostname

    if _is_loopback_host(host):
        snap = get_listen_http_base()
        if not snap:
            return None
        path = (parsed.path or "").rstrip("/")
        if path:
            return f"{snap.rstrip('/')}{path}"
        return snap

    return base


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
