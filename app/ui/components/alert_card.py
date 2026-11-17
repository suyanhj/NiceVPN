# -*- coding: utf-8 -*-
"""告警卡片组件。"""

from nicegui import ui

_LEVEL_STYLES = {
    "info": {"class": "alert-card__icon-wrap--info", "icon": "info"},
    "warning": {"class": "alert-card__icon-wrap--warning", "icon": "warning"},
    "error": {"class": "alert-card__icon-wrap--error", "icon": "error"},
}


def show(level: str, title: str, message: str) -> None:
    """显示告警卡片。"""
    style = _LEVEL_STYLES.get(level, _LEVEL_STYLES["info"])

    with ui.card().classes("alert-card w-full"):
        with ui.row().classes("items-start no-wrap w-full gap-md"):
            with ui.element("div").classes(
                f"alert-card__icon-wrap {style['class']}"
            ):
                ui.icon(style["icon"]).classes("text-subtitle1")

            with ui.column().classes("gap-1 w-full"):
                ui.label(title).classes("text-weight-medium")
                ui.label(message).classes("section-caption")
