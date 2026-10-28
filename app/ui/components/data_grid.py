# -*- coding: utf-8 -*-
"""统一 AG Grid Community 数据视图。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Mapping, Sequence

from nicegui import ui


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
) -> DataGrid:
    """构建统一的 Community AG Grid、分页、选择和结果摘要。"""

    if page_size <= 0:
        raise ValueError("分页大小必须大于 0")

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
        "pagination": bool(pagination),
        ":getRowId": f"(params) => String(params.data?.{row_id} ?? '')",
        "animateRows": True,
    }
    if row_drag_managed:
        options["rowDragManaged"] = True
        options["suppressMoveWhenRowDragging"] = False
        # AG Grid emits overIndex for rowDragEnd, but NiceGUI's event bridge
        # only forwards toIndex. Copy the target before the bridge serializes it.
        options[":onRowDragEnd"] = (
            "(event) => { "
            "if (Number.isInteger(event.overIndex)) event.toIndex = event.overIndex; "
            "}"
        )
    if pagination:
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
            ui.label(f"共 {len(normalized_rows)} 条").classes("vpn-data-grid__summary")
            if show_summary
            else None
        )
    return DataGrid(container=container, grid=grid, summary=summary)
