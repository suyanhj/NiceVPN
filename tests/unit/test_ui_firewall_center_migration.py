# -*- coding: utf-8 -*-
"""防火墙中心策略共享外壳、筛选与 AG Grid 测试。"""

from __future__ import annotations

import asyncio

import pytest
from nicegui.testing import user_simulation

from app.services.group.crud import GroupService
from app.ui.pages.firewall import FirewallPage


CENTER_RULES = [
    {
        "id": "rule-accept",
        "_row_kind": "json",
        "_owner_id": "group-dev",
        "owner_type": "group",
        "owner_id": "group-dev",
        "deployment_target": "center",
        "action": "accept",
        "protocol": "tcp",
        "source_subnet": "10.8.0.0/24",
        "dest_ip": "172.16.0.10",
        "dest_port": "443",
        "priority": 10,
        "enabled": True,
        "description": "开发访问 API",
    },
    {
        "id": "rule-drop",
        "_row_kind": "json",
        "_owner_id": "alice",
        "owner_type": "user",
        "owner_id": "alice",
        "deployment_target": "center",
        "action": "drop",
        "protocol": "all",
        "source_ips": ["10.8.0.20"],
        "dest_ip": "",
        "dest_port": "",
        "priority": 20,
        "enabled": False,
        "description": "隔离用户",
    },
    {
        "id": "ce-peer-peer-001",
        "_row_kind": "center_peer",
        "_peer_id": "peer-001",
        "_owner_id": "peer-001",
        "owner_type": "peer",
        "owner_id": "peer-001",
        "deployment_target": "center",
        "action": "accept",
        "protocol": "udp",
        "source_subnet": "192.168.10.0/24",
        "dest_ip": "10.0.0.0/8",
        "dest_port": "53",
        "priority": 30,
        "enabled": True,
        "description": "站点 DNS",
        "_center_tag_line": "nicevpn-center-peer peer=peer-001",
    },
]


class FakeRuleService:
    def __init__(self, rows=None):
        self.rows = list(CENTER_RULES if rows is None else rows)

    def list_unified_flat(self):
        return [dict(row) for row in self.rows]


class FakePeerService:
    def list_all(self):
        return [{"id": "peer-001", "name": "广州站点"}]


def _prepare_page(monkeypatch: pytest.MonkeyPatch, rows=None) -> FirewallPage:
    page = FirewallPage()
    page.rule_service = FakeRuleService(rows)
    page._peer_service = FakePeerService()
    monkeypatch.setattr(
        GroupService,
        "list_all",
        lambda self: [
            {
                "id": "group-dev",
                "name": "开发组",
                "subnet": "10.8.0.0/24",
                "status": "active",
            }
        ],
    )
    return page


def test_center_firewall_page_uses_shared_shell_filters_and_aggrid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = _prepare_page(monkeypatch)

    async def verify() -> None:
        async with user_simulation(page.render) as user:
            response = await user.open("/")
            assert response.status_code == 200
            await user.should_see("规则管理")
            await user.should_see("中心策略")
            await user.should_see("对端远端")
            await user.should_see("新建规则")
            await user.should_see("批量启用")
            await user.should_see("批量停用")
            await user.should_see("批量删除")
            await user.should_see("3 RULES TOTAL")

    asyncio.run(verify())

    assert page._center_grid_view is not None
    options = page._center_grid_view.grid.options
    assert options["pagination"] is True
    assert options["paginationPageSize"] == 50
    assert options["rowSelection"] == {"mode": "multiRow"}
    assert ":getRowId" in options
    assert len(options["rowData"]) == 3

    rows = options["rowData"]
    assert rows[0]["owner_label"] == "开发组"
    assert rows[1]["owner_label"] == "alice"
    assert rows[2]["owner_label"] == "广州站点 (peer-001)"
    assert rows[2]["kind_label"] == "对端·中心放行"


def test_center_firewall_keyword_search_and_no_match_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = _prepare_page(monkeypatch)

    async def verify() -> None:
        async with user_simulation(page.render) as user:
            await user.open("/")
            page._center_search_input.set_value("开发组")
            page._refresh_rules()

            assert page._center_grid_view is not None
            assert len(page._center_grid_view.grid.options["rowData"]) == 1
            assert page._center_grid_view.grid.options["rowData"][0]["id"] == "rule-accept"
            assert page._rules_total_footer.text == "1 MATCHED / 3 TOTAL"

            page._center_search_input.set_value("definitely-not-found")
            page._refresh_rules()
            await user.should_see("没有匹配结果")
            await user.should_see("当前关键词或归属筛选没有匹配规则")

            page._clear_center_filters()
            assert page._center_grid_view is not None
            assert len(page._center_grid_view.grid.options["rowData"]) == 3

    asyncio.run(verify())


def test_center_firewall_owner_filter_includes_peer_virtual_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = _prepare_page(monkeypatch)

    async def verify() -> None:
        async with user_simulation(page.render) as user:
            await user.open("/")
            page._center_owner_filter.set_value("peer-001")
            page._refresh_rules()

            rows = page._center_grid_view.grid.options["rowData"]
            assert [row["id"] for row in rows] == ["ce-peer-peer-001"]
            assert page.current_owner_id == "peer-001"
            assert page._is_unified_list is False

    asyncio.run(verify())


def test_center_firewall_true_empty_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = _prepare_page(monkeypatch, rows=[])

    async def verify() -> None:
        async with user_simulation(page.render) as user:
            await user.open("/")
            await user.should_see("暂无中心规则")
            await user.should_see("新建规则")
            assert page._rules_total_footer.text == "0 RULES TOTAL"

    asyncio.run(verify())


def test_center_grid_columns_keep_priority_owner_flow_and_id() -> None:
    fields = [column["field"] for column in FirewallPage._center_grid_columns()]

    assert fields == [
        "priority",
        "status_label",
        "owner_label",
        "kind_label",
        "protocol",
        "action_label",
        "source",
        "dest",
        "dest_port",
        "description",
        "id",
    ]
    first = FirewallPage._center_grid_columns()[0]
    assert first["pinned"] == "left"
    assert first["checkboxSelection"] is True
    assert first["headerCheckboxSelection"] is True


def test_center_grid_selection_syncs_existing_batch_state() -> None:
    page = FirewallPage()

    class FakeGrid:
        async def get_selected_rows(self):
            return [{"id": "rule-accept"}, {"id": "rule-drop"}]

    page._center_grid_view = FakeGrid()
    asyncio.run(page._on_center_grid_selection_changed())

    assert page.selected_rule_ids == {"rule-accept", "rule-drop"}
