# -*- coding: utf-8 -*-
"""统一页面标题、面包屑和操作区。"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from nicegui import ui


@dataclass(frozen=True, slots=True)
class BreadcrumbItem:
    """面包屑条目；没有路径时表示当前页面。"""

    label: str
    path: str | None = None


@dataclass(slots=True)
class PageHeader:
    """已渲染页头的组件引用。"""

    container: Any
    title: Any
    description: Any | None
    breadcrumbs: Any | None
    actions: Any | None


def render_page_header(
    title: str,
    *,
    description: str | None = None,
    breadcrumbs: Sequence[BreadcrumbItem] = (),
    actions: Callable[[], None] | None = None,
) -> PageHeader:
    """渲染页面标题和可选操作；description 仅保留为兼容参数，不再展示。"""

    normalized_title = str(title).strip()
    if not normalized_title:
        raise ValueError("页面标题不能为空")

    description_element = None
    breadcrumb_element = None
    actions_element = None

    with ui.element("section").classes("vpn-page-header") as container:
        with ui.element("div").classes("vpn-page-header__copy"):
            if breadcrumbs:
                with ui.element("nav").props('aria-label="面包屑"').classes(
                    "vpn-breadcrumbs"
                ) as breadcrumb_element:
                    for index, item in enumerate(breadcrumbs):
                        if index:
                            ui.icon("chevron_right").classes("vpn-breadcrumbs__separator")
                        if item.path:
                            ui.link(item.label, item.path).classes("vpn-breadcrumbs__link")
                        else:
                            ui.label(item.label).props("aria-current=page").classes(
                                "vpn-breadcrumbs__current"
                            )

            title_element = ui.label(normalized_title).classes("vpn-page-header__title")

        if actions is not None:
            with ui.element("div").classes("vpn-page-header__actions") as actions_element:
                actions()

    return PageHeader(
        container=container,
        title=title_element,
        description=description_element,
        breadcrumbs=breadcrumb_element,
        actions=actions_element,
    )
