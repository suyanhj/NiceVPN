# -*- coding: utf-8 -*-
"""验证统一应用壳层与导航配置。"""

import asyncio

import pytest
from nicegui.testing import user_simulation

from app.services.user.crud import UserService
from app.services.user.device_bind import DeviceBindingService
from app.ui.components.app_shell import (
    NAVIGATION_ITEMS,
    render_app_shell,
    resolve_active_navigation_path,
)
from app.ui.pages.users import UsersPage


def test_navigation_paths_are_unique_and_keep_existing_urls() -> None:
    """一级导航不重复，并保持现有管理页面 URL。"""

    paths = tuple(item.path for item in NAVIGATION_ITEMS)
    assert len(paths) == len(set(paths))
    assert paths == (
        "/",
        "/users",
        "/groups",
        "/peers",
        "/firewall",
        "/certs",
        "/services",
        "/settings",
    )


def test_child_page_resolves_to_parent_navigation() -> None:
    """对端部署说明等子页面仍高亮所属一级模块。"""

    assert resolve_active_navigation_path("/peers/manual") == "/peers"
    assert resolve_active_navigation_path("/peers/manual/") == "/peers"
    assert resolve_active_navigation_path("/") == "/"
    assert resolve_active_navigation_path("/unknown") == ""


def test_shell_renders_all_navigation_and_marks_current_page() -> None:
    """壳层展示完整导航并只标记当前导航项。"""

    state: dict[str, object] = {}

    def page() -> None:
        state["shell"] = render_app_shell("对端部署说明", "/peers/manual")

    async def verify_page() -> None:
        async with user_simulation(page) as user:
            response = await user.open("/")
            assert response.status_code == 200
            for item in NAVIGATION_ITEMS:
                await user.should_see(item.label)

    asyncio.run(verify_page())

    shell = state["shell"]
    assert shell.active_path == "/peers"
    assert shell.drawer.value is None
    assert shell.navigation_toggle_button._props["aria-label"] == "切换主导航"
    assert shell.navigation_toggle_button._event_listeners
    assert "is-active" in shell.navigation_buttons["/peers"]._classes
    assert shell.navigation_buttons["/peers"]._props["aria-current"] == "page"
    inactive_buttons = [
        button
        for path, button in shell.navigation_buttons.items()
        if path != "/peers"
    ]
    assert all("is-active" not in button._classes for button in inactive_buttons)


def test_authenticated_shell_exposes_logout_entry() -> None:
    """认证启用时应用顶栏提供明确退出入口。"""

    state = _render_shell_for_test(auth_enabled=True)
    shell = state["shell"]
    assert shell.logout_button is not None
    assert shell.logout_button.text == "退出"


def _render_shell_for_test(*, auth_enabled: bool) -> dict[str, object]:
    """在独立客户端中构建指定认证模式的壳层。"""

    state: dict[str, object] = {}

    def page() -> None:
        state["shell"] = render_app_shell(
            "用户管理",
            "/users",
            auth_enabled=auth_enabled,
        )

    async def verify_page() -> None:
        async with user_simulation(page) as user:
            response = await user.open("/")
            assert response.status_code == 200

    asyncio.run(verify_page())
    return state


def test_shell_styles_include_responsive_navigation_breakpoint() -> None:
    """窄屏显示导航触发器，桌面仍由固定侧栏承载导航。"""

    from pathlib import Path

    shell_css = (
        Path(__file__).resolve().parents[2]
        / "app"
        / "ui"
        / "static"
        / "css"
        / "shell.css"
    ).read_text(encoding="utf-8")
    assert ".vpn-app-header" in shell_css
    assert "@media (max-width: 1023px)" in shell_css


def test_new_shell_can_wrap_legacy_users_page(monkeypatch: pytest.MonkeyPatch) -> None:
    """迁移期间新壳层可继续承载尚未重构的旧业务页面。"""

    monkeypatch.setattr(UserService, "list_all", lambda self: [])
    monkeypatch.setattr(UserService, "list_ccd_virtual_ipv4_by_username", lambda self: {})
    monkeypatch.setattr(DeviceBindingService, "build_user_binding_aux", lambda self: {})

    def page() -> None:
        render_app_shell("用户管理", "/users")
        UsersPage().render()

    async def verify_page() -> None:
        async with user_simulation(page) as user:
            response = await user.open("/")
            assert response.status_code == 200
            await user.should_see("新建用户")
            await user.should_see("暂无用户")

    asyncio.run(verify_page())
