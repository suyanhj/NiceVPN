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
        self.apply_calls: list[tuple[str, list[str]]] = []

    def list_all(self):
        return [dict(PEER)]

    def get(self, peer_id: str):
        return dict(PEER) if peer_id == "peer-001" else None

    def fetch_remote_peer_filter_chain_snapshot(self, peer_id: str):
        self.fetch_calls.append(peer_id)
        if self.fetch_error:
            raise self.fetch_error
        return dict(self.fetch_result)

    def apply_remote_peer_filter_chain_rests(self, peer_id: str, rests: list[str]):
        self.apply_calls.append((peer_id, list(rests)))
        return True

    def list_peer_lan_firewall_presets_for_center_form(self):
        return [
            {
                "key": "peer-001:192.168.10.0/24",
                "cidr": "192.168.10.0/24",
                "label": "广州站点 · 192.168.10.0/24",
            }
        ]


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
    assert options["columnDefs"][0]["rowDrag"] is True
    assert options["rowDragManaged"] is True


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
    assert page._remote_grid_view.grid.options["columnDefs"][0]["rowDrag"] is False
    assert "rowDragManaged" not in page._remote_grid_view.grid.options


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


def test_remote_grid_drag_uses_cache_indices_and_schedules_reorder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = FirewallPage()
    page._remote_reorder_allowed = True
    page._visible_remote_indices = [0, 1, 2]
    captured: list[list[int]] = []

    monkeypatch.setattr(
        page,
        "_do_remote_reorder_async",
        lambda order: captured.append(list(order)),
    )
    monkeypatch.setattr(page, "_schedule_remote_coro", lambda _value: None)
    monkeypatch.setattr(page, "_refresh_remote_view", lambda: None)

    class Event:
        args = {
            "data": {"index": 0},
            "overIndex": 2,
        }

    page._on_remote_grid_row_drag_end(Event())

    assert captured == [[1, 2, 0]]


def test_remote_grid_drag_blocked_when_remote_truth_is_not_safe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = FirewallPage()
    page._remote_reorder_allowed = False
    messages: list[tuple[str, str]] = []
    refreshed: list[bool] = []
    monkeypatch.setattr(
        page,
        "_notify_for_page",
        lambda message, type="info", **_kwargs: messages.append((str(message), type)),
    )
    monkeypatch.setattr(page, "_refresh_remote_view", lambda: refreshed.append(True))

    class Event:
        args = {"data": {"index": 0}, "overIndex": 1}

    page._on_remote_grid_row_drag_end(Event())

    assert "不能调整顺序" in messages[-1][0]
    assert messages[-1][1] == "warning"
    assert refreshed == [True]


def test_remote_create_shared_form_keeps_source_priority_and_enabled_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = FirewallPage()
    page._center_tab_active = False
    page._peer_service = FakePeerService()
    page._remote_peer_select = type("Select", (), {"value": "peer-001"})()

    monkeypatch.setattr(
        "app.ui.pages.firewall.peer_row_has_usable_ssh_auth",
        lambda _row: True,
    )
    monkeypatch.setattr(
        "app.ui.pages.firewall.GroupService.list_all",
        lambda self: [
            {
                "id": "group-dev",
                "name": "开发组",
                "subnet": "10.8.0.0/24",
                "status": "active",
            }
        ],
    )
    monkeypatch.setattr(
        "app.ui.pages.firewall.UserService.list_all",
        lambda self: [{"username": "alice"}],
    )

    async def verify() -> None:
        async with user_simulation(page._show_create_remote_line_dialog) as user:
            await user.open("/")
            await user.should_see("新建对端规则")
            await user.should_see("来源类型")
            await user.should_see("选择组")
            # 默认来源类型为“组”，用户多选控件此时按设计隐藏。
            await user.should_see("源用本组子网")
            await user.should_see("对端内网 CIDR（可选）")
            await user.should_see("源地址")
            await user.should_see("优先级")
            await user.should_see("创建后启用")
            await user.should_see("创建并写回")

    asyncio.run(verify())


def test_insert_remote_rows_respects_priority_and_applies_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = FirewallPage()
    page._center_tab_active = False
    page._remote_peer_select = type("Select", (), {"value": "peer-001"})()
    marked: list[tuple[str, str, bool, list[dict]]] = []
    refreshed: list[bool] = []
    messages: list[tuple[str, str]] = []

    cache = {
        **CACHE,
        "rows": [
            {
                "rest": "-s 10.0.0.0/24 -j ACCEPT",
                "enabled": True,
                "priority": 10,
            },
            {
                "rest": "-s 20.0.0.0/24 -j ACCEPT",
                "enabled": True,
                "priority": 20,
            },
        ],
    }

    monkeypatch.setattr(
        "app.ui.pages.firewall.remote_chain_cache.read_remote_chain_cache",
        lambda _peer_id: dict(cache),
    )
    monkeypatch.setattr(
        "app.ui.pages.firewall.remote_chain_cache.mark_pending_apply",
        lambda peer_id, chain, chain_exists, rows: marked.append(
            (peer_id, chain, chain_exists, [dict(row) for row in rows])
        ),
    )

    async def apply_ok(_peer_id: str) -> bool:
        return True

    monkeypatch.setattr(page, "_apply_remote_cache_to_peer", apply_ok)
    monkeypatch.setattr(page, "_refresh_remote_view", lambda: refreshed.append(True))
    monkeypatch.setattr(
        page,
        "_notify_for_page",
        lambda message, type="info", **_kwargs: messages.append((str(message), type)),
    )

    new_rows = [
        {
            "rest": "-p tcp -s 15.0.0.0/24 --dport 443 -j ACCEPT",
            "enabled": True,
            "priority": 15,
            "description": "inserted",
        }
    ]
    assert asyncio.run(
        page._insert_remote_rows_and_push("peer-001", new_rows, 15)
    ) is True

    assert len(marked) == 1
    persisted = marked[0][3]
    assert [row["priority"] for row in persisted] == [10, 15, 20]
    assert persisted[1]["description"] == "inserted"
    assert refreshed == [True]
    assert messages[-1] == ("已创建并写回对端", "positive")


def test_remote_reorder_failure_preserves_selection_for_rolled_back_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = FirewallPage()
    page._center_tab_active = False
    page._remote_peer_select = type("Select", (), {"value": "peer-001"})()
    page._selected_remote_indices = {0, 1}
    marked: list[list[dict]] = []

    monkeypatch.setattr(
        "app.ui.pages.firewall.remote_chain_cache.read_remote_chain_cache",
        lambda _peer_id: dict(CACHE),
    )
    monkeypatch.setattr(
        "app.ui.pages.firewall.remote_chain_cache.mark_pending_apply",
        lambda _peer_id, _chain, _exists, rows: marked.append(
            [dict(row) for row in rows]
        ),
    )

    async def apply_fail(_peer_id: str) -> bool:
        return False

    monkeypatch.setattr(page, "_apply_remote_cache_to_peer", apply_fail)
    monkeypatch.setattr(page, "_refresh_remote_view", lambda: None)

    asyncio.run(page._do_remote_reorder_async([1, 0]))

    assert [row["priority"] for row in marked[0]] == [20, 10]
    assert page._selected_remote_indices == {0, 1}


def test_remote_delete_failure_preserves_selection_and_success_reindexes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = FirewallPage()
    page._center_tab_active = False
    page._remote_peer_select = type("Select", (), {"value": "peer-001"})()
    page._selected_remote_indices = {0, 1}

    monkeypatch.setattr(
        "app.ui.pages.firewall.remote_chain_cache.read_remote_chain_cache",
        lambda _peer_id: dict(CACHE),
    )
    monkeypatch.setattr(
        "app.ui.pages.firewall.remote_chain_cache.mark_pending_apply",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(page, "_refresh_remote_view", lambda: None)
    monkeypatch.setattr(page, "_notify_for_page", lambda *_args, **_kwargs: None)

    async def apply_fail(_peer_id: str) -> bool:
        return False

    monkeypatch.setattr(page, "_apply_remote_cache_to_peer", apply_fail)
    asyncio.run(page._do_delete_remote_line_async(0))
    assert page._selected_remote_indices == {0, 1}

    async def apply_ok(_peer_id: str) -> bool:
        return True

    monkeypatch.setattr(page, "_apply_remote_cache_to_peer", apply_ok)
    asyncio.run(page._do_delete_remote_line_async(0))
    assert page._selected_remote_indices == {0}


def test_remote_delete_confirmation_explains_immediate_apply_and_rollback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = FirewallPage()
    captured: dict[str, object] = {}
    monkeypatch.setattr(
        "app.ui.pages.firewall.confirm_dialog.show",
        lambda *args, **kwargs: captured.update(kwargs),
    )

    page._confirm_delete_remote_line(1)

    assert captured["danger_level"] == "danger"
    assert captured["object_count"] == 1
    assert "立即通过 SSH" in str(captured["impact"])
    assert "prepush" in str(captured["impact"])


def test_remote_batch_enable_skips_existing_and_applies_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = FirewallPage()
    page._center_tab_active = False
    page._remote_peer_select = type("Select", (), {"value": "peer-001"})()
    page._selected_remote_indices = {0, 1}
    marked: list[list[dict]] = []
    messages: list[tuple[str, str]] = []

    monkeypatch.setattr(
        "app.ui.pages.firewall.remote_chain_cache.read_remote_chain_cache",
        lambda _peer_id: dict(CACHE),
    )
    monkeypatch.setattr(
        "app.ui.pages.firewall.remote_chain_cache.mark_pending_apply",
        lambda _peer_id, _chain, _exists, rows: marked.append(
            [dict(row) for row in rows]
        ),
    )
    monkeypatch.setattr(page, "_refresh_remote_toolbar_state", lambda: None)
    monkeypatch.setattr(page, "_refresh_remote_view", lambda: None)
    monkeypatch.setattr(
        page,
        "_notify_for_page",
        lambda message, type="info", **_kwargs: messages.append((str(message), type)),
    )

    async def apply_ok(_peer_id: str) -> bool:
        return True

    monkeypatch.setattr(page, "_apply_remote_cache_to_peer", apply_ok)

    assert asyncio.run(page._do_batch_remote_set_enabled_async(True)) is True

    assert len(marked) == 1
    assert marked[0][0]["enabled"] is True
    assert marked[0][1]["enabled"] is True
    assert page._selected_remote_indices == set()
    assert messages[-1] == (
        "批量启用完成：成功 1，跳过 1，失败 0",
        "positive",
    )


def test_remote_batch_enable_failure_keeps_selection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = FirewallPage()
    page._center_tab_active = False
    page._remote_peer_select = type("Select", (), {"value": "peer-001"})()
    page._selected_remote_indices = {1}
    messages: list[tuple[str, str]] = []

    monkeypatch.setattr(
        "app.ui.pages.firewall.remote_chain_cache.read_remote_chain_cache",
        lambda _peer_id: dict(CACHE),
    )
    monkeypatch.setattr(
        "app.ui.pages.firewall.remote_chain_cache.mark_pending_apply",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(page, "_refresh_remote_toolbar_state", lambda: None)
    monkeypatch.setattr(page, "_refresh_remote_view", lambda: None)
    monkeypatch.setattr(
        page,
        "_notify_for_page",
        lambda message, type="info", **_kwargs: messages.append((str(message), type)),
    )

    async def apply_fail(_peer_id: str) -> bool:
        return False

    monkeypatch.setattr(page, "_apply_remote_cache_to_peer", apply_fail)

    assert asyncio.run(page._do_batch_remote_set_enabled_async(True)) is False
    assert page._selected_remote_indices == {1}
    assert messages[-1] == (
        "批量启用失败：成功 0，跳过 0，失败 1",
        "negative",
    )


def test_remote_batch_delete_failure_keeps_selection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = FirewallPage()
    page._center_tab_active = False
    page._remote_peer_select = type("Select", (), {"value": "peer-001"})()
    page._selected_remote_indices = {0, 1}

    monkeypatch.setattr(
        "app.ui.pages.firewall.remote_chain_cache.read_remote_chain_cache",
        lambda _peer_id: dict(CACHE),
    )
    monkeypatch.setattr(
        "app.ui.pages.firewall.remote_chain_cache.mark_pending_apply",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(page, "_refresh_remote_toolbar_state", lambda: None)
    monkeypatch.setattr(page, "_refresh_remote_view", lambda: None)
    monkeypatch.setattr(page, "_notify_for_page", lambda *_args, **_kwargs: None)

    async def apply_fail(_peer_id: str) -> bool:
        return False

    monkeypatch.setattr(page, "_apply_remote_cache_to_peer", apply_fail)

    assert asyncio.run(page._do_batch_remote_delete_async()) is False
    assert page._selected_remote_indices == {0, 1}


def test_remote_push_duplicate_guard_and_success_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = FirewallPage()
    page._center_tab_active = False
    page._remote_peer_select = type("Select", (), {"value": "peer-001"})()
    messages: list[tuple[str, str]] = []
    marked: list[bool] = []

    monkeypatch.setattr(
        "app.ui.pages.firewall.remote_chain_cache.read_remote_chain_cache",
        lambda _peer_id: dict(CACHE),
    )
    monkeypatch.setattr(
        "app.ui.pages.firewall.remote_chain_cache.mark_pending_apply",
        lambda *_args, **_kwargs: marked.append(True),
    )
    monkeypatch.setattr(page, "_refresh_remote_toolbar_state", lambda: None)
    monkeypatch.setattr(page, "_refresh_remote_view", lambda: None)
    monkeypatch.setattr(
        page,
        "_notify_for_page",
        lambda message, type="info", **_kwargs: messages.append((str(message), type)),
    )

    page._remote_push_running_peer = "another-peer"
    assert asyncio.run(page._on_remote_push()) is False
    assert marked == []
    assert "请勿重复提交" in messages[-1][0]

    page._remote_push_running_peer = None

    async def apply_ok(_peer_id: str) -> bool:
        assert page._remote_push_running_peer == "peer-001"
        return True

    monkeypatch.setattr(page, "_apply_remote_cache_to_peer", apply_ok)

    assert asyncio.run(page._on_remote_push()) is True
    assert marked == [True]
    assert page._remote_push_running_peer is None
    assert messages[-1] == ("已写回对端", "positive")


def test_remote_push_confirmation_describes_chain_rewrite(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = FirewallPage()
    page._center_tab_active = False
    page._remote_peer_select = type("Select", (), {"value": "peer-001"})()
    captured: dict[str, object] = {}

    monkeypatch.setattr(
        "app.ui.pages.firewall.remote_chain_cache.read_remote_chain_cache",
        lambda _peer_id: dict(CACHE),
    )
    monkeypatch.setattr(
        "app.ui.pages.firewall.confirm_dialog.show",
        lambda *args, **kwargs: captured.update(kwargs),
    )

    page._confirm_remote_push()

    assert captured["object_count"] == 2
    assert captured["danger_level"] == "warning"
    assert "SSH 重写" in str(captured["impact"])
    assert "prepush" in str(captured["impact"])


def test_execute_remote_import_failure_keeps_form_and_selection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = FirewallPage()
    page._center_tab_active = False
    page._remote_peer_select = type("Select", (), {"value": "peer-001"})()
    page._selected_remote_indices = {0}
    monkeypatch.setattr(
        "app.ui.pages.firewall.remote_chain_cache.mark_pending_apply",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(page, "_refresh_remote_toolbar_state", lambda: None)
    monkeypatch.setattr(page, "_refresh_remote_view", lambda: None)

    async def apply_fail(_peer_id: str) -> bool:
        return False

    monkeypatch.setattr(page, "_apply_remote_cache_to_peer", apply_fail)

    class FakeForm:
        busy: list[bool] = []
        closed = False

        def set_busy(self, value: bool):
            self.busy.append(value)

        def close(self):
            self.closed = True

    class Status:
        text = ""

    form = FakeForm()
    status = Status()
    prepared = {
        "peer_id": "peer-001",
        "kind": "iptables",
        "rows": [{"rest": "-j ACCEPT", "enabled": True}],
        "chain": "VPN_PEER_peer001",
        "chain_exists": True,
        "count": 1,
    }

    assert asyncio.run(
        page._execute_remote_import(form, status, prepared)
    ) is False
    assert form.closed is False
    assert form.busy == [True, False]
    assert page._selected_remote_indices == {0}
    assert "导入失败" in status.text


def test_execute_remote_import_success_closes_and_clears_selection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = FirewallPage()
    page._center_tab_active = False
    page._remote_peer_select = type("Select", (), {"value": "peer-001"})()
    page._selected_remote_indices = {0}
    monkeypatch.setattr(
        "app.ui.pages.firewall.remote_chain_cache.mark_pending_apply",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(page, "_refresh_remote_toolbar_state", lambda: None)
    monkeypatch.setattr(page, "_refresh_remote_view", lambda: None)
    monkeypatch.setattr(page, "_notify_for_page", lambda *_args, **_kwargs: None)

    async def apply_ok(_peer_id: str) -> bool:
        return True

    monkeypatch.setattr(page, "_apply_remote_cache_to_peer", apply_ok)

    class FakeForm:
        busy: list[bool] = []
        closed = False

        def set_busy(self, value: bool):
            self.busy.append(value)

        def close(self):
            self.closed = True

    class Status:
        text = ""

    form = FakeForm()
    status = Status()
    prepared = {
        "peer_id": "peer-001",
        "kind": "backup",
        "rows": [{"rest": "-j ACCEPT", "enabled": True}],
        "chain": "VPN_PEER_peer001",
        "chain_exists": True,
        "count": 1,
    }

    assert asyncio.run(
        page._execute_remote_import(form, status, prepared)
    ) is True
    assert form.closed is True
    assert form.busy == [True, False]
    assert page._selected_remote_indices == set()
    assert status.text == "导入完成：成功 1，跳过 0，失败 0"


def test_remote_import_dialog_uses_precheck_shared_form() -> None:
    page = FirewallPage()
    page._center_tab_active = False
    page._remote_peer_select = type("Select", (), {"value": "peer-001"})()

    async def verify() -> None:
        async with user_simulation(page._show_remote_import_dialog) as user:
            await user.open("/")
            await user.should_see("导入对端规则")
            await user.should_see("规则内容")
            await user.should_see("预检查并导入")
            await user.should_see("尚未执行预检查")

    asyncio.run(verify())
