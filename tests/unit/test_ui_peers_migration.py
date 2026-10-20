# -*- coding: utf-8 -*-
"""对端站点共享页面迁移测试。"""

from __future__ import annotations

import asyncio

import pytest
from nicegui.testing import user_simulation

from app.ui.pages.peers import PeersPage


def _peer(**extra) -> dict:
    data = {
        "id": "peer-1234567890",
        "name": "广州站点",
        "bound_username": "peer-gz",
        "lan_cidrs": ["192.168.10.0/24"],
        "mesh_route_visible_group_ids": ["group-dev"],
        "ssh_host": "203.0.113.8",
        "ssh_username": "root",
        "ssh_port": 22,
        "ssh_password": "secret",
        "ssh_private_key": "",
    }
    data.update(extra)
    return data


class FakePeerService:
    def __init__(self, peers: list[dict]) -> None:
        self.peers = list(peers)
        self.mesh_calls = 0
        self.deleted: list[str] = []

    def list_all(self) -> list[dict]:
        return [dict(item) for item in self.peers]

    def sync_all_mesh_push_routes_in_ccd(self) -> None:
        self.mesh_calls += 1

    def delete(self, peer_id: str) -> None:
        self.deleted.append(peer_id)


class FakeGroupService:
    def list_all(self):
        return [{"id": "group-dev", "name": "开发组"}]


def test_peers_page_uses_shared_header_toolbar_status_and_cards(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = PeersPage()
    page.peer_service = FakePeerService([_peer()])
    page.group_service = FakeGroupService()
    monkeypatch.setattr(page, "_has_global_ssh_key", lambda: False)

    async def verify() -> None:
        async with user_simulation(page.render) as user:
            response = await user.open("/")
            assert response.status_code == 200
            await user.should_see("对端站点")
            await user.should_see("新建对端")
            await user.should_see("刷新 Mesh 路由")
            await user.should_see("广州站点")
            await user.should_see("可远程运维")
            await user.should_see("peer-gz")
            await user.should_see("192.168.10.0/24")
            await user.should_see("Mesh: 开发组")
            await user.should_see("探测/安装")
            await user.should_see("推送配置")
            await user.should_see("共 1 个")

    asyncio.run(verify())


def test_peers_page_disables_remote_actions_when_credentials_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = PeersPage()
    page.peer_service = FakePeerService([_peer(ssh_password="", ssh_private_key="")])
    page.group_service = FakeGroupService()
    monkeypatch.setattr(page, "_has_global_ssh_key", lambda: False)

    async def verify() -> None:
        async with user_simulation(page.render) as user:
            await user.open("/")
            await user.should_see("缺少凭据")

    asyncio.run(verify())

    buttons = [
        element
        for element in page.list_container.client.elements.values()
        if getattr(element, "text", None) in {"探测/安装", "推送配置"}
    ]
    assert len(buttons) == 2
    assert all(button._props.get("disable") is True for button in buttons)


def test_peers_page_empty_and_error_states(monkeypatch: pytest.MonkeyPatch) -> None:
    empty_page = PeersPage()
    empty_page.peer_service = FakePeerService([])
    empty_page.group_service = FakeGroupService()
    monkeypatch.setattr(empty_page, "_has_global_ssh_key", lambda: False)

    async def verify_empty() -> None:
        async with user_simulation(empty_page.render) as user:
            await user.open("/")
            await user.should_see("暂无对端站点")
            await user.should_see("创建对端后")

    asyncio.run(verify_empty())

    error_page = PeersPage()

    class BrokenService:
        def list_all(self):
            raise RuntimeError("peer data broken")

    error_page.peer_service = BrokenService()
    error_page.group_service = FakeGroupService()

    async def verify_error() -> None:
        async with user_simulation(error_page.render) as user:
            await user.open("/")
            await user.should_see("对端列表加载失败")
            await user.should_see("peer data broken")
            await user.should_see("重试")

    asyncio.run(verify_error())


def test_mesh_refresh_has_duplicate_click_guard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = PeersPage()
    service = FakePeerService([])
    page.peer_service = service
    messages: list[str] = []
    monkeypatch.setattr(
        "app.ui.pages.peers.ui.notify",
        lambda message, **_kwargs: messages.append(str(message)),
    )

    page._mesh_sync_running = True
    page._sync_mesh_ccd_only()
    assert service.mesh_calls == 0
    assert messages == ["Mesh 路由正在刷新，请稍候"]

    page._mesh_sync_running = False
    page._sync_mesh_ccd_only()
    assert service.mesh_calls == 1
    assert messages[-1] == "已重写各用户 CCD 中的 Mesh 路由"


def test_peer_delete_uses_impact_confirmation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = PeersPage()
    captured: dict[str, object] = {}

    monkeypatch.setattr(
        "app.ui.pages.peers.confirm_dialog.show",
        lambda *args, **kwargs: captured.update(kwargs),
    )

    page._confirm_delete(_peer())

    assert captured["title"] == "删除对端"
    assert captured["danger_level"] == "danger"
    assert captured["object_count"] == 1
    assert "iptables" in str(captured["impact"])
    assert "CCD" in str(captured["impact"])
