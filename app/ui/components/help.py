# -*- coding: utf-8 -*-
"""紧凑帮助提示：把解释性文案收进可悬浮的信息图标。"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from nicegui import ui


def help_hint(text: str, *, aria_label: str = "查看说明") -> Any:
    """渲染小型帮助图标；解释文案通过 Tooltip 展示。"""

    normalized = str(text or "").strip()
    if not normalized:
        raise ValueError("帮助说明不能为空")

    icon = (
        ui.icon("help_outline")
        .classes("vpn-help-hint")
        .props(f'tabindex=0 role=img aria-label="{aria_label}"')
    )
    icon.tooltip(normalized)
    return icon


def field_with_help(
    render_field: Callable[[], Any],
    text: str,
    *,
    aria_label: str = "查看说明",
    control_grow: bool = True,
    align: str = "center",
) -> Any:
    """把帮助图标与其所属表单控件放在同一行，避免图标掉到下一行。

    Args:
        render_field: 在布局行内部创建并返回 NiceGUI 表单控件的回调。
        text: Tooltip 说明。
        aria_label: 帮助图标的无障碍名称。
        control_grow: True 时控件占据剩余宽度；False 时按内容宽度展示，
            适合 switch/toggle 等带内置标签的控件。
        align: 图标与控件的垂直对齐方式，支持 center/start。
    """

    if align not in {"center", "start"}:
        raise ValueError("帮助图标对齐方式必须为 center 或 start")

    row_classes = "w-full no-wrap vpn-field-help-row"
    row_classes += " vpn-field-help-row--start" if align == "start" else " items-center"

    with ui.row().classes(row_classes):
        field = render_field()
        field.classes(
            "vpn-field-help__control"
            + (" vpn-field-help__control--grow" if control_grow else " vpn-field-help__control--intrinsic")
        )
        help_hint(text, aria_label=aria_label)

    return field
