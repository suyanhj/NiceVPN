# -*- coding: utf-8 -*-
"""响应式页面工具栏和批量操作栏。"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from nicegui import ui


@dataclass(slots=True)
class PageToolbar:
    """页面工具栏区域引用。"""

    container: Any
    search: Any | None
    primary: Any | None
    secondary: Any | None


@dataclass(slots=True)
class BulkActionBar:
    """批量操作栏引用与选中数量更新入口。"""

    container: Any
    count_label: Any
    actions: Any | None

    def set_selected_count(self, count: int) -> None:
        """更新选中数量。"""

        if count < 0:
            raise ValueError("选中数量不能小于 0")
        self.count_label.text = f"已选择 {count} 项"


def render_page_toolbar(
    *,
    search: Callable[[], None] | None = None,
    primary: Callable[[], None] | None = None,
    secondary: Callable[[], None] | None = None,
) -> PageToolbar:
    """渲染可换行工具栏，并为搜索和主操作保留独立区域。"""

    search_container = None
    primary_container = None
    secondary_container = None
    with ui.element("section").classes("vpn-toolbar") as container:
        if search is not None:
            with ui.element("div").classes("vpn-toolbar__search") as search_container:
                search()
        if primary is not None:
            with ui.element("div").classes("vpn-toolbar__primary") as primary_container:
                primary()
        if secondary is not None:
            with ui.element("div").classes("vpn-toolbar__secondary") as secondary_container:
                secondary()
    return PageToolbar(
        container=container,
        search=search_container,
        primary=primary_container,
        secondary=secondary_container,
    )


def render_bulk_action_bar(
    selected_count: int,
    *,
    actions: Callable[[], None] | None = None,
) -> BulkActionBar:
    """渲染始终可见选中数量的批量操作栏。"""

    if selected_count < 0:
        raise ValueError("选中数量不能小于 0")
    actions_container = None
    with ui.element("section").classes("vpn-bulk-bar") as container:
        count_label = ui.label(f"已选择 {selected_count} 项").classes(
            "vpn-bulk-bar__count"
        )
        if actions is not None:
            with ui.element("div").classes("vpn-bulk-bar__actions") as actions_container:
                actions()
    return BulkActionBar(
        container=container,
        count_label=count_label,
        actions=actions_container,
    )
