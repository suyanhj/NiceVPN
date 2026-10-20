# -*- coding: utf-8 -*-
"""防火墙对端策略选择、同步状态、AG Grid 与拉取保护测试。"""

from __future__ import annotations

import asyncio

import pytest
from nicegui import ui
from nicegui.testing import user_simulation

from app.ui.pages.firewall import FirewallPage


PEER = {
    "id": "peer-001",
    "name": "广州站点",
    "ssh_host": "203.0.113.8",
    "ssh_username": "root",
    "ssh_password": "secret",
}

CACHE = {
    "version": 1,
    "peer_id": "peer-001",
    "chain": "VPN_PEER_peer001",
    "chain_exists": True,
    "rows": [
        {
            "rest": "-p tcp -s 10.8.0.0/24 -d 172.16.0.10 --dport 443 -j ACCEPT",
            "enabled": True,
            "priority": 10,
            "description": "allow api",
        },
        {
            "rest": "-s 10.9.0.0/24 -j DROP",
            "enabled": False,
            "priority": 20,
        },
    ],
    "pending_sync": False,
    "last_sync_error": None,
    "last_pulled_at": "2026-10-20T01:00:00Z",
    "last_pushed_at": "2026-10-20T02:00:00Z",
}


class FakePeerService:
    def __init__(self, *, fetch_result=None, fetch_error: Exception | None = None):
        self.fetch_result = fetch_result or {
            "chain": "VPN_PEER_peer001",
            "chain_exists": True,
            "chain_rests": ["-j ACCEPT"],
        }
        self.fetch_error = fetch_error
        self.fetch_calls: list[str] = []

    def list_all(self):
        return [dict(PEER)]

    def get(self, peer_id: str):
        return dict(PEER) if peer_id == "peer-001" else None

    def fetch_remote_peer_filter_chain_snapshot(self, peer_id: str):
        self.fetch_calls.append(peer_id)
        if self.fetch_error:
            raise self.fetch_error
        return dict(self.fetch_result)


def _build_remote_view(page: FirewallPage) -> None:
    page._center_tab_active = False
    page._remote_tab_shell = ui.column().classes("w-full")
    with page._remote_tab_shell:
        page._build_remote_tab_static()
    page._refresh_remote_view()


def test_remote_toolbar_and_aggrid_show_ssh_cache_and_raw_rule(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = FirewallPage()
    page._peer_service = FakePeerService()
    monkeypatch.setattr(
        "app.ui.pages.firewall.peer_row_has_usable_ssh_auth",
        lambda _row: True,
    )
    monkeypatch.setattr(
        "app.ui.pages.firewall.remote_chain_cache.read_remote_chain_cache",
        lambda _peer_id: dict(CACHE),
    )

    async def verify() -> None:
        async with user_simulation(lambda: _build_remote_view(page)) as user:
            response = await user.open("/")
            assert response.status_code == 200
            await user.should_see("对端节点")
            await user.should_see("SSH 可用")
            await user.should_see("已与远端同步")
            await user.should_see("从对端拉取")
            await user.should_see("推送到对端")
            await user.should_see("2 RULES TOTAL")

    asyncio.run(verify())

    assert page._remote_grid_view is not None
    options = page._remote_grid_view.grid.options
    assert options["pagination"] is True
    assert options["paginationPageSize"] == 50
    assert options["rowSelection"] == {"mode": "multiRow"}
    assert ":getRowId" in options
    assert len(options["rowData"]) == 2

    first = options["rowData"][0]
    assert first["id"] == "peer-001:0"
    assert first["protocol"] == "TCP"
    assert first["action_label"] == "ACCEPT"
    assert first["source"] == "10.8.0.0/24"
    assert first["dest"] == "172.16.0.10"
    assert first["dest_port"] == "443"
    assert first["raw_rest"].startswith("-p tcp")
    assert first["description"] == "allow api"

    assert page._remote_pull_button._props.get("disable") is not True
    assert page._remote_push_button._props.get("disable") is not True


def test_remote_toolbar_disables_pull_and_push_without_ssh_auth(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = FirewallPage()
    page._peer_service = FakePeerService()
    monkeypatch.setattr(
        "app.ui.pages.firewall.peer_row_has_usable_ssh_auth",
        lambda _row: False,
    )
    monkeypatch.setattr(
        "app.ui.pages.firewall.remote_chain_cache.read_remote_chain_cache",
        lambda _peer_id: dict(CACHE),
    )

    async def verify() -> None:
        async with user_simulation(lambda: _build_remote_view(page)) as user:
            await user.open("/")
            await user.should_see("SSH 缺少凭据")

    asyncio.run(verify())

    assert page._remote_pull_button._props.get("disable") is True
    assert page._remote_push_button._props.get("disable") is True


def test_remote_no_cache_fetching_error_and_empty_states(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = FirewallPage()
    page._peer_service = FakePeerService()
    monkeypatch.setattr(
        "app.ui.pages.firewall.peer_row_has_usable_ssh_auth",
        lambda _row: True,
    )
    monkeypatch.setattr(
        "app.ui.pages.firewall.remote_chain_cache.read_remote_chain_cache",
        lambda _peer_id: None,
    )

    def build_fetching() -> None:
        page._center_tab_active = False
        page._remote_fetch_running_peer = "peer-001"
        page._remote_tab_shell = ui.column()
        with page._remote_tab_shell:
            page._build_remote_tab_static()
        page._refresh_remote_view()

    async def verify_fetching() -> None:
        async with user_simulation(build_fetching) as user:
            await user.open("/")
            await user.should_see("正在从对端拉取规则")

    asyncio.run(verify_fetching())

    page2 = FirewallPage()
    page2._peer_service = FakePeerService()
    page2._remote_fetch_error_by_peer["peer-001"] = "SSH timeout"
    monkeypatch.setattr(
        "app.ui.pages.firewall.peer_row_has_usable_ssh_auth",
        lambda _row: True,
    )
    monkeypatch.setattr(
        "app.ui.pages.firewall.remote_chain_cache.read_remote_chain_cache",
        lambda _peer_id: None,
    )

    async def verify_error() -> None:
        async with user_simulation(lambda: _build_remote_view(page2)) as user:
            await user.open("/")
            await user.should_see("拉取对端规则失败")
            await user.should_see("SSH timeout")
            await user.should_see("重试")

    asyncio.run(verify_error())


def test_remote_fetch_failure_preserves_existing_cache_and_records_ui_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = FirewallPage()
    page._peer_service = FakePeerService(fetch_error=RuntimeError("network down"))
    page._center_tab_active = False
    page._remote_fetch_generation = 5
    page._remote_fetch_running_peer = "peer-001"
    page._remote_peer_select = type("Select", (), {"value": "peer-001"})()
    messages: list[tuple[str, str]] = []
    recorded: list[object] = []
    refreshed: list[bool] = []

    async def fake_io_bound(func, *args):
        return func(*args)

    monkeypatch.setattr("app.ui.pages.firewall.run.io_bound", fake_io_bound)
    monkeypatch.setattr(
        "app.ui.pages.firewall.remote_chain_cache.record_from_fetch",
        lambda *args, **kwargs: recorded.append((args, kwargs)),
    )
    monkeypatch.setattr(
        page,
        "_notify_for_page",
        lambda message, type="info", **_kwargs: messages.append((str(message), type)),
    )
    monkeypatch.setattr(page, "_refresh_remote_toolbar_state", lambda: None)
    monkeypatch.setattr(page, "_refresh_remote_view", lambda: refreshed.append(True))

    asyncio.run(page._remote_fetch_chain_via_ssh("peer-001", 5))

    assert recorded == []
    assert page._remote_fetch_error_by_peer["peer-001"] == "network down"
    assert page._remote_fetch_running_peer is None
    assert messages[-1] == ("从对端拉取失败: network down", "negative")
    assert refreshed == []


def test_remote_fetch_generation_protects_against_stale_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = FirewallPage()
    service = FakePeerService()
    page._peer_service = service
    page._center_tab_active = False
    page._remote_fetch_generation = 9
    recorded: list[object] = []

    monkeypatch.setattr(
        "app.ui.pages.firewall.remote_chain_cache.record_from_fetch",
        lambda *args, **kwargs: recorded.append((args, kwargs)),
    )

    asyncio.run(page._remote_fetch_chain_via_ssh("peer-001", 8))

    assert service.fetch_calls == []
    assert recorded == []


def test_remote_fetch_success_records_snapshot_and_refreshes_current_peer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = FirewallPage()
    service = FakePeerService()
    page._peer_service = service
    page._center_tab_active = False
    page._remote_fetch_generation = 3
    page._remote_fetch_running_peer = "peer-001"
    page._remote_peer_select = type("Select", (), {"value": "peer-001"})()
    recorded: list[tuple[dict, str]] = []
    refreshed: list[bool] = []

    async def fake_io_bound(func, *args):
        return func(*args)

    monkeypatch.setattr("app.ui.pages.firewall.run.io_bound", fake_io_bound)
    monkeypatch.setattr(
        "app.ui.pages.firewall.remote_chain_cache.record_from_fetch",
        lambda snap, peer_id: recorded.append((dict(snap), peer_id)),
    )
    monkeypatch.setattr(page, "_refresh_remote_toolbar_state", lambda: None)
    monkeypatch.setattr(page, "_refresh_remote_view", lambda: refreshed.append(True))

    asyncio.run(page._remote_fetch_chain_via_ssh("peer-001", 3))

    assert service.fetch_calls == ["peer-001"]
    assert recorded == [(service.fetch_result, "peer-001")]
    assert page._remote_fetch_running_peer is None
    assert "peer-001" not in page._remote_fetch_error_by_peer
    assert refreshed == [True]


def test_remote_grid_selection_maps_rows_back_to_cache_indices() -> None:
    page = FirewallPage()

    class FakeGrid:
        async def get_selected_rows(self):
            return [
                {"id": "peer-001:0", "index": 0},
                {"id": "peer-001:2", "index": 2},
            ]

    page._remote_grid_view = FakeGrid()
    asyncio.run(page._on_remote_grid_selection_changed())

    assert page._selected_remote_indices == {0, 2}


def test_remote_grid_columns_preserve_raw_rest_and_operations() -> None:
    fields = [
        column["field"]
        for column in FirewallPage._remote_grid_columns()
    ]

    assert fields == [
        "row_number",
        "status_label",
        "priority",
        "protocol",
        "action_label",
        "source",
        "dest",
        "dest_port",
        "description",
        "raw_rest",
        "actions",
    ]
    assert FirewallPage._remote_grid_columns()[0]["checkboxSelection"] is True
    assert FirewallPage._remote_grid_columns()[0]["headerCheckboxSelection"] is True
