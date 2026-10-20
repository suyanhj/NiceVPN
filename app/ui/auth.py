# -*- coding: utf-8 -*-
"""管理后台登录页、会话状态和路由保护。"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import unquote, urlencode, urlsplit, urlunsplit

from fastapi import Request
from fastapi.responses import RedirectResponse
from nicegui import app, ui
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response

from app.services.auth import AdminLoginManager
from app.ui.theme import setup_theme
from app.utils.audit_log import AuditLogger


logger = logging.getLogger(__name__)

_PUBLIC_EXACT_PATHS = frozenset({"/login", "/favicon.ico"})
_PUBLIC_PATH_PREFIXES = ("/_nicegui", "/static", "/api", "/download")


@dataclass(slots=True)
class LoginForm:
    """登录表单组件引用。"""

    username: Any
    password: Any
    submit: Any


def sanitize_redirect_target(value: str | None) -> str:
    """只允许站内绝对路径作为登录后的跳转目标。"""

    candidate = str(value or "").strip()
    if not candidate or any(ord(character) < 32 for character in candidate):
        return "/"
    parsed = urlsplit(candidate)
    decoded_path = unquote(parsed.path)
    if (
        parsed.scheme
        or parsed.netloc
        or parsed.fragment
        or not parsed.path.startswith("/")
        or parsed.path.startswith("//")
        or decoded_path.startswith("//")
        or "\\" in parsed.path
        or "\\" in decoded_path
        or parsed.path == "/login"
    ):
        return "/"
    return urlunsplit(("", "", parsed.path, parsed.query, ""))


def is_public_auth_path(path: str) -> bool:
    """判断路径是否应继续使用自身既有安全边界。"""

    if path in _PUBLIC_EXACT_PATHS:
        return True
    return any(path == prefix or path.startswith(f"{prefix}/") for prefix in _PUBLIC_PATH_PREFIXES)


def session_is_authenticated(session: Mapping, *, now: float | None = None) -> bool:
    """仅接受带有未过期时间的已认证会话。"""

    if session.get("authenticated") is not True:
        return False
    try:
        expires_at = float(session.get("expires_at", 0))
    except (TypeError, ValueError):
        return False
    if now is None:
        import time

        now = time.time()
    return expires_at > now


def render_login_form(on_submit) -> LoginForm:
    """渲染可独立测试的登录表单。"""

    with ui.column().classes("vpn-login-page"):
        with ui.card().classes("vpn-login-card"):
            ui.icon("shield").classes("vpn-login-icon")
            ui.label("OpenVPN 管理后台").classes("vpn-login-title")
            ui.label("使用管理员凭据登录").classes("vpn-login-description")
            username = ui.input("管理员用户名").props("autofocus autocomplete=username")
            password = ui.input(
                "管理员密码",
                password=True,
                password_toggle_button=True,
            ).props("autocomplete=current-password")
            username.on("keydown.enter", lambda: password.run_method("focus"))
            password.on("keydown.enter", on_submit)
            submit = ui.button("登录", icon="login", on_click=on_submit).props(
                "unelevated color=primary"
            ).classes("w-full")
    return LoginForm(username=username, password=password, submit=submit)


class AdminAuthMiddleware(BaseHTTPMiddleware):
    """在认证启用时保护全部管理页面。"""

    async def dispatch(
        self,
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        path = request.url.path
        if is_public_auth_path(path):
            return await call_next(request)

        session = app.storage.user
        if session_is_authenticated(session):
            return await call_next(request)

        session.clear()
        target = sanitize_redirect_target(
            f"{path}?{request.url.query}" if request.url.query else path
        )
        session["redirect_after_login"] = target
        login_url = f"/login?{urlencode({'redirect_to': target})}"
        return RedirectResponse(login_url, status_code=303)


def logout_admin_session() -> None:
    """清空当前管理会话并返回登录页。"""

    username = clear_admin_session(app.storage.user)
    logger.info("管理后台退出 username=%s", username)
    ui.navigate.to("/login")


def clear_admin_session(session) -> str:
    """清除会话状态并返回仅供非敏感日志使用的用户名。"""

    username = str(session.get("username") or "admin")
    session.clear()
    return username


def register_admin_auth(config) -> None:
    """注册认证中间件与登录页；只允许在完整凭据模式下调用。"""

    configured_username = str(config.admin_username)
    configured_password_hash = str(config.admin_password_hash)
    session_lifetime_seconds = int(config.admin_session_lifetime_minutes) * 60
    login_manager = AdminLoginManager(
        configured_username=configured_username,
        configured_password_hash=configured_password_hash,
        session_lifetime_seconds=session_lifetime_seconds,
    )

    app.add_middleware(AdminAuthMiddleware)

    @ui.page("/login")
    def login_page(request: Request, redirect_to: str = "/"):
        if session_is_authenticated(app.storage.user):
            return RedirectResponse(sanitize_redirect_target(redirect_to), status_code=303)

        setup_theme()
        form: LoginForm

        def try_login() -> None:
            username_value = str(form.username.value or "")
            password_value = str(form.password.value or "")
            source = request.client.host if request.client else "unknown"
            result = login_manager.attempt_login(
                app.storage.user,
                submitted_username=username_value,
                submitted_password=password_value,
                source=source,
            )
            if result.blocked:
                logger.warning("管理后台登录受限 source=%s", source)
                AuditLogger().log(
                    "admin_login",
                    "admin_session",
                    source,
                    "失败次数达到限制",
                    "failure",
                )
                ui.notify("登录尝试过多，请稍后重试", type="negative")
                form.password.set_value("")
                form.password.run_method("focus")
                return
            if not result.success:
                logger.warning("管理后台登录失败 source=%s", source)
                AuditLogger().log(
                    "admin_login",
                    "admin_session",
                    source,
                    "凭据验证失败",
                    "failure",
                )
                ui.notify("用户名或密码错误", type="negative")
                form.password.set_value("")
                form.password.run_method("focus")
                return

            target = sanitize_redirect_target(
                redirect_to or app.storage.user.get("redirect_after_login")
            )
            logger.info("管理后台登录成功 username=%s source=%s", configured_username, source)
            AuditLogger().log(
                "admin_login",
                "admin_session",
                configured_username,
                f"登录成功，来源 {source}",
                "success",
            )
            ui.navigate.to(target)

        form = render_login_form(try_login)

        return None
