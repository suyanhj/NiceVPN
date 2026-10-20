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


def test_users_page_uses_aggrid_toolbar_bulk_state_and_selection_restore(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """用户页统一到 AG Grid，并用稳定用户名恢复有效选择。"""

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
            await user.should_see("用户管理")
            await user.should_see("新建用户")
            await user.should_see("批量导入")
            await user.should_see("刷新")
            await user.should_see("已选择 1 项")
            await user.should_see("共 2 个用户")

    asyncio.run(verify_page())

    assert page.grid_view is not None
    assert page.grid_view.grid._props["modules"] == ["AllCommunityModule"]
    assert page.grid_view.grid.options["paginationPageSize"] == 50
    assert page.grid_view.grid.options["rowSelection"] == {"mode": "multiRow"}
    assert ":getRowId" in page.grid_view.grid.options
    assert ":onFirstDataRendered" in page.grid_view.grid.options
    assert len(page.grid_view.grid.options["rowData"]) == 2
    assert page.bulk_download_button._props.get("disable") is not True
    assert page.bulk_more_menu.button._props.get("disable") is not True


def test_users_search_distinguishes_no_match_from_true_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """有数据但筛选无结果时显示无匹配状态，而不是“暂无用户”。"""

    page = UsersPage()
    page.user_service = FakeUserService([_user("alice")])
    monkeypatch.setattr(page, "_load_live_sessions", lambda: {})
    monkeypatch.setattr(DeviceBindingService, "build_user_binding_aux", lambda self: {})
    monkeypatch.setattr(GroupService, "list_all", lambda self: [])

    async def verify_page() -> None:
        async with user_simulation(page.render) as user:
            await user.open("/")
            page.search_input.set_value("not-exists")
            page._handle_search()
            await user.should_see("没有匹配结果")
            await user.should_see("没有用户匹配“not-exists”")

    asyncio.run(verify_page())
    assert page.grid_view.summary.text == "匹配 0 / 共 1 个用户"


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
    assert page._row_matches_query(rows[4999], "user-4999")
    assert page._row_matches_query(rows[4999], str(rows[4999]["virtual_ip"]).casefold())
    assert not page._row_matches_query(rows[4999], "definitely-not-found")


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


def test_change_group_uses_refresh_and_reports_virtual_ip(monkeypatch: pytest.MonkeyPatch) -> None:
    """切组成功后保留用户页上下文并刷新 Grid。"""

    page = UsersPage()
    refreshed: list[bool] = []
    messages: list[tuple[str, str]] = []

    class Service:
        def change_group(self, username: str, group_id: str):
            assert username == "alice"
            assert group_id == "group-new"
            return {
                "changed": True,
                "old_virtual_ip": "10.8.0.8",
                "new_virtual_ip": "10.8.1.8",
            }

    page.user_service = Service()
    monkeypatch.setattr(page, "_refresh_user_list", lambda: refreshed.append(True))
    monkeypatch.setattr(
        "app.ui.pages.users.ui.notify",
        lambda message, type="info", **_kwargs: messages.append((str(message), type)),
    )

    assert page._change_group("alice", "group-new") is True
    assert refreshed == [True]
    assert messages[-1] == (
        "已切换 alice 的用户组，虚拟 IP: 10.8.0.8 -> 10.8.1.8",
        "positive",
    )


def test_delete_online_user_disconnects_then_refreshes_without_navigation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """删除已知在线用户前先踢会话，完成后刷新 Grid 而非整页跳转。"""

    page = UsersPage()
    page.all_grid_rows = [{"username": "alice", "_online": True}]
    page.selected_users = {"alice"}
    calls: list[tuple[str, str]] = []
    refreshed: list[bool] = []
    navigated: list[str] = []

    class Service:
        def kick_offline(self, username: str):
            calls.append(("kick", username))
            return True

        def delete(self, username: str):
            calls.append(("delete", username))
            return True

    page.user_service = Service()
    monkeypatch.setattr(page, "_refresh_user_list", lambda: refreshed.append(True))
    monkeypatch.setattr(
        "app.ui.pages.users.ui.notify",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "app.ui.pages.users.ui.navigate.to",
        lambda target: navigated.append(str(target)),
    )

    page._do_delete("alice")

    assert calls == [("kick", "alice"), ("delete", "alice")]
    assert "alice" not in page.selected_users
    assert refreshed == [True]
    assert navigated == []


def test_batch_delete_disconnects_only_known_online_users(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """批量删除只对当前快照中在线用户执行预断开。"""

    page = UsersPage()
    page.all_grid_rows = [
        {"username": "alice", "_online": True},
        {"username": "bob", "_online": False},
    ]
    page.selected_users = {"alice", "bob"}
    calls: list[tuple[str, str]] = []

    class Service:
        def kick_offline(self, username: str):
            calls.append(("kick", username))
            return True

        def delete(self, username: str):
            calls.append(("delete", username))
            return True

    page.user_service = Service()
    monkeypatch.setattr(page, "_refresh_user_list", lambda: None)
    monkeypatch.setattr("app.ui.pages.users.ui.notify", lambda *_args, **_kwargs: None)

    page._batch_delete()

    assert ("kick", "alice") in calls
    assert ("kick", "bob") not in calls
    assert ("delete", "alice") in calls
    assert ("delete", "bob") in calls
    assert page.selected_users == set()
