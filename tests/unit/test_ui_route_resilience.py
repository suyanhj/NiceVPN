# -*- coding: utf-8 -*-
"""最终集成：主要后台路由的数据源异常隔离测试。"""

from __future__ import annotations

import asyncio

import pytest
from box import Box
from nicegui.testing import user_simulation

from app.services.cert.cert_service import CertService
from app.services.firewall.rule_service import FirewallRuleService
from app.services.group.crud import GroupService
from app.services.peer_instance.service import PeerService
from app.services.user.crud import UserService
from app.ui.pages.certs import CertsPage
from app.ui.pages.dashboard import DashboardPage
from app.ui.pages.firewall import FirewallPage
from app.ui.pages.groups import GroupsPage
from app.ui.pages.peers import PeersPage
from app.ui.pages.services import ServicesPage
from app.ui.pages.settings import SettingsPage
from app.ui.pages.users import UsersPage


def _open(builder, expected_text: str) -> None:
    async def verify() -> None:
        async with user_simulation(builder) as user:
            response = await user.open("/")
            assert response.status_code == 200
            await user.should_see(expected_text)

    asyncio.run(verify())


def test_users_route_survives_primary_list_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        UserService,
        "list_all",
        lambda _self: (_ for _ in ()).throw(RuntimeError("users broken")),
    )
    _open(lambda: UsersPage().render(), "用户列表加载失败")


def test_groups_route_survives_primary_list_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        GroupService,
        "list_all",
        lambda _self: (_ for _ in ()).throw(RuntimeError("groups broken")),
    )
    _open(lambda: GroupsPage().render(), "用户组列表加载失败")


def test_peers_route_survives_primary_list_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        PeerService,
        "list_all",
        lambda _self: (_ for _ in ()).throw(RuntimeError("peers broken")),
    )
    _open(lambda: PeersPage().render(), "对端列表加载失败")


def test_peers_route_survives_auxiliary_group_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        PeerService,
        "list_all",
        lambda _self: [{"id": "peer-1", "name": "站点 A"}],
    )
    monkeypatch.setattr(
        GroupService,
        "list_all",
        lambda _self: (_ for _ in ()).throw(RuntimeError("group index broken")),
    )
    _open(lambda: PeersPage().render(), "对端辅助数据加载失败")


def test_firewall_route_survives_center_source_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        FirewallRuleService,
        "list_unified_flat",
        lambda _self: (_ for _ in ()).throw(RuntimeError("rules broken")),
    )
    _open(lambda: FirewallPage().render(), "中心规则加载失败")


def test_certs_route_survives_single_snapshot_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        CertService,
        "get_expiring",
        lambda _self: (_ for _ in ()).throw(
            AssertionError("证书页不应为临期告警重复读取证书")
        ),
    )
    monkeypatch.setattr(
        CertService,
        "list_all",
        lambda _self: (_ for _ in ()).throw(RuntimeError("certs broken")),
    )

    async def verify() -> None:
        async with user_simulation(lambda: CertsPage().render()) as user:
            response = await user.open("/")
            assert response.status_code == 200
            await user.should_see("证书列表加载失败")
            await user.should_not_see("临期证书检查不可用")

    asyncio.run(verify())


def test_services_route_survives_local_instance_scan_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = Box(
        {
            "instances": {},
            "openvpn_conf_dir": "/tmp/not-used",
        },
        default_box=True,
        default_box_attr=None,
    )
    monkeypatch.setattr(
        "app.ui.pages.services.load_config",
        lambda: config,
    )
    monkeypatch.setattr(
        ServicesPage,
        "_collect_instance_names",
        lambda _self: (_ for _ in ()).throw(RuntimeError("scan broken")),
    )
    _open(lambda: ServicesPage().render(), "实例扫描失败")


def test_settings_route_survives_config_load_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.ui.pages.settings.load_config",
        lambda: (_ for _ in ()).throw(RuntimeError("config broken")),
    )
    _open(lambda: SettingsPage().render(), "系统设置加载失败")


def test_dashboard_route_survives_config_load_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.ui.pages.dashboard.load_config",
        lambda: (_ for _ in ()).throw(RuntimeError("dashboard config broken")),
    )

    async def verify() -> None:
        page = DashboardPage()
        async with user_simulation(page.render) as user:
            response = await user.open("/")
            assert response.status_code == 200
            assert page._snapshot is not None
            assert "config" in page._snapshot.source_errors
            await user.should_see("部分数据不可用")

    asyncio.run(verify())
