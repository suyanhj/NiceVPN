# -*- coding: utf-8 -*-
"""紧凑帮助提示：把解释性文案收进可悬浮的信息图标。"""

from __future__ import annotations

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
