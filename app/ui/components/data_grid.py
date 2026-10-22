# -*- coding: utf-8 -*-
"""统一 AG Grid Community 数据视图。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Mapping, Sequence

from nicegui import ui


@dataclass(slots=True)
class DataGrid:
    container: Any
    grid: Any
    summary: Any

    def set_rows(self, rows: Iterable[Mapping[str, Any]]) -> None:
        """替换行数据并刷新结果摘要。"""

        normalized = [dict(row) for row in rows]
        self.grid.options["rowData"] = normalized
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
        "pagination": True,
        "paginationPageSize": page_size,
        ":getRowId": f"(params) => String(params.data?.{row_id} ?? '')",
        "animateRows": True,
    }
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
        summary = ui.label(f"共 {len(normalized_rows)} 条").classes("vpn-data-grid__summary")
    return DataGrid(container=container, grid=grid, summary=summary)
