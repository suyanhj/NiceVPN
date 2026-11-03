# -*- coding: utf-8 -*-
"""服务端分页的共享页脚，视觉与 AG Grid 原生分页保持一致。"""

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
    """渲染统一服务端分页页脚；样式、信息密度与 AG Grid 原生分页对齐。"""

    safe_total = max(0, int(total))
    safe_size = max(1, int(page_size))
    pages = max(1, (safe_total + safe_size - 1) // safe_size)
    current = min(max(1, int(page)), pages)
    range_start = 0 if safe_total == 0 else (current - 1) * safe_size + 1
    range_end = min(safe_total, current * safe_size)

    with ui.row().classes(
        "vpn-server-pager w-full items-center justify-end no-wrap"
    ):
        if on_page_size_change is not None:
            normalized_options = tuple(
                dict.fromkeys(
                    max(1, int(value))
                    for value in (*page_size_options, safe_size)
                )
            )
            ui.label("每页").classes("vpn-server-pager__label")
            ui.select(
                list(normalized_options),
                value=safe_size,
                on_change=lambda event: on_page_size_change(int(event.value)),
            ).props("dense borderless options-dense").classes(
                "vpn-server-pager__page-size"
            )

        ui.label(
            f"{range_start} 至 {range_end} 共 {safe_total}"
        ).classes("vpn-server-pager__range")

        first = ui.button(
            icon="first_page",
            on_click=lambda: on_change(1),
        ).props("flat round dense aria-label='首页'").classes(
            "vpn-server-pager__button"
        )
        previous = ui.button(
            icon="chevron_left",
            on_click=lambda: on_change(current - 1),
        ).props("flat round dense aria-label='上一页'").classes(
            "vpn-server-pager__button"
        )

        ui.label(f"第{current}共{pages}").classes("vpn-server-pager__page")

        following = ui.button(
            icon="chevron_right",
            on_click=lambda: on_change(current + 1),
        ).props("flat round dense aria-label='下一页'").classes(
            "vpn-server-pager__button"
        )
        last = ui.button(
            icon="last_page",
            on_click=lambda: on_change(pages),
        ).props("flat round dense aria-label='末页'").classes(
            "vpn-server-pager__button"
        )

        if current <= 1:
            first.disable()
            previous.disable()
        if current >= pages:
            following.disable()
            last.disable()
