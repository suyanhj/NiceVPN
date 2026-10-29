# -*- coding: utf-8 -*-
"""验证管理后台认证路由与会话辅助逻辑。"""

import time

import pytest
from fastapi import Request
from nicegui import app
from starlette.responses import PlainTextResponse

from app.ui.auth import (
    AdminAuthMiddleware,
    clear_admin_session,
    is_public_auth_path,
    sanitize_redirect_target,
    session_is_authenticated,
    render_login_form,
)


@pytest.mark.parametrize(
    ("value", "expected"),
    (
        (None, "/"),
        ("", "/"),
        ("/users", "/users"),
        ("/services?tab=peers&peer=east", "/services?tab=peers&peer=east"),
        ("https://example.com/steal", "/"),
        ("//example.com/steal", "/"),
        ("/%2F%2Fexample.com/steal", "/"),
        ("/\\example.com", "/"),
        ("/login", "/"),
        ("users", "/"),
    ),
)
def test_redirect_target_only_allows_internal_paths(value, expected) -> None:
    """登录返回地址必须是经过校验的站内绝对路径。"""

    assert sanitize_redirect_target(value) == expected


@pytest.mark.parametrize(
    "path",
    (
        "/login",
        "/favicon.ico",
        "/_nicegui/client/abc",
        "/static/css/tokens.css",
        "/api/vpn/users",
        "/download/token-value",
    ),
)
def test_public_paths_keep_their_existing_security_boundary(path: str) -> None:
    """登录页、框架资源、静态资源、API 与下载令牌不改用页面会话认证。"""

    assert is_public_auth_path(path) is True


@pytest.mark.parametrize("path", ("/", "/users", "/peers/manual", "/settings"))
def test_management_pages_are_protected(path: str) -> None:
    """所有后台管理页面都必须经过会话认证。"""

    assert is_public_auth_path(path) is False


def test_session_requires_authentication_flag_and_future_expiry() -> None:
    """缺少状态、过期或非法时间均按未登录处理。"""

    now = time.time()
    assert session_is_authenticated(
        {"authenticated": True, "expires_at": now + 60},
        now=now,
    )
    assert not session_is_authenticated(
        {"authenticated": True, "expires_at": now - 1},
        now=now,
    )
    assert not session_is_authenticated({"authenticated": True}, now=now)
    assert not session_is_authenticated(
        {"authenticated": False, "expires_at": now + 60},
        now=now,
    )


def test_login_page_builds_with_complete_form() -> None:
    """认证开启模式的登录页面可完整构建。"""

    form = render_login_form(lambda: None)
    assert form.username._props["autocomplete"] == "username"
    assert form.password._props["type"] == "password"
    assert form.submit.text == "登录"
    assert form.submit._event_listeners


def test_logout_clears_all_session_state() -> None:
    """退出只保留非敏感日志标识并清空认证状态。"""

    session = {
        "authenticated": True,
        "username": "admin",
        "expires_at": time.time() + 60,
    }
    assert clear_admin_session(session) == "admin"
    assert session == {}


def test_auth_middleware_redirects_anonymous_page_and_keeps_target(monkeypatch) -> None:
    """匿名管理页面请求跳转登录并保存合法原始目标。"""

    import asyncio

    session: dict[str, object] = {}
    monkeypatch.setattr(type(app.storage), "user", property(lambda self: session))
    middleware = AdminAuthMiddleware(lambda scope, receive, send: None)
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "scheme": "http",
            "path": "/users",
            "raw_path": b"/users",
            "query_string": b"page=2",
            "headers": [],
            "client": ("10.0.0.8", 12345),
            "server": ("testserver", 80),
        }
    )

    async def call_next(_request):
        return PlainTextResponse("allowed")

    response = asyncio.run(middleware.dispatch(request, call_next))
    assert response.status_code == 303
    assert response.headers["location"].startswith("/login?redirect_to=")
    assert session == {"redirect_after_login": "/users?page=2"}


@pytest.mark.parametrize("path", ("/static/css/tokens.css", "/api/vpn/users", "/download/abc"))
def test_auth_middleware_does_not_replace_existing_endpoint_security(
    path: str,
    monkeypatch,
) -> None:
    """匿名静态资源、API 和令牌下载请求直接进入原处理链。"""

    import asyncio

    session: dict[str, object] = {}
    monkeypatch.setattr(type(app.storage), "user", property(lambda self: session))
    middleware = AdminAuthMiddleware(lambda scope, receive, send: None)
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "scheme": "http",
            "path": path,
            "raw_path": path.encode(),
            "query_string": b"",
            "headers": [],
            "client": ("10.0.0.8", 12345),
            "server": ("testserver", 80),
        }
    )

    async def call_next(_request):
        return PlainTextResponse("existing security", status_code=204)

    response = asyncio.run(middleware.dispatch(request, call_next))
    assert response.status_code == 204
    assert session == {}
