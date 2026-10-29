# -*- coding: utf-8 -*-
"""服务端分页的共享轻量页脚。"""

from __future__ import annotations

from collections.abc import Callable

from nicegui import ui


def render_server_pager(
    *,
    total: int,
    page: int,
    page_size: int,
    on_change: Callable[[int], object],
) -> None:
    """渲染上一页/下一页页脚；数据本身由调用方按数据库页读取。"""

    safe_total = max(0, int(total))
    safe_size = max(1, int(page_size))
    pages = max(1, (safe_total + safe_size - 1) // safe_size)
    current = min(max(1, int(page)), pages)

    with ui.row().classes("w-full items-center justify-end gap-1 q-pt-sm"):
        previous = ui.button(
            icon="chevron_left",
            on_click=lambda: on_change(current - 1),
        ).props("flat round dense aria-label='上一页'")
        ui.label(f"第 {current} / {pages} 页 · 共 {safe_total} 条").classes(
            "text-caption text-grey-6"
        )
        following = ui.button(
            icon="chevron_right",
            on_click=lambda: on_change(current + 1),
        ).props("flat round dense aria-label='下一页'")
        if current <= 1:
            previous.disable()
        if current >= pages:
            following.disable()
