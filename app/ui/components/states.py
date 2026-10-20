# -*- coding: utf-8 -*-
"""统一加载、空数据、无匹配和错误状态。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from nicegui import ui

from app.ui.components.actions import secondary_action


@dataclass(slots=True)
class ViewState:
    container: Any
    title: Any
    description: Any | None
    action: Any | None


def loading_state(message: str = "正在加载…", *, refreshing: bool = False) -> ViewState:
    """渲染首次加载或局部刷新状态。"""

    text = str(message).strip() or ("正在刷新…" if refreshing else "正在加载…")
    with ui.column().classes("vpn-view-state vpn-view-state--loading items-center") as container:
        ui.spinner("dots", size="32px", color="primary")
        title = ui.label(text).classes("vpn-view-state__title")
    return ViewState(container=container, title=title, description=None, action=None)


def empty_state(
    title: str = "暂无数据",
    *,
    description: str | None = None,
    action_label: str | None = None,
    on_action: Callable[..., Any] | None = None,
    icon: str = "inbox",
) -> ViewState:
    """渲染真正的空数据状态。"""

    return _state(
        "empty",
        title,
        description=description,
        action_label=action_label,
        on_action=on_action,
        icon=icon,
    )


def no_match_state(
    *,
    description: str = "当前筛选条件没有匹配结果。",
    on_clear: Callable[..., Any] | None = None,
) -> ViewState:
    """渲染数据存在但搜索/筛选无匹配的状态。"""

    return _state(
        "no-match",
        "没有匹配结果",
        description=description,
        action_label="清除筛选" if on_clear else None,
        on_action=on_clear,
        icon="search_off",
    )


def error_state(
    message: str,
    *,
    title: str = "加载失败",
    on_retry: Callable[..., Any] | None = None,
) -> ViewState:
    """渲染可独立恢复的错误状态。"""

    return _state(
        "error",
        title,
        description=str(message),
        action_label="重试" if on_retry else None,
        on_action=on_retry,
        icon="error_outline",
    )


def _state(
    kind: str,
    title_text: str,
    *,
    description: str | None,
    action_label: str | None,
    on_action: Callable[..., Any] | None,
    icon: str,
) -> ViewState:
    normalized_title = str(title_text).strip()
    if not normalized_title:
        raise ValueError("状态标题不能为空")

    with ui.column().classes(f"vpn-view-state vpn-view-state--{kind} items-center") as container:
        ui.icon(icon).classes("vpn-view-state__icon")
        title = ui.label(normalized_title).classes("vpn-view-state__title")
        description_element = (
            ui.label(str(description)).classes("vpn-view-state__description")
            if description
            else None
        )
        action = (
            secondary_action(action_label, on_click=on_action)
            if action_label and on_action is not None
            else None
        )
    return ViewState(
        container=container,
        title=title,
        description=description_element,
        action=action,
    )
