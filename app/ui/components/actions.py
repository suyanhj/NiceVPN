# -*- coding: utf-8 -*-
"""统一页面操作按钮和更多菜单。"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from nicegui import ui


ActionVariant = Literal["primary", "create", "secondary", "danger", "more"]


@dataclass(frozen=True, slots=True)
class MenuAction:
    """更多菜单中的单个操作。"""

    label: str
    on_click: Callable[..., Any] | None = None
    icon: str | None = None
    danger: bool = False
    disabled: bool = False
    tooltip: str | None = None


@dataclass(slots=True)
class MoreActionMenu:
    """更多操作按钮、菜单和菜单项引用。"""

    button: Any
    menu: Any
    items: list[Any]


_VARIANT_PROPS: dict[ActionVariant, str] = {
    "primary": "unelevated color=primary",
    "create": "outline color=primary",
    "secondary": "outline color=primary",
    "danger": "outline color=negative",
    "more": "flat color=primary",
}


def action_button(
    label: str,
    *,
    icon: str | None = None,
    on_click: Callable[..., Any] | None = None,
    variant: ActionVariant = "secondary",
    disabled: bool = False,
    busy: bool = False,
    tooltip: str | None = None,
):
    """创建文案、图标和状态一致的操作按钮。"""

    normalized_label = str(label).strip()
    if not normalized_label:
        raise ValueError("操作按钮文案不能为空")
    if variant not in _VARIANT_PROPS:
        raise ValueError(f"不支持的操作按钮类型: {variant}")

    button = ui.button(
        normalized_label,
        icon=icon,
        on_click=on_click,
    ).props(f"no-caps {_VARIANT_PROPS[variant]}").classes(
        f"vpn-action vpn-action--{variant}"
    )
    if disabled or busy:
        button.disable()
    if busy:
        button.props("loading")
    if tooltip:
        button.tooltip(str(tooltip))
    return button


def primary_action(label: str, **kwargs):
    """创建主操作按钮。"""

    return action_button(label, variant="primary", **kwargs)


def create_action(label: str, **kwargs):
    """创建页级新增/创建操作按钮。"""

    return action_button(label, variant="create", **kwargs)


def secondary_action(label: str, **kwargs):
    """创建普通操作按钮。"""

    return action_button(label, variant="secondary", **kwargs)


def danger_action(label: str, **kwargs):
    """创建危险操作按钮。"""

    return action_button(label, variant="danger", **kwargs)


def more_actions(
    actions: Sequence[MenuAction],
    *,
    label: str = "更多",
    icon: str = "more_horiz",
    disabled: bool = False,
    busy: bool = False,
    tooltip: str | None = None,
) -> MoreActionMenu:
    """创建低频操作菜单，并保留危险和禁用语义。"""

    button = action_button(
        label,
        icon=icon,
        variant="more",
        disabled=disabled or not actions,
        busy=busy,
        tooltip=tooltip,
    )
    items: list[Any] = []
    with button:
        with ui.menu().classes("vpn-action-menu") as menu:
            for action in actions:
                normalized_label = str(action.label).strip()
                if not normalized_label:
                    raise ValueError("更多菜单操作文案不能为空")
                with ui.menu_item(on_click=action.on_click).classes(
                    "vpn-action-menu__item" + (" text-negative" if action.danger else "")
                ) as item:
                    if action.icon:
                        with ui.item_section().props("avatar"):
                            ui.icon(action.icon)
                    with ui.item_section():
                        ui.item_label(normalized_label)
                if action.disabled:
                    item.disable()
                if action.tooltip:
                    item.tooltip(action.tooltip)
                items.append(item)
    return MoreActionMenu(button=button, menu=menu, items=items)
