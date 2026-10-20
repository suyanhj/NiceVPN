# -*- coding: utf-8 -*-
"""验证响应式页面工具栏与批量操作栏。"""

import asyncio
from pathlib import Path

import pytest
from nicegui import ui
from nicegui.testing import user_simulation

from app.ui.components.actions import danger_action, primary_action, secondary_action
from app.ui.components.toolbars import render_bulk_action_bar, render_page_toolbar


def test_page_toolbar_keeps_search_primary_and_secondary_regions() -> None:
    """搜索、主操作和低频操作分别位于稳定区域。"""

    state: dict[str, object] = {}

    def page() -> None:
        state["toolbar"] = render_page_toolbar(
            search=lambda: ui.input("搜索用户", placeholder="用户名或虚拟 IP"),
            primary=lambda: primary_action("新建用户", icon="person_add"),
            secondary=lambda: secondary_action("刷新", icon="refresh"),
        )

    async def verify_page() -> None:
        async with user_simulation(page) as user:
            response = await user.open("/")
            assert response.status_code == 200
            await user.should_see("搜索用户")
            await user.should_see("新建用户")
            await user.should_see("刷新")

    asyncio.run(verify_page())
    toolbar = state["toolbar"]
    assert toolbar.search is not None
    assert toolbar.primary is not None
    assert toolbar.secondary is not None


def test_bulk_bar_updates_count_and_keeps_actions() -> None:
    """批量操作栏可更新数量且保留普通与危险操作。"""

    state: dict[str, object] = {}

    def page() -> None:
        def actions() -> None:
            secondary_action("批量启用", icon="toggle_on")
            danger_action("批量删除", icon="delete")

        state["bulk"] = render_bulk_action_bar(2, actions=actions)

    async def verify_page() -> None:
        async with user_simulation(page) as user:
            response = await user.open("/")
            assert response.status_code == 200
            await user.should_see("已选择 2 项")
            await user.should_see("批量启用")
            await user.should_see("批量删除")

    asyncio.run(verify_page())
    bulk = state["bulk"]
    bulk.set_selected_count(5)
    assert bulk.count_label.text == "已选择 5 项"
    assert bulk.actions is not None


def test_toolbars_reject_negative_selected_count() -> None:
    """非法选中数量应尽早报错。"""

    with pytest.raises(ValueError, match="不能小于 0"):
        render_bulk_action_bar(-1)


def test_toolbar_css_preserves_critical_regions_on_narrow_screens() -> None:
    """窄屏规则明确保留搜索、主操作和选中数量区域。"""

    source = (
        Path(__file__).resolve().parents[2]
        / "app"
        / "ui"
        / "static"
        / "css"
        / "shell.css"
    ).read_text(encoding="utf-8")
    assert ".vpn-toolbar__search" in source
    assert ".vpn-toolbar__primary" in source
    assert ".vpn-bulk-bar__count" in source
    assert "@media (max-width: 767px)" in source
