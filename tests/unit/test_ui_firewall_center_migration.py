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
        self.enabled_calls: list[tuple[str, bool]] = []
        self.delete_calls: list[str] = []
        self.reorder_calls: list[tuple[str, list[str]]] = []
        self.create_calls: list[dict] = []
        self.update_calls: list[tuple[str, dict]] = []
        self.restore_calls: list[str] = []
        self.create_many_calls: list[list[dict]] = []

    def list_unified_flat(self):
        return [dict(row) for row in self.rows]

    def list_unified_facets(self):
        owners = []
        seen_owners = set()
        protocols = set()
        actions = set()
        kind_keys = set()
        for row in self.rows:
            owner = (str(row.get("owner_type") or ""), str(row.get("owner_id") or ""))
            if owner not in seen_owners and owner[1]:
                seen_owners.add(owner)
                owners.append({"owner_type": owner[0], "owner_id": owner[1]})
            protocols.add(str(row.get("protocol") or "all").lower())
            actions.add(str(row.get("action") or "accept").lower())
            if row.get("_row_kind") == "center_peer":
                kind_keys.add("center_peer")
            elif str(row.get("deployment_target") or "center") == "peer":
                kind_keys.add("peer_marker")
            else:
                kind_keys.add("center")
        return {
            "owners": owners,
            "protocols": sorted(protocols),
            "actions": sorted(actions),
            "kind_keys": sorted(kind_keys),
        }

    def list_unified_page(
        self,
        *,
        offset=0,
        limit=50,
        search="",
        owner_ids=None,
        enabled_values=None,
        protocols=None,
        actions=None,
        kind_keys=None,
        sort_by="priority",
        sort_desc=False,
        **_kwargs,
    ):
        rows = [dict(row) for row in self.rows]
        query = str(search or "").strip().casefold()
        if query:
            def search_text(row):
                source = str(row.get("source_subnet") or "") or ",".join(
                    str(value) for value in (row.get("source_ips") or [])
                )
                return " ".join([
                    str(row.get("id") or ""),
                    source,
                    str(row.get("dest_ip") or ""),
                    str(row.get("dest_port") or ""),
                ]).casefold()
            rows = [row for row in rows if query in search_text(row)]
        if owner_ids:
            allowed = {str(value) for value in owner_ids}
            rows = [row for row in rows if str(row.get("owner_id") or "") in allowed]
        if enabled_values is not None:
            allowed = {bool(value) for value in enabled_values}
            rows = [row for row in rows if bool(row.get("enabled", True)) in allowed]
        if protocols:
            allowed = {str(value).lower() for value in protocols}
            rows = [row for row in rows if str(row.get("protocol") or "all").lower() in allowed]
        if actions:
            allowed = {str(value).lower() for value in actions}
            rows = [row for row in rows if str(row.get("action") or "accept").lower() in allowed]
        if kind_keys:
            allowed = {str(value) for value in kind_keys}
            def kind_key(row):
                if row.get("_row_kind") == "center_peer":
                    return "center_peer"
                if str(row.get("deployment_target") or "center") == "peer":
                    return "peer_marker"
                return "center"
            rows = [row for row in rows if kind_key(row) in allowed]
        key_field = {
            "owner_label": "owner_id",
            "kind_label": "deployment_target",
            "status_label": "enabled",
            "action_label": "action",
            "source": "source_subnet",
            "dest": "dest_ip",
        }.get(sort_by, sort_by)
        rows.sort(
            key=lambda row: (
                row.get(key_field) if isinstance(row.get(key_field), (int, float, bool))
                else str(row.get(key_field) or "").casefold(),
                str(row.get("id") or ""),
            ),
            reverse=bool(sort_desc),
        )
        total = len(rows)
        return rows[max(0, int(offset)): max(0, int(offset)) + max(1, int(limit))], total

    def set_enabled(self, rule_id: str, enabled: bool):
        self.enabled_calls.append((rule_id, enabled))
        for row in self.rows:
            if str(row.get("id")) == rule_id:
                row["enabled"] = enabled
                return True
        raise ValueError(f"规则不存在: {rule_id}")

    def delete(self, rule_id: str):
        self.delete_calls.append(rule_id)
        before = len(self.rows)
        self.rows = [row for row in self.rows if str(row.get("id")) != rule_id]
        if len(self.rows) == before:
            raise ValueError(f"规则不存在: {rule_id}")
        return True

    def reorder(self, owner_id: str, order: list[str]):
        self.reorder_calls.append((owner_id, list(order)))
        mapping = {str(row["id"]): row for row in self.rows}
        reordered = [mapping[rid] for rid in order if rid in mapping]
        reordered.extend(row for row in self.rows if str(row["id"]) not in set(order))
        self.rows = reordered
        return True

    def create(self, payload: dict):
        self.create_calls.append(dict(payload))
        return dict(payload)

    def update_by_id(self, rule_id: str, payload: dict):
        self.update_calls.append((rule_id, dict(payload)))
        return dict(payload)

    def backup(self):
        return """{
  "version": 2,
  "rules_by_owner": {
    "group-dev": [{"id": "rule-accept"}]
  },
  "peers_center_forward": [{"id": "peer-001"}]
}"""

    def restore(self, text: str):
        self.restore_calls.append(text)
        return True

    def list_all_flat(self):
        return [dict(row) for row in self.rows if row.get("_row_kind") != "center_peer"]

    def create_many(self, payloads: list[dict]):
        copied = [dict(payload) for payload in payloads]
        self.create_many_calls.append(copied)
        return copied


class FakePeerService:
    def __init__(self):
        self.updated: list[tuple[str, dict]] = []

    def list_all(self):
        return [{"id": "peer-001", "name": "广州站点"}]

    def get(self, peer_id: str):
        if peer_id == "peer-001":
            return {"id": "peer-001", "name": "广州站点"}
        return None

    def update(self, peer_id: str, payload: dict, **_kwargs):
        self.updated.append((peer_id, dict(payload)))
        return {"id": peer_id, **payload}

    def list_peer_lan_firewall_presets_for_center_form(self):
        return [
            {
                "key": "peer-001:192.168.10.0/24",
                "cidr": "192.168.10.0/24",
                "label": "广州站点 · 192.168.10.0/24",
            }
        ]


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
            await user.should_see("中心策略")
            await user.should_see("对端远端")
            await user.should_not_see("正在加载中心规则…")
            await user.should_not_see("正在刷新中心规则…")
            await user.should_not_see("点击“调整优先级”进入拖动排序模式")

    asyncio.run(verify())

    assert page._action_bar is not None
    action_labels = {
        element._props.get("aria-label")
        for element in page._action_bar.actions.client.elements.values()
        if getattr(element, "_props", {}).get("aria-label")
    }
    assert {
        "新建规则",
        "导入",
        "导出",
        "批量启用",
        "批量停用",
        "批量删除",
    } <= action_labels

    assert page._center_grid_view is not None
    options = page._center_grid_view.grid.options
    assert options["pagination"] is False
    assert "paginationPageSize" not in options
    assert options["rowSelection"] == {"mode": "multiRow"}
    assert ":getRowId" in options
    assert len(options["rowData"]) == 3

    rows = options["rowData"]
    assert rows[0]["owner_label"] == "开发组"
    assert rows[1]["owner_label"] == "alice"
    assert rows[2]["owner_label"] == "广州站点 (peer-001)"
    assert rows[2]["kind_label"] == "对端·中心放行"
    assert rows[0]["action_label_tone"] == "success"
    assert rows[1]["action_label_tone"] == "failed"


def test_center_firewall_keyword_search_and_no_match_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = _prepare_page(monkeypatch)

    async def verify() -> None:
        async with user_simulation(page.render) as user:
            await user.open("/")
            await user.should_not_see("正在加载中心规则…")
            await user.should_not_see("正在刷新中心规则…")
            page._center_search_input.set_value("172.16.0.10")
            page._on_center_search_changed()
            await user.should_not_see("正在加载中心规则…")
            await user.should_not_see("正在刷新中心规则…")

            assert page._center_grid_view is not None
            assert len(page._center_display_rows) == 1
            assert page._center_display_rows[0]["id"] == "rule-accept"
            assert len(page._center_grid_view.grid.options["rowData"]) == 1
            assert ":isExternalFilterPresent" not in page._center_grid_view.grid.options
            assert page._center_grid_view.summary is None

            page._center_search_input.set_value("443")
            page._on_center_search_changed()
            await user.should_not_see("正在加载中心规则…")
            await user.should_not_see("正在刷新中心规则…")
            assert len(page._center_display_rows) == 1
            assert page._center_display_rows[0]["id"] == "rule-accept"

            page._center_search_input.set_value("开发访问")
            page._on_center_search_changed()
            await user.should_not_see("正在加载中心规则…")
            await user.should_not_see("正在刷新中心规则…")
            assert len(page._center_display_rows) == 0

            page._center_search_input.set_value("definitely-not-found")
            page._on_center_search_changed()
            await user.should_see("没有匹配结果")
            await user.should_see("当前搜索或列筛选没有匹配规则")

            page._clear_center_filters()
            await user.should_not_see("正在加载中心规则…")
            await user.should_not_see("正在刷新中心规则…")
            assert page._center_grid_view is not None
            assert len(page._center_grid_view.grid.options["rowData"]) == 3

    asyncio.run(verify())


def test_center_firewall_classification_filters_are_searchable_multiselects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = _prepare_page(monkeypatch)
    columns = {
        column["field"]: column
        for column in FirewallPage._center_grid_columns()
    }

    for field in (
        "status_label",
        "owner_label",
        "kind_label",
        "protocol",
        "action_label",
    ):
        column = columns[field]
        assert column[":filter"] == "window.NiceVpnSetFilter"
        assert column["filterParams"]["selectAllLabel"] == "(全选)"
        assert column["filterParams"]["showValuesFromOtherFilters"] is False
        assert column["filterParams"]["searchPlaceholder"]

    for field in ("source", "dest", "dest_port", "description", "id"):
        assert columns[field][":filter"] == "window.NiceVpnMultiTextFilter"
        assert columns[field]["filterParams"]["inputPlaceholder"]

    assert "checkboxSelection" not in columns["priority"]
    assert "headerCheckboxSelection" not in columns["priority"]
    assert page._center_filter_model == {}


def test_center_firewall_true_empty_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = _prepare_page(monkeypatch, rows=[])

    async def verify() -> None:
        async with user_simulation(page.render) as user:
            await user.open("/")
            await user.should_not_see("正在加载中心规则…")
            await user.should_not_see("正在刷新中心规则…")
            await user.should_see("暂无中心规则")
            await user.should_see("新建规则")

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
        "actions",
    ]
    first = FirewallPage._center_grid_columns()[0]
    assert first["pinned"] == "left"
    assert first["filter"] is False
    assert "checkboxSelection" not in first
    assert "headerCheckboxSelection" not in first
    assert FirewallPage._center_grid_columns(can_reorder=True)[0]["rowDrag"] is True
    assert FirewallPage._center_grid_columns(can_reorder=False)[0]["rowDrag"] is False


def test_center_priority_reorder_mode_shows_full_order_without_pagination(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = _prepare_page(monkeypatch)
    expected = ["rule-drop", "ce-peer-peer-001", "rule-accept"]
    page._center_reorder_mode = True
    page._center_reorder_original_order = [
        "rule-accept",
        "rule-drop",
        "ce-peer-peer-001",
    ]
    page._pending_center_order = list(expected)

    async def verify() -> None:
        async with user_simulation(page.render) as user:
            response = await user.open("/")
            assert response.status_code == 200
            await user.should_not_see("正在加载中心规则…")
            await user.should_not_see("正在刷新中心规则…")
            assert page._center_grid_view is not None
            options = page._center_grid_view.grid.options
            assert options["pagination"] is False
            assert "paginationPageSize" not in options
            assert "rowSelection" not in options
            assert options["rowDragManaged"] is True
            assert options["suppressMoveWhenRowDragging"] is False
            assert options["columnDefs"][0]["rowDrag"] is True
            assert ":rowDrag" not in options["columnDefs"][0]
            assert page._center_grid_view.summary is None
            assert [row["id"] for row in options["rowData"]] == expected

    asyncio.run(verify())


def test_center_grid_selection_syncs_existing_batch_state() -> None:
    page = FirewallPage()

    class FakeGrid:
        async def get_selected_rows(self):
            return [{"id": "rule-accept"}, {"id": "rule-drop"}]

    page._center_grid_view = FakeGrid()
    asyncio.run(page._on_center_grid_selection_changed())

    assert page.selected_rule_ids == {"rule-accept", "rule-drop"}


def test_center_grid_row_drag_is_only_available_in_explicit_reorder_mode() -> None:
    normal = FirewallPage._center_grid_columns()
    reorder = FirewallPage._center_grid_columns(
        can_reorder=True,
        reorder_mode=True,
    )

    assert normal[0]["rowDrag"] is False
    assert reorder[0]["rowDrag"] is True
    assert ":rowDrag" not in reorder[0]
    assert all(column["sortable"] is False for column in reorder)
    assert all(column["filter"] is False for column in reorder)
    assert reorder[-1]["hide"] is True


def test_center_grid_drag_previews_order_until_explicit_save(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = FirewallPage()
    service = FakeRuleService()
    page.rule_service = service
    page._center_reorder_mode = True
    page._center_reorder_allowed = True
    page._center_reorder_original_order = [
        "rule-accept",
        "rule-drop",
        "ce-peer-peer-001",
    ]
    page._pending_center_order = list(page._center_reorder_original_order)
    page._visible_center_rule_ids = list(page._pending_center_order)
    page._center_all_rows = [dict(row) for row in CENTER_RULES]
    monkeypatch.setattr(page, "_refresh_rules", lambda: None)
    monkeypatch.setattr(page, "_notify_for_page", lambda *_args, **_kwargs: None)

    class Event:
        args = {
            "rowId": "rule-accept",
            "rowIndex": 0,
            "toIndex": 2,
        }

    page._on_center_grid_row_drag_end(Event())

    expected = ["rule-drop", "ce-peer-peer-001", "rule-accept"]
    assert service.reorder_calls == []
    assert page._pending_center_order == expected

    page._save_center_reorder()
    assert service.reorder_calls == [("", expected)]
    assert page._center_reorder_mode is False



def test_center_grid_drag_to_bottom_moves_rule_to_last_position(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = FirewallPage()
    page._center_reorder_mode = True
    page._center_reorder_allowed = True
    page._center_reorder_original_order = ["rule-accept", "rule-drop"]
    page._pending_center_order = ["rule-accept", "rule-drop"]
    page._visible_center_rule_ids = list(page._pending_center_order)
    page._center_all_rows = [
        dict(row) for row in CENTER_RULES
        if row["id"] in {"rule-accept", "rule-drop"}
    ]
    monkeypatch.setattr(page, "_notify_for_page", lambda *_args, **_kwargs: None)

    class Event:
        args = {"rowId": "rule-accept", "rowIndex": 0, "toIndex": -1}

    page._on_center_grid_row_drag_end(Event())

    assert page._pending_center_order == ["rule-drop", "rule-accept"]


def test_center_grid_drag_is_blocked_while_filtered(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = FirewallPage()
    page._center_reorder_mode = True
    page._center_reorder_allowed = True
    page._center_filter_model = {"owner_label": {"values": ["开发组"]}}
    messages: list[str] = []
    refreshed: list[bool] = []
    monkeypatch.setattr(
        page,
        "_notify_for_page",
        lambda message, **_kwargs: messages.append(str(message)),
    )
    monkeypatch.setattr(page, "_render_center_rules", lambda: refreshed.append(True))

    class Event:
        args = {"rowId": "rule-accept", "rowIndex": 0, "toIndex": 1}

    page._on_center_grid_row_drag_end(Event())

    assert messages == ["请进入“调整优先级”模式，并确保没有搜索、筛选或表格排序"]
    assert refreshed == [True]


def test_center_batch_enable_summarizes_success_and_skip(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = FirewallPage()
    service = FakeRuleService()
    page.rule_service = service
    page._center_tab_active = True
    page.selected_rule_ids = {"rule-accept", "rule-drop"}
    page._center_raw_by_id = {
        str(row["id"]): dict(row)
        for row in CENTER_RULES
    }
    messages: list[tuple[str, str]] = []

    monkeypatch.setattr(page, "_refresh_rules", lambda: None)
    monkeypatch.setattr(
        page,
        "_notify_for_page",
        lambda message, type="info", **_kwargs: messages.append((str(message), type)),
    )

    page._batch_set_enabled(True)

    assert service.enabled_calls == [("rule-drop", True)]
    assert page.selected_rule_ids == set()
    assert messages[-1] == (
        "批量启用完成：成功 1，跳过 1，失败 0",
        "positive",
    )


def test_center_batch_delete_reports_partial_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = FirewallPage()

    class PartialService(FakeRuleService):
        def delete(self, rule_id: str):
            if rule_id == "rule-drop":
                raise RuntimeError("iptables failed")
            return super().delete(rule_id)

    service = PartialService()
    page.rule_service = service
    page.selected_rule_ids = {"rule-accept", "rule-drop"}
    messages: list[tuple[str, str]] = []
    monkeypatch.setattr(page, "_refresh_rules", lambda: None)
    monkeypatch.setattr(
        page,
        "_notify_for_page",
        lambda message, type="info", **_kwargs: messages.append((str(message), type)),
    )

    page._do_batch_delete_center()

    assert service.delete_calls == ["rule-accept"]
    assert page.selected_rule_ids == set()
    assert messages[-1] == (
        "批量删除完成：成功 1，失败 1",
        "warning",
    )


def test_center_delete_confirmation_describes_peer_virtual_row_impact(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = FirewallPage()
    page._center_raw_by_id = {
        "ce-peer-peer-001": dict(CENTER_RULES[2]),
    }
    captured: dict[str, object] = {}

    monkeypatch.setattr(
        "app.ui.pages.firewall_center_actions.confirm_dialog.show",
        lambda *args, **kwargs: captured.update(kwargs),
    )

    page._confirm_delete("ce-peer-peer-001")

    assert captured["danger_level"] == "danger"
    assert captured["object_count"] == 1
    assert "清空该对端的 LAN CIDR" in str(captured["impact"])


def test_create_center_rule_normalizes_protocol_and_refreshes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = FirewallPage()
    service = FakeRuleService()
    page.rule_service = service
    messages: list[tuple[str, str]] = []
    refreshed: list[bool] = []
    monkeypatch.setattr(
        page,
        "_notify_for_page",
        lambda message, type="info", **_kwargs: messages.append((str(message), type)),
    )
    monkeypatch.setattr(page, "_refresh_rules", lambda: refreshed.append(True))

    payload = {
        "owner_type": "group",
        "owner_id": "group-dev",
        "instance": "server",
        "deployment_target": "center",
        "action": "accept",
        "protocol": "any",
        "source_subnet": "10.8.0.0/24",
        "source_ips": None,
        "dest_ip": "172.16.0.10",
        "dest_port": "443",
        "priority": 10,
        "description": "allow api",
        "enabled": True,
    }

    assert page._create_center_rule(payload) is True
    assert service.create_calls[0]["protocol"] == "all"
    assert refreshed == [True]
    assert messages[-1] == ("规则创建成功", "positive")


def test_create_center_rule_failure_keeps_form_state_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = FirewallPage()

    class BrokenService(FakeRuleService):
        def create(self, payload: dict):
            raise RuntimeError("iptables failed")

    page.rule_service = BrokenService()
    messages: list[tuple[str, str]] = []
    refreshed: list[bool] = []
    monkeypatch.setattr(
        page,
        "_notify_for_page",
        lambda message, type="info", **_kwargs: messages.append((str(message), type)),
    )
    monkeypatch.setattr(page, "_refresh_rules", lambda: refreshed.append(True))

    assert page._create_center_rule({
        "owner_type": "group",
        "owner_id": "group-dev",
        "action": "accept",
        "protocol": "tcp",
        "priority": 10,
    }) is False
    assert refreshed == []
    assert messages[-1] == ("创建失败: iptables failed", "negative")


def test_center_json_edit_success_and_failure_refresh_semantics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = FirewallPage()
    service = FakeRuleService()
    page.rule_service = service
    messages: list[tuple[str, str]] = []
    refreshed: list[bool] = []
    monkeypatch.setattr(
        page,
        "_notify_for_page",
        lambda message, type="info", **_kwargs: messages.append((str(message), type)),
    )
    monkeypatch.setattr(page, "_refresh_rules", lambda: refreshed.append(True))

    original = dict(CENTER_RULES[0])
    assert page._save_center_rule_edit(
        original,
        action="drop",
        protocol="any",
        source_input="10.8.1.0/24",
        dest_ip="172.16.0.20",
        dest_port="80-443",
        description="updated",
        enabled=False,
    ) is True

    rule_id, payload = service.update_calls[-1]
    assert rule_id == "rule-accept"
    assert payload["protocol"] == "all"
    assert payload["source_subnet"] == "10.8.1.0/24"
    assert payload["enabled"] is False
    assert "_row_kind" not in payload
    assert refreshed == [True]

    class BrokenUpdate(FakeRuleService):
        def update_by_id(self, rule_id: str, payload: dict):
            raise RuntimeError("rebuild failed")

    page.rule_service = BrokenUpdate()
    refreshed.clear()
    assert page._save_center_rule_edit(
        original,
        action="accept",
        protocol="tcp",
        source_input="10.8.0.0/24",
        dest_ip=None,
        dest_port=None,
        description=None,
        enabled=True,
    ) is False
    assert refreshed == []
    assert messages[-1] == ("更新失败: rebuild failed", "negative")


def test_center_edit_can_set_a_manual_global_priority(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = FirewallPage()
    service = FakeRuleService()
    page.rule_service = service
    monkeypatch.setattr(page, "_notify_for_page", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(page, "_refresh_rules", lambda: None)

    assert page._save_center_rule_edit(
        dict(CENTER_RULES[0]),
        action="accept",
        protocol="tcp",
        source_input="10.8.0.0/24",
        dest_ip="172.16.0.10",
        dest_port="443",
        description="开发访问 API",
        enabled=True,
        priority=25,
    ) is True

    assert service.update_calls[-1][1]["priority"] == 25


def test_center_peer_edit_updates_peer_fields_and_validates_accept_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = FirewallPage()
    peers = FakePeerService()
    page._peer_service = peers
    messages: list[tuple[str, str]] = []
    refreshed: list[bool] = []
    monkeypatch.setattr(
        page,
        "_notify_for_page",
        lambda message, type="info", **_kwargs: messages.append((str(message), type)),
    )
    monkeypatch.setattr(page, "_refresh_rules", lambda: refreshed.append(True))

    original = dict(CENTER_RULES[2])

    assert page._save_center_rule_edit(
        original,
        action="drop",
        protocol="udp",
        source_input="192.168.10.0/24",
        dest_ip="10.0.0.0/8",
        dest_port="53",
        description="dns",
        enabled=True,
    ) is False
    assert peers.updated == []
    assert messages[-1] == ("对端·中心放行仅支持 ACCEPT", "negative")

    assert page._save_center_rule_edit(
        original,
        action="accept",
        protocol="udp",
        source_input="192.168.10.0/24, 192.168.20.0/24",
        dest_ip="10.0.0.0/8",
        dest_port="53",
        description="dns",
        enabled=False,
    ) is True

    peer_id, payload = peers.updated[-1]
    assert peer_id == "peer-001"
    assert payload["lan_cidrs"] == ["192.168.10.0/24", "192.168.20.0/24"]
    assert payload["center_forward_enabled"] is False
    assert payload["center_forward_protocol"] == "udp"
    assert refreshed == [True]


def test_center_create_and_edit_dialogs_use_shared_form(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = FirewallPage()
    page.rule_service = FakeRuleService()
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
    monkeypatch.setattr(
        "app.ui.pages.firewall_center_actions.UserService.list_all",
        lambda self: [{"username": "alice"}],
    )
    monkeypatch.setattr(
        "app.core.config.load_config",
        lambda: {"instances": {"server": {}}},
    )

    async def verify_create() -> None:
        async with user_simulation(page._show_create_center_dialog) as user:
            await user.open("/")
            await user.should_see("新建中心规则")
            await user.should_see("归属类型")
            await user.should_see("源用本组子网")
            await user.should_see("对端内网 CIDR")
            await user.should_see("创建后启用")
            await user.should_see("创建")

    asyncio.run(verify_create())

    async def verify_edit() -> None:
        async with user_simulation(
            lambda: page._show_edit_dialog(dict(CENTER_RULES[0]))
        ) as user:
            await user.open("/")
            await user.should_see("编辑中心规则")
            await user.should_see("优先级（全局唯一）")
            await user.should_see("保存")

    asyncio.run(verify_edit())


def test_center_export_uses_timestamped_json_filename_and_backup_content(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = FirewallPage()
    service = FakeRuleService()
    page.rule_service = service
    page._center_tab_active = True
    downloads: list[tuple[bytes, str]] = []
    messages: list[tuple[str, str]] = []

    monkeypatch.setattr(
        "app.ui.pages.firewall.ui.download",
        lambda data, filename: downloads.append((data, filename)),
    )
    monkeypatch.setattr(
        page,
        "_notify_for_page",
        lambda message, type="info", **_kwargs: messages.append((str(message), type)),
    )

    page._export_backup()

    assert len(downloads) == 1
    body, filename = downloads[0]
    assert b'"rules_by_owner"' in body
    assert b'"peers_center_forward"' in body
    assert filename.startswith("firewall-rules-backup_")
    assert filename.endswith(".json")
    assert len(filename) == len("firewall-rules-backup_YYYYMMDD_HHMMSS.json")
    assert messages[-1] == (
        "已导出（中心 JSON + 对端中心侧策略）",
        "positive",
    )


def test_center_import_prepare_recognizes_backup_and_counts_items() -> None:
    page = FirewallPage()
    backup = """{
      "version": 2,
      "rules_by_owner": {
        "group-dev": [{"id": "a"}, {"id": "b"}],
        "alice": [{"id": "c"}]
      },
      "peers_center_forward": [{"id": "peer-1"}]
    }"""

    prepared = page._prepare_center_import(
        backup,
        file_mode="json_overwrite",
    )

    assert prepared["kind"] == "backup"
    assert prepared["rule_count"] == 3
    assert prepared["peer_count"] == 1
    assert prepared["count"] == 4


def test_center_import_prepare_simplified_requires_owner_and_builds_payloads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = FirewallPage()
    page._is_unified_list = True
    page.current_owner_id = ""

    with pytest.raises(ValueError, match="归属筛选"):
        page._prepare_center_import(
            "-p tcp -s 10.8.0.0/24 -d 172.16.0.10 --dport 443",
            file_mode="simplified_text",
        )

    page._is_unified_list = False
    page.current_owner_id = "group-dev"
    monkeypatch.setattr(
        "app.ui.pages.firewall_center_imports.resolve_center_owner_type",
        lambda _oid: "group",
    )
    monkeypatch.setattr(
        "app.core.config.load_config",
        lambda: {"instances": {"server": {}}},
    )

    prepared = page._prepare_center_import(
        "-p tcp -s 10.8.0.0/24 -d 172.16.0.10 --dport 443",
        file_mode="simplified_text",
    )

    assert prepared["kind"] == "simplified"
    assert prepared["owner_id"] == "group-dev"
    assert prepared["count"] == 1
    payload = prepared["payloads"][0]
    assert payload["owner_id"] == "group-dev"
    assert payload["source_subnet"] == "10.8.0.0/24"
    assert payload["protocol"] == "tcp"
    assert payload["dest_port"] == "443"


def test_center_import_prepare_reports_simplified_parse_error_with_context() -> None:
    page = FirewallPage()
    page._is_unified_list = False
    page.current_owner_id = "group-dev"

    with pytest.raises(ValueError, match="简写解析失败"):
        page._prepare_center_import(
            "-A VPN_FORWARD -j ACCEPT",
            file_mode="simplified_text",
        )


def test_execute_center_import_success_closes_form_and_refreshes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = FirewallPage()
    service = FakeRuleService()
    page.rule_service = service
    refreshed: list[bool] = []
    messages: list[tuple[str, str]] = []

    class FakeForm:
        busy: list[bool] = []
        closed = False

        def set_busy(self, value: bool):
            self.busy.append(value)

        def close(self):
            self.closed = True

    class FakeStatus:
        text = ""

    form = FakeForm()
    status = FakeStatus()

    async def fake_io_bound(func, *args):
        return func(*args)

    monkeypatch.setattr("app.ui.pages.firewall_center_imports.run.io_bound", fake_io_bound)
    monkeypatch.setattr(page, "_refresh_rules", lambda: refreshed.append(True))
    monkeypatch.setattr(
        page,
        "_notify_for_page",
        lambda message, type="info", **_kwargs: messages.append((str(message), type)),
    )

    prepared = {
        "kind": "backup",
        "text": service.backup(),
        "count": 2,
        "rule_count": 1,
        "peer_count": 1,
    }
    assert asyncio.run(
        page._execute_center_import(form, status, prepared)
    ) is True

    assert service.restore_calls == [prepared["text"]]
    assert form.busy == [True, False]
    assert form.closed is True
    assert refreshed == [True]
    assert status.text == "导入完成：成功 2，跳过 0，失败 0"
    assert messages[-1] == (
        "导入完成：成功 2，跳过 0，失败 0",
        "positive",
    )


def test_execute_center_import_failure_keeps_form_open_and_input_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = FirewallPage()

    class BrokenService(FakeRuleService):
        def restore(self, text: str):
            raise RuntimeError("iptables restore failed")

    page.rule_service = BrokenService()
    refreshed: list[bool] = []
    messages: list[tuple[str, str]] = []

    class FakeForm:
        busy: list[bool] = []
        closed = False

        def set_busy(self, value: bool):
            self.busy.append(value)

        def close(self):
            self.closed = True

    class FakeStatus:
        text = ""

    form = FakeForm()
    status = FakeStatus()

    async def fake_io_bound(func, *args):
        return func(*args)

    monkeypatch.setattr("app.ui.pages.firewall_center_imports.run.io_bound", fake_io_bound)
    monkeypatch.setattr(page, "_refresh_rules", lambda: refreshed.append(True))
    monkeypatch.setattr(
        page,
        "_notify_for_page",
        lambda message, type="info", **_kwargs: messages.append((str(message), type)),
    )

    prepared = {
        "kind": "backup",
        "text": "{}",
        "count": 3,
    }
    assert asyncio.run(
        page._execute_center_import(form, status, prepared)
    ) is False

    assert form.busy == [True, False]
    assert form.closed is False
    assert refreshed == []
    assert "iptables restore failed" in status.text
    assert messages[-1][1] == "negative"
    assert "失败 3" in messages[-1][0]


def test_center_import_dialog_uses_shared_form_and_precheck_copy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = FirewallPage()
    page._center_tab_active = True

    async def verify() -> None:
        async with user_simulation(page._show_center_import_dialog) as user:
            await user.open("/")
            await user.should_see("导入中心规则")
            await user.should_see("规则内容")
            await user.should_see("从文件加载")
            await user.should_see("预检查并导入")
            await user.should_see("尚未执行预检查")

    asyncio.run(verify())


def test_center_server_pager_supports_page_size_changes() -> None:
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    source = (root / "app" / "ui" / "pages" / "firewall_center.py").read_text(
        encoding="utf-8"
    )
    assert "on_server_page_size_change=(" in source
    assert "self._on_center_page_size_change" in source
    assert "page_after_size_change(" in source


def test_firewall_v2_visual_contract_uses_compact_action_and_grid_system() -> None:
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    source = (root / "app" / "ui" / "pages" / "firewall.py").read_text(
        encoding="utf-8"
    )
    center_source = (
        root / "app" / "ui" / "pages" / "firewall_center.py"
    ).read_text(encoding="utf-8")
    center_actions_source = (
        root / "app" / "ui" / "pages" / "firewall_center_actions.py"
    ).read_text(encoding="utf-8")
    center_import_source = (
        root / "app" / "ui" / "pages" / "firewall_center_imports.py"
    ).read_text(encoding="utf-8")
    helper_source = (root / "app" / "ui" / "firewall_helpers.py").read_text(
        encoding="utf-8"
    )
    shell_css = (
        root / "app" / "ui" / "static" / "css" / "shell.css"
    ).read_text(encoding="utf-8")
    legacy_css = (
        root / "app" / "ui" / "static" / "css" / "legacy.css"
    ).read_text(encoding="utf-8")

    assert "firewall-page--v2" in source
    assert "second_line_comment_text," in source
    assert "firewall-tab-shell" in source
    assert "firewall-bulk-bar" in source
    assert "firewall-filter-bar" in source
    assert "window.NiceVpnSetFilter" in helper_source
    assert "搜索 IP / 端口 / 规则 ID" in source
    assert "on_change=self._on_center_search_changed" in source
    assert "async def _load_center_grid_rows_async" in center_source
    assert "self.rule_service.list_unified_page(" in center_source
    assert "self.rule_service.list_unified_facets" in center_source
    assert "firewall-owner-filter" not in source
    assert "firewall-bar-icon-action" in source
    assert "render_page_toolbar(" not in source
    assert "more_actions(" not in source
    assert "ACTION_BUTTON_RENDERER" in helper_source
    assert "STATUS_PILL_RENDERER" in helper_source
    assert "firewall-action-dialog" in center_source
    assert "firewall-rule-drawer" in center_actions_source
    assert "FirewallCenterImportMixin" in source
    assert "def _prepare_center_import(" in center_import_source
    assert "def _show_center_import_dialog(" in center_import_source
    assert "def _prepare_center_import(" not in center_actions_source
    assert "def _show_center_import_dialog(" not in center_actions_source
    assert "firewall-rule-card" not in source

    assert ".firewall-page--v2 .firewall-tab-shell" in shell_css
    assert ".firewall-page--v2 .firewall-bulk-bar" in shell_css
    assert ".firewall-page--v2 .firewall-filter-bar" in shell_css
    assert ".firewall-page--v2 .firewall-center-shell" in shell_css
    assert ".firewall-page--v2 .firewall-remote-control" in shell_css
    assert ".firewall-rule-card" not in legacy_css
    assert ".firewall-search-shell" not in legacy_css
    assert ".q-dialog .q-card.firewall-action-dialog" in shell_css
    assert ".q-dialog .q-card.firewall-rule-drawer" in shell_css
