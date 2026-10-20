# -*- coding: utf-8 -*-
"""验证当前 NiceGUI 版本提供的后台组件能力。"""

import asyncio

from nicegui import ui
from nicegui.testing import user_simulation

from app.ui.pages.firewall import FirewallPage


def test_aggrid_community_capabilities() -> None:
    """验证 Community 表格的主题、选择、事件、排序和刷新入口。"""
    state: dict[str, object] = {}

    def page() -> None:
        rows = [
            {"id": "alice", "name": "Alice", "enabled": True},
            {"id": "bob", "name": "Bob", "enabled": False},
        ]
        grid = ui.aggrid(
            {
                "columnDefs": [
                    {"field": "name", "sortable": True, "filter": True},
                    {"field": "enabled", "sortable": True},
                ],
                "rowData": rows,
                "rowSelection": {"mode": "multiRow"},
                "pagination": True,
                "paginationPageSize": 20,
            },
            theme="quartz",
            modules="community",
        )
        grid.on("cellClicked", lambda _: None)
        grid.update()
        state["grid"] = grid
        ui.label("AG Grid 能力验证完成")

    async def verify_page() -> None:
        async with user_simulation(page) as user:
            await user.open("/")
            await user.should_see("AG Grid 能力验证完成")

    asyncio.run(verify_page())

    grid = state["grid"]
    assert grid._props["modules"] == ["AllCommunityModule"]
    assert grid.options["theme"] == "quartz"
    assert grid.options["rowSelection"] == {"mode": "multiRow"}
    assert grid.options["columnDefs"][0]["sortable"] is True
    assert grid._event_listeners
    assert callable(grid.get_selected_rows)
    assert callable(grid.run_grid_method)


def test_echart_dark_update_click_and_empty_capabilities() -> None:
    """验证 ECharts 暗色主题、更新、点击和无数据配置。"""
    state: dict[str, object] = {}

    def page() -> None:
        chart = ui.echart(
            {
                "backgroundColor": "transparent",
                "xAxis": {"show": False},
                "yAxis": {"show": False},
                "series": [],
            },
            theme="dark",
            on_point_click=lambda _: None,
        )
        chart.on_click(lambda _: None)
        chart.update()
        state["chart"] = chart
        ui.label("ECharts 能力验证完成")

    async def verify_page() -> None:
        async with user_simulation(page) as user:
            await user.open("/")
            await user.should_see("ECharts 能力验证完成")

    asyncio.run(verify_page())

    chart = state["chart"]
    assert chart._props["theme"] == "dark"
    assert chart.options["backgroundColor"] == "transparent"
    assert chart.options["series"] == []
    assert len(chart._event_listeners) >= 2
    assert callable(chart.run_chart_method)


def test_nicegui_sortable_replaces_manual_sortablejs_loader() -> None:
    """验证 NiceGUI 自带 SortableJS 支持拖拽手柄、动画和结束事件。"""

    state: dict[str, object] = {}

    def page() -> None:
        with ui.column() as container:
            ui.label("第一项")
            ui.label("第二项")
        sortable = container.make_sortable(
            on_end=lambda event: state.update(
                old_index=event.old_index,
                new_index=event.new_index,
            ),
            handle=".drag-handle",
            ghost_class="sortable-ghost",
        )
        state["options"] = sortable.options

    async def verify_page() -> None:
        async with user_simulation(page) as user:
            response = await user.open("/")
            assert response.status_code == 200

    asyncio.run(verify_page())

    assert state["options"] == {
        "animation": 150.0,
        "ghostClass": "sortable-ghost",
        "handle": ".drag-handle",
    }


def test_firewall_sort_order_uses_drag_indices() -> None:
    """验证中心与对端规则都可只凭拖拽索引生成完整新顺序。"""

    assert FirewallPage._move_order(["a", "b", "c"], 0, 2) == ["b", "c", "a"]
    assert FirewallPage._move_order([0, 1, 2], 2, 0) == [2, 0, 1]
    assert FirewallPage._move_order(["a"], 1, 0) == []
