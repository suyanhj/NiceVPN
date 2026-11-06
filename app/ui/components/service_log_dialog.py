# -*- coding: utf-8 -*-
"""Local and SSH peer service logs share one dialog structure and scroll layout."""

from nicegui import ui

LOG_TAIL_CODE_STYLE = "height: min(500px, 55vh); font-size: 12px;"


def log_dialog_card():
    """Keep the log card at a fixed height; only its log viewport scrolls."""
    return ui.card().classes("w-full services-view-dialog services-log-dialog")


def log_scroll_area(*, on_scroll=None):
    return (
        ui.scroll_area(on_scroll=on_scroll)
        .classes("w-full log-frame vpn-log-tail-viewport services-log-frame")
        .style(LOG_TAIL_CODE_STYLE)
    )


def log_text_label(text: str = ""):
    return (
        ui.label(text)
        .classes("w-full whitespace-pre-wrap break-words font-mono text-left")
        .style("font-size: 12px;")
    )


def log_action_row():
    """Footer outside the scroll area, shared by local and peer log dialogs."""
    return ui.row().classes(
        "services-log-dialog__actions w-full justify-end gap-sm items-center"
    )
