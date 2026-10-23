# -*- coding: utf-8 -*-
"""用户管理 AG Grid 迁移、批量操作与规模场景测试。"""

from __future__ import annotations

import asyncio

import pytest
from nicegui.testing import user_simulation

from app.services.group.crud import GroupService
from app.services.user.device_bind import DeviceBindingService
from app.ui.pages.users import UsersPage


class FakeUserService:
    def __init__(self, users: list[dict]):
        self.users = {str(item["username"]): dict(item) for item in users}
        self.toggled: list[str] = []

    def list_all(self):
        return [dict(item) for item in self.users.values()]

    def list_ccd_virtual_ipv4_by_username(self):
        return {
            name: f"10.8.0.{index + 10}"
            for index, name in enumerate(self.users)
        }

    def get(self, username: str):
        item = self.users.get(username)
        return dict(item) if item else None

    def toggle_status(self, username: str):
        item = self.users[username]
        item["status"] = "disabled" if item.get("status") == "active" else "active"
        self.toggled.append(username)
        return item["status"]


def _user(name: str, *, status: str = "active", group_id: str = "group-main") -> dict:
    return {
        "username": name,
        "group_id": group_id,
        "status": status,
        "created_at": "2026-10-20T08:00:00+00:00",
        "cert_serial": f"SERIAL-{name}",
    }


def test_users_page_uses_aggrid_action_bar_and_selection_restore(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """用户页统一到 AG Grid；页级动作与批量动作共享单条操作栏。"""

    page = UsersPage()
    page.user_service = FakeUserService([
        _user("alice"),
        _user("bob", status="disabled"),
    ])
    page.selected_users = {"alice"}

    monkeypatch.setattr(
        page,
        "_load_live_sessions",
        lambda: {
            "alice": {
                "common_name": "alice",
                "virtual_address": "10.8.0.10",
                "real_address": "203.0.113.10:50000",
                "bytes_received": 1024,
                "bytes_sent": 2048,
                "connected_since": "2026-10-20 08:00:00",
            }
        },
    )
    monkeypatch.setattr(DeviceBindingService, "build_user_binding_aux", lambda self: {})
    monkeypatch.setattr(
        GroupService,
        "list_all",
        lambda self: [
            {
                "id": "group-main",
                "name": "默认接入组",
                "subnet": "10.8.0.0/24",
                "status": "active",
            }
        ],
    )

    async def verify_page() -> None:
        async with user_simulation(page.render) as user:
            response = await user.open("/")
            assert response.status_code == 200
            await user.should_see("已选择 1 项")
            await user.should_see("共 2 个用户")

    asyncio.run(verify_page())

    bar_labels = {
        element._props.get("aria-label")
        for element in page.bulk_bar.actions.client.elements.values()
        if getattr(element, "_props", {}).get("aria-label")
    }
    assert {
        "新建用户",
        "批量导入",
        "刷新用户列表",
        "批量下载",
        "批量启用",
        "批量停用",
        "批量踢下线",
        "批量切换组",
        "批量重置绑定",
        "批量删除",
    } <= bar_labels

    assert page.grid_view is not None
    assert page.grid_view.grid._props["modules"] == ["AllCommunityModule"]
    assert page.grid_view.grid.options["paginationPageSize"] == 50
    assert page.grid_view.grid.options["suppressHorizontalScroll"] is True
    assert page.grid_view.grid.options["rowSelection"] == {"mode": "multiRow"}
    assert ":getRowId" in page.grid_view.grid.options
    assert ":onFirstDataRendered" in page.grid_view.grid.options
    assert len(page.grid_view.grid.options["rowData"]) == 2
    assert page.bulk_download_button._props.get("disable") is not True
    assert len(page.bulk_icon_buttons) == 5
    assert all(button._props.get("disable") is not True for button in page.bulk_icon_buttons)


def test_users_grid_columns_follow_certs_v2_table_template() -> None:
    """Users 主表只保留高频字段，并复用共享状态/操作 renderer。"""

    columns = {column["field"]: column for column in UsersPage._user_grid_columns()}

    assert list(columns) == [
        "username",
        "account_status",
        "session_status",
        "virtual_ip",
        "group",
        "traffic",
        "connection",
        "device_source",
        "actions",
    ]

    username = columns["username"]
    assert username["pinned"] == "left"
    assert username["lockPinned"] is True
    assert username["cellClass"] == "vpn-grid-primary-cell"

    account = columns["account_status"]
    session = columns["session_status"]
    assert "vpn-grid-status-pill" in account[":cellRenderer"]
    assert "vpn-grid-status-pill" in session[":cellRenderer"]
    assert account[":filter"] == "window.NiceVpnSetFilter"
    assert session[":filter"] == "window.NiceVpnSetFilter"

    group = columns["group"]
    device = columns["device_source"]
    assert group[":filter"] == "window.NiceVpnSetFilter"
    assert group["filterParams"]["selectAllLabel"] == "(全选)"
    assert group["filterParams"]["searchPlaceholder"] == "搜索用户组..."

    assert device[":filter"] == "window.NiceVpnSetFilter"
    assert device["filterParams"]["filterValueField"] == "device_filter"
    assert device["filterParams"]["selectAllLabel"] == "(全选)"
    assert device["filterParams"]["valueOrder"] == [
        "安卓",
        "iOS",
        "Mac",
        "Windows",
        "Linux",
        "未知",
    ]

    actions = columns["actions"]
    assert actions["pinned"] == "right"
    assert actions["lockPinned"] is True
    assert actions["width"] == 92
    assert actions["resizable"] is False
    assert "vpn-grid-inline-action" in actions[":cellRenderer"]

    for removed in ("cert_serial_display", "source", "device", "created_at"):
        assert removed not in columns


def test_user_grid_row_exposes_field_specific_status_tones_and_device_source() -> None:
    page = UsersPage()

    online = page._build_user_grid_row(
        _user("alice"),
        {
            "virtual_address": "10.8.0.10",
            "real_address": "203.0.113.10:50000",
            "bytes_received": 1024,
            "bytes_sent": 2048,
            "connected_since": "2026-10-20 08:00:00",
        },
        {"device_label": "Mac"},
        "10.8.0.10",
        {"group-main": "默认接入组"},
        live_available=True,
    )
    disabled = page._build_user_grid_row(
        _user("bob", status="disabled"),
        None,
        {},
        "10.8.0.11",
        {"group-main": "默认接入组"},
        live_available=True,
    )

    assert online["account_status_tone"] == "success"
    assert online["session_status_tone"] == "success"
    assert online["device_filter"] == "Mac"
    assert online["device_source"] == "Mac · 203.0.113.10:50000"

    assert disabled["device_filter"] == "未知"
    assert disabled["account_status_tone"] == "warning"
    assert disabled["session_status_tone"] == "unknown"


def test_user_grid_row_builder_handles_5000_records_without_page_card_expansion() -> None:
    """5,000 用户规模只生成轻量行字典，由 AG Grid 负责分页与虚拟化。"""

    page = UsersPage()
    rows = [
        page._build_user_grid_row(
            _user(f"user-{index:04d}"),
            None,
            {},
            f"10.8.{index // 250}.{index % 250 + 1}",
            {"group-main": "默认接入组"},
            live_available=True,
        )
        for index in range(5000)
    ]

    assert len(rows) == 5000
    assert rows[0]["username"] == "user-0000"
    assert rows[-1]["username"] == "user-4999"


def test_batch_enable_summarizes_success_skip_and_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """批量状态操作跳过已经处于目标状态的用户。"""

    service = FakeUserService([
        _user("already-active"),
        _user("disabled-user", status="disabled"),
    ])
    page = UsersPage()
    page.user_service = service
    page.selected_users = {"already-active", "disabled-user"}
    messages: list[tuple[str, str]] = []

    monkeypatch.setattr(
        "app.ui.pages.users.ui.notify",
        lambda message, type="info", **_kwargs: messages.append((str(message), type)),
    )
    monkeypatch.setattr(page, "_refresh_user_list", lambda: None)

    page._batch_set_status(True)

    assert service.toggled == ["disabled-user"]
    assert messages[-1] == ("批量启用完成：成功 1，跳过 1，失败 0", "positive")


def test_progress_dialog_reports_progress_and_final_summary() -> None:
    """后台创建/导入共用的进度对话框可呈现进行中和结果状态。"""

    page = UsersPage()
    state: dict[str, object] = {}

    def builder() -> None:
        refs = page._open_progress_dialog("批量任务", 10)
        page._update_progress_dialog(
            refs,
            done=5,
            total=10,
            current="alice",
            result="已完成 5 项",
        )
        state["refs"] = refs

    async def verify_page() -> None:
        async with user_simulation(builder) as user:
            await user.open("/")
            await user.should_see("批量任务")
            await user.should_see("已处理 5 / 10 · alice")
            await user.should_see("已完成 5 项")
            await user.should_see("关闭")

    asyncio.run(verify_page())

    refs = state["refs"]
    assert refs["progress"].value == pytest.approx(0.5)
    assert refs["close"].visible is True


def test_user_download_link_push_uses_notification_registry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = UsersPage()
    messages: list[tuple[str, str]] = []
    calls: list[tuple[str, str]] = []

    monkeypatch.setattr(
        page,
        "_ensure_one_time_download_url",
        lambda username: f"https://vpn.example/download/{username}",
    )
    monkeypatch.setattr(
        "app.services.notify.send_download_link",
        lambda username, url: calls.append((username, url)) or True,
    )
    monkeypatch.setattr(
        "app.ui.pages.users.ui.notify",
        lambda message, type="info", **_kwargs: messages.append(
            (str(message), type)
        ),
    )

    page._push_download_link_notify("alice")

    assert calls == [
        ("alice", "https://vpn.example/download/alice")
    ]
    assert messages[-1] == (
        "已推送下载链接: alice",
        "positive",
    )

    monkeypatch.setattr(
        "app.services.notify.send_download_link",
        lambda _username, _url: False,
    )
    page._push_download_link_notify("bob")

    assert messages[-1][1] == "negative"
    assert "系统设置 → 通知" in messages[-1][0]


def test_users_v2_visual_contract_uses_shared_table_and_dialog_system() -> None:
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    source = (root / "app" / "ui" / "pages" / "users.py").read_text(encoding="utf-8")
    shell_css = (
        root / "app" / "ui" / "static" / "css" / "shell.css"
    ).read_text(encoding="utf-8")
    renderer_source = (
        root / "app" / "ui" / "components" / "data_grid_renderers.py"
    ).read_text(encoding="utf-8")

    assert "users-page--v2" in source
    assert "users-toolbar" not in source
    assert "users-bulk-bar" in source
    assert "users-action-separator" in source
    assert "users-bar-icon-action" in source
    assert "users-bar-icon-action--create" in source
    assert "more_actions(" not in source
    assert "MenuAction(" not in source
    assert "aria-label='{tooltip}'" in source
    assert "aria-label='新建用户'" in source
    for label in (
        "新建用户",
        "批量导入",
        "刷新用户列表",
        "批量下载",
        "批量启用",
        "批量停用",
        "批量踢下线",
        "批量切换组",
        "批量重置绑定",
        "批量删除",
    ):
        assert f'"{label}"' in source
    assert "search_input" not in source
    assert "_handle_search" not in source
    assert "_clear_search" not in source
    assert "users-data-region" in source
    assert "users-action-dialog" in source
    assert "users-dialog-icon-action" in source
    assert "users-import-dialog" in source
    assert "users-change-group-dialog" in source
    assert "users-config-drawer" in source
    assert "suppress_horizontal_scroll=True" in source

    assert ".users-page--v2 .users-toolbar" not in shell_css
    assert ".users-page--v2 .users-action-separator" in shell_css
    assert ".users-page--v2 .users-bar-icon-action" in shell_css
    assert ".users-page--v2 .users-bar-icon-action--create" in shell_css
    assert ".users-page--v2 .users-data-region" in shell_css
    assert ".users-page--v2 .users-data-region .vpn-data-grid__grid" in shell_css
    assert ".q-dialog .q-card.users-action-dialog" in shell_css
    assert ".users-dialog-icon-action" in shell_css
    assert ".q-dialog .q-card.users-config-drawer" in shell_css

    assert "field + '_tone'" in renderer_source
    set_filter_source = (
        root / "app" / "ui" / "static" / "js" / "ag-grid-set-filter.js"
    ).read_text(encoding="utf-8")
    assert "filterValueField" in set_filter_source
    assert "this.filterValue(params.node)" in set_filter_source
