# -*- coding: utf-8 -*-
"""服务端分页的共享轻量页脚。"""

from __future__ import annotations

from collections.abc import Callable, Sequence

from nicegui import ui


SERVER_PAGE_SIZE_OPTIONS: tuple[int, ...] = (20, 50, 100, 200)


def page_after_size_change(*, page: int, old_size: int, new_size: int) -> int:
    """按当前页首条记录的位置换算新的页码。"""

    safe_page = max(1, int(page))
    safe_old = max(1, int(old_size))
    safe_new = max(1, int(new_size))
    first_offset = (safe_page - 1) * safe_old
    return first_offset // safe_new + 1


def render_server_pager(
    *,
    total: int,
    page: int,
    page_size: int,
    on_change: Callable[[int], object],
    on_page_size_change: Callable[[int], object] | None = None,
    page_size_options: Sequence[int] = SERVER_PAGE_SIZE_OPTIONS,
) -> None:
    """渲染服务端分页页脚；页码与每页条数变化都由调用方重新查询数据。"""

    safe_total = max(0, int(total))
    safe_size = max(1, int(page_size))
    pages = max(1, (safe_total + safe_size - 1) // safe_size)
    current = min(max(1, int(page)), pages)

    with ui.row().classes("w-full items-center justify-end gap-2 q-pt-sm"):
        if on_page_size_change is not None:
            normalized_options = tuple(
                dict.fromkeys(
                    max(1, int(value))
                    for value in (*page_size_options, safe_size)
                )
            )
            ui.label("每页").classes("text-caption text-grey-6")
            ui.select(
                list(normalized_options),
                value=safe_size,
                on_change=lambda event: on_page_size_change(int(event.value)),
            ).props(
                "dense outlined options-dense"
            ).classes("server-pager-page-size")

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
