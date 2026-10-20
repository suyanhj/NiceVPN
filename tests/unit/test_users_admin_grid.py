# -*- coding: utf-8 -*-
"""用户管理新数据视图测试。"""

from __future__ import annotations

import asyncio

from nicegui.testing import user_simulation

from app.ui.pages.users import UsersPage


def _sample_rows() -> list[dict]:
    return [
        {
            "id": "alice",
            "username": "alice",
            "status_label": "在线",
            "group_name": "研发组",
            "virtual_ip": "10.255.0.8",
            "traffic_received": "1.5 KB",
            "traffic_sent": "1.0 MB",
            "connection_duration": "1时30分",
            "real_address": "203.0.113.8:49200",
            "device_label": "Windows / OpenVPN",
            "cert_serial_short": "12345678...90ABCDEF",
            "created_date": "2026-10-01",
        },
        {
            "id": "bob",
            "username": "bob",
            "status_label": "离线 / 已停用",
            "group_name": "默认组",
            "virtual_ip": "10.255.0.9",
            "traffic_received": "—",
            "traffic_sent": "—",
            "connection_duration": "—",
            "real_address": "—",
            "device_label": "—",
            "cert_serial_short": "—",
            "created_date": "2026-10-02",
        },
    ]


def test_user_grid_columns_cover_core_operational_fields() -> None:
    columns = UsersPage._user_grid_columns()
    fields = [column["field"] for column in columns]

    assert fields == [
        "username",
        "status_label",
        "group_name",
        "virtual_ip",
        "traffic_received",
        "traffic_sent",
        "connection_duration",
        "real_address",
        "device_label",
        "cert_serial_short",
        "created_date",
    ]
    assert columns[0]["checkboxSelection"] is True
    assert columns[0]["headerCheckboxSelection"] is True
    assert columns[0]["pinned"] == "left"


def test_users_page_builds_shared_grid_with_representative_rows(monkeypatch) -> None:
    page = UsersPage()
    monkeypatch.setattr(page, "_build_user_rows", _sample_rows)

    async def verify() -> None:
        async with user_simulation(page.render) as user:
            response = await user.open("/")
            assert response.status_code == 200
            await user.should_see("用户管理")
            await user.should_see("新建用户")
            await user.should_see("批量导入")
            await user.should_see("批量下载")
            await user.should_see("共 2 条")

    asyncio.run(verify())

    assert page.data_grid is not None
    options = page.data_grid.grid.options
    assert options["pagination"] is True
    assert options["paginationPageSize"] == 50
    assert options["rowSelection"] == {"mode": "multiRow"}
    assert options["rowData"][0]["virtual_ip"] == "10.255.0.8"
    assert options["rowData"][0]["traffic_sent"] == "1.0 MB"
    assert options["rowData"][0]["connection_duration"] == "1时30分"


def test_users_page_distinguishes_empty_and_no_match_states(monkeypatch) -> None:
    empty_page = UsersPage()
    monkeypatch.setattr(empty_page, "_build_user_rows", lambda: [])

    async def verify_empty() -> None:
        async with user_simulation(empty_page.render) as user:
            response = await user.open("/")
            assert response.status_code == 200
            await user.should_see("暂无用户")
            await user.should_see("创建用户后会显示在这里")

    asyncio.run(verify_empty())

    search_page = UsersPage()
    monkeypatch.setattr(search_page, "_build_user_rows", _sample_rows)

    def filtered_render() -> None:
        search_page.render()
        search_page.search_input.value = "nobody"
        search_page._refresh_user_list()

    async def verify_no_match() -> None:
        async with user_simulation(filtered_render) as user:
            response = await user.open("/")
            assert response.status_code == 200
            await user.should_see("没有匹配结果")
            await user.should_see("清除筛选")

    asyncio.run(verify_no_match())


def test_grid_selection_syncs_existing_batch_operation_state() -> None:
    page = UsersPage()

    class FakeGrid:
        async def get_selected_rows(self):
            return [{"username": "alice"}, {"username": "bob"}]

    class FakeBulkBar:
        count = -1

        def set_selected_count(self, count: int) -> None:
            self.count = count

    page.data_grid = FakeGrid()
    page.bulk_bar = FakeBulkBar()

    asyncio.run(page._on_grid_selection_changed())

    assert page.selected_users == {"alice", "bob"}
    assert page.bulk_bar.count == 2
