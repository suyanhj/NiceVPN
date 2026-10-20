# -*- coding: utf-8 -*-
"""管理后台应用壳层与一级导航配置。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from nicegui import ui

from app.ui.theme import setup_theme


@dataclass(frozen=True, slots=True)
class NavigationItem:
    """一级导航项。"""

    label: str
    icon: str
    path: str


@dataclass(slots=True)
class AppShell:
    """已渲染壳层的引用，供响应式行为和测试复用。"""

    page_title: str
    active_path: str
    drawer: Any
    navigation_toggle_button: Any
    logout_button: Any | None
    navigation_buttons: dict[str, Any]


NAVIGATION_ITEMS: tuple[NavigationItem, ...] = (
    NavigationItem("仪表盘", "dashboard", "/"),
    NavigationItem("用户管理", "people", "/users"),
    NavigationItem("组管理", "folder_shared", "/groups"),
    NavigationItem("对端站点", "hub", "/peers"),
    NavigationItem("防火墙规则", "shield", "/firewall"),
    NavigationItem("证书管理", "verified_user", "/certs"),
    NavigationItem("服务管理", "dns", "/services"),
    NavigationItem("系统设置", "settings", "/settings"),
)


def resolve_active_navigation_path(current_path: str) -> str:
    """将子页面路径归属到最接近的一级导航项。"""

    normalized = "/" + str(current_path or "").strip().strip("/")
    if normalized == "/":
        return "/"

    candidates = (
        item.path
        for item in NAVIGATION_ITEMS
        if item.path != "/"
        and (normalized == item.path or normalized.startswith(f"{item.path}/"))
    )
    return max(candidates, key=len, default="")


def render_app_shell(
    page_title: str,
    current_path: str,
    *,
    auth_enabled: bool = False,
) -> AppShell:
    """渲染统一侧边导航，并返回后续响应式控制所需引用。"""

    setup_theme()
    active_path = resolve_active_navigation_path(current_path)
    navigation_buttons: dict[str, Any] = {}

    with ui.left_drawer(value=None).props("width=248 bordered").classes(
        "vpn-navigation text-white"
    ) as drawer:
        drawer.props('aria-label="主导航"')
        with ui.column().classes("w-full q-pa-lg"):
            ui.label("OpenVPN").classes("vpn-nav-title")
            ui.label("Operations Console").classes("vpn-nav-subtitle")
            ui.label("主导航").classes("vpn-nav-group-label")

            with ui.column().classes("w-full gap-sm"):
                for item in NAVIGATION_ITEMS:
                    classes = "vpn-nav-button"
                    props = "flat no-caps align=left"
                    if active_path == item.path:
                        classes += " is-active"
                        props += " aria-current=page"
                    button = ui.button(
                        item.label,
                        icon=item.icon,
                        on_click=lambda target=item.path: ui.navigate.to(target),
                    ).props(props).classes(classes)
                    navigation_buttons[item.path] = button

    with ui.header().classes("vpn-app-header"):
        navigation_toggle_button = ui.button(
            icon="menu",
            on_click=drawer.toggle,
        ).props('flat round aria-label="切换主导航"').classes("vpn-navigation-toggle")
        ui.label(page_title).classes("vpn-app-page-title")
        logout_button = None
        if auth_enabled:
            from app.ui.auth import logout_admin_session

            ui.space()
            logout_button = ui.button(
                "退出",
                icon="logout",
                on_click=logout_admin_session,
            ).props("flat no-caps").classes("vpn-logout-button")

    ui.query(".nicegui-content").classes(add="vpn-shell")
    return AppShell(
        page_title=page_title,
        active_path=active_path,
        drawer=drawer,
        navigation_toggle_button=navigation_toggle_button,
        logout_button=logout_button,
        navigation_buttons=navigation_buttons,
    )
