# -*- coding: utf-8 -*-
"""统一 AG Grid Community 数据视图。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Mapping, Sequence

from nicegui import ui

from app.ui.components.server_pager import SERVER_PAGE_SIZE_OPTIONS


MULTI_TEXT_FILTER_JS = "window.NiceVpnMultiTextFilter"


def multi_text_filter_column(
    placeholder: str = "输入后按 Enter",
    *,
    filter_value_field: str | None = None,
) -> dict[str, Any]:
    """自由文本列统一使用可追加标签的多值过滤器。"""

    params: dict[str, Any] = {"inputPlaceholder": str(placeholder)}
    if filter_value_field:
        params["filterValueField"] = str(filter_value_field)
    return {
        ":filter": MULTI_TEXT_FILTER_JS,
        "filterParams": params,
    }


AG_GRID_LOCALE_ZH_CN: dict[str, str] = {
    # Text / simple filter
    "filterOoo": "筛选...",
    "equals": "等于",
    "notEqual": "不等于",
    "blank": "为空",
    "notBlank": "不为空",
    "empty": "请选择",
    "contains": "包含",
    "notContains": "不包含",
    "startsWith": "开头是",
    "endsWith": "结尾是",
    "lessThan": "小于",
    "greaterThan": "大于",
    "lessThanOrEqual": "小于或等于",
    "greaterThanOrEqual": "大于或等于",
    "inRange": "范围内",
    "inRangeStart": "从",
    "inRangeEnd": "到",
    "andCondition": "并且",
    "orCondition": "或者",
    "applyFilter": "应用",
    "resetFilter": "重置",
    "clearFilter": "清除",
    "cancelFilter": "取消",

    # Set filter / value picker
    "selectAll": "(全选)",
    "selectAllSearchResults": "(全选搜索结果)",
    "searchOoo": "搜索...",
    "blanks": "(空白)",
    "noMatches": "没有匹配项",

    # Pagination
    "page": "第",
    "more": "更多",
    "to": "至",
    "of": "共",
    "next": "下一页",
    "last": "末页",
    "first": "首页",
    "previous": "上一页",
    "pageSizeSelectorLabel": "每页",

    # General grid text
    "loadingOoo": "加载中...",
    "noRowsToShow": "暂无数据",
    "ariaSearch": "搜索",
    "ariaFilterInput": "筛选输入",
    "ariaFilterColumnsInput": "筛选列",
    "ariaFilterValue": "筛选值",
}


@dataclass(slots=True)
class DataGrid:
    container: Any
    grid: Any
    summary: Any | None

    def set_rows(self, rows: Iterable[Mapping[str, Any]]) -> None:
        """替换行数据并刷新结果摘要。"""

        normalized = [dict(row) for row in rows]
        self.grid.options["rowData"] = normalized
        if self.summary is not None:
            self.summary.text = f"共 {len(normalized)} 条"
        self.grid.update()

    def refresh(self) -> None:
        self.grid.update()

    async def get_selected_rows(self) -> list[dict[str, Any]]:
        rows = await self.grid.get_selected_rows()
        return list(rows or [])


def render_data_grid(
    columns: Sequence[Mapping[str, Any]],
    rows: Iterable[Mapping[str, Any]],
    *,
    row_id: str = "id",
    page_size: int = 20,
    selectable: bool = True,
    selected_ids: Iterable[object] | None = None,
    on_cell_click: Callable[..., Any] | None = None,
    suppress_horizontal_scroll: bool = False,
    external_filter_text: str | None = None,
    pagination: bool = True,
    row_drag_managed: bool = False,
    show_summary: bool = True,
    server_total: int | None = None,
    server_page: int = 1,
    on_server_page_change: Callable[[int], Any] | None = None,
    on_server_page_size_change: Callable[[int], Any] | None = None,
    server_page_size_options: Sequence[int] = SERVER_PAGE_SIZE_OPTIONS,
) -> DataGrid:
    """构建统一的 Community AG Grid、分页、选择和结果摘要。

    server_total 与 on_server_page_change 同时提供时启用服务端分页：
    Grid 只持有当前页 rowData，页码由 Python 驱动，避免浏览器一次加载全量数据。
    """

    if page_size <= 0:
        raise ValueError("分页大小必须大于 0")

    server_pagination = (
        server_total is not None and on_server_page_change is not None
    )
    total_rows = max(0, int(server_total or 0)) if server_pagination else None
    total_pages = (
        max(1, (total_rows + page_size - 1) // page_size)
        if server_pagination
        else 1
    )
    current_page = (
        min(max(1, int(server_page)), total_pages)
        if server_pagination
        else 1
    )

    normalized_rows = [dict(row) for row in rows]
    options: dict[str, Any] = {
        "columnDefs": [dict(column) for column in columns],
        "defaultColDef": {
            "sortable": True,
            "filter": True,
            "resizable": True,
        },
        "rowData": normalized_rows,
        "localeText": dict(AG_GRID_LOCALE_ZH_CN),
        "pagination": bool(pagination and not server_pagination),
        ":getRowId": f"(params) => String(params.data?.{row_id} ?? '')",
        "animateRows": True,
    }
    if row_drag_managed:
        options["rowDragManaged"] = True
        if not selectable:
            # 中心优先级调整模式不使用行选择；此时允许直接按住整行拖动，
            # 行为与旧版排序交互一致，也避免用户必须命中很小的拖拽手柄。
            options["rowDragEntireRow"] = True
        options["suppressMoveWhenRowDragging"] = False
        # Keep the compatibility bridge for NiceGUI's Python-side rowDragEnd
        # handler; the drag interaction itself is handled by AG Grid.
        options[":onRowDragEnd"] = (
            "(event) => { "
            "if (Number.isInteger(event.overIndex)) event.toIndex = event.overIndex; "
            "}"
        )
    if pagination and not server_pagination:
        options["paginationPageSize"] = page_size
    if suppress_horizontal_scroll:
        options["suppressHorizontalScroll"] = True
    if external_filter_text is not None:
        query = str(external_filter_text or "").strip().casefold()
        options[":isExternalFilterPresent"] = f"() => {str(bool(query)).lower()}"
        options[":doesExternalFilterPass"] = (
            "(node) => String(node.data?.search_text ?? '')"
            f".toLocaleLowerCase().includes({json.dumps(query, ensure_ascii=False)})"
        )
    if selectable:
        options["rowSelection"] = {"mode": "multiRow"}
        options["selectionColumnDef"] = {
            "pinned": "left",
            "lockPosition": "left",
            "width": 48,
            "maxWidth": 48,
            "minWidth": 48,
            "sortable": False,
            "filter": False,
            "resizable": False,
            "suppressMovable": True,
        }
        selected = [str(value) for value in (selected_ids or []) if value is not None]
        if selected:
            selected_json = json.dumps(selected, ensure_ascii=False)
            options[":onFirstDataRendered"] = (
                "(params) => {"
                f"const selected = new Set({selected_json});"
                "params.api.forEachNode((node) => "
                "node.setSelected(selected.has(String(node.data?."
                f"{row_id}"
                " ?? ''))));"
                "}"
            )

    with ui.column().classes("vpn-data-grid w-full") as container:
        grid = (
            ui.aggrid(options, theme="quartz", modules="community")
            .classes("vpn-data-grid__grid w-full")
        )
        if on_cell_click is not None:
            grid.on("cellClicked", on_cell_click)
        summary = (
            ui.label(
                f"共 {total_rows if server_pagination else len(normalized_rows)} 条"
            ).classes("vpn-data-grid__summary")
            if show_summary
            else None
        )
        if server_pagination:
            with ui.row().classes(
                "vpn-data-grid__server-pager w-full items-center justify-end gap-2"
            ):
                if on_server_page_size_change is not None:
                    normalized_options = tuple(
                        dict.fromkeys(
                            max(1, int(value))
                            for value in (*server_page_size_options, page_size)
                        )
                    )
                    ui.label("每页").classes("vpn-data-grid__server-page-label")
                    ui.select(
                        list(normalized_options),
                        value=page_size,
                        on_change=lambda event: on_server_page_size_change(
                            int(event.value)
                        ),
                    ).props(
                        "dense outlined options-dense"
                    ).classes("vpn-data-grid__server-page-size")
                previous = ui.button(
                    icon="chevron_left",
                    on_click=lambda: on_server_page_change(current_page - 1),
                ).props("flat round dense aria-label='上一页'")
                ui.label(
                    f"第 {current_page} / {total_pages} 页 · 共 {total_rows} 条"
                ).classes("vpn-data-grid__server-page-label")
                following = ui.button(
                    icon="chevron_right",
                    on_click=lambda: on_server_page_change(current_page + 1),
                ).props("flat round dense aria-label='下一页'")
                if current_page <= 1:
                    previous.disable()
                if current_page >= total_pages:
                    following.disable()
    return DataGrid(container=container, grid=grid, summary=summary)
