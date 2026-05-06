# -*- coding: utf-8 -*-
"""告警卡片组件。"""

from nicegui import ui

_LEVEL_STYLES = {
    "info": {"accent": "#2dd4bf", "bg": "rgba(45, 212, 191, 0.12)", "icon": "info"},
    "warning": {"accent": "#f59e0b", "bg": "rgba(245, 158, 11, 0.14)", "icon": "warning"},
    "error": {"accent": "#fb7185", "bg": "rgba(251, 113, 133, 0.14)", "icon": "error"},
}


def show(level: str, title: str, message: str) -> None:
    """显示告警卡片。"""
    style = _LEVEL_STYLES.get(level, _LEVEL_STYLES["info"])

    with ui.card().classes("alert-card w-full"):
        with ui.row().classes("items-start no-wrap w-full gap-md"):
            with ui.element("div").style(
                f"width: 40px; height: 40px; border-radius: 12px; "
                f"background: {style['bg']}; color: {style['accent']}; "
                "display: flex; align-items: center; justify-content: center;"
            ):
                ui.icon(style["icon"]).classes("text-subtitle1")

            with ui.column().classes("gap-1 w-full"):
                ui.label(title).classes("text-weight-medium")
                ui.label(message).classes("section-caption")
