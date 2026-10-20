# -*- coding: utf-8 -*-
"""记录界面改造前各页面在空数据环境下的构建基线。"""

import asyncio
from collections.abc import Callable

import pytest
from box import Box
from nicegui.testing import user_simulation

from app.services.cert.cert_service import CertService
from app.services.firewall.rule_service import FirewallRuleService
from app.services.group.crud import GroupService
from app.services.monitor.service_monitor import InstanceStatus, ServiceMonitor
from app.services.peer_instance.service import PeerService
from app.services.user.crud import UserService
from app.services.user.device_bind import DeviceBindingService
from app.ui.pages.certs import CertsPage
from app.ui.pages.dashboard import DashboardPage
from app.ui.pages.firewall import FirewallPage
from app.ui.pages.groups import GroupsPage
from app.ui.pages.init_page import InitPage
from app.ui.pages.peers import PeersPage
from app.ui.pages.services import ServicesPage
from app.ui.pages.settings import SettingsPage
from app.ui.pages.users import UsersPage
from app.ui.components.app_shell import render_app_shell


_PAGE_BUILDERS: list[tuple[str, Callable[[], None]]] = [
    ("dashboard", lambda: DashboardPage().render()),
    ("users", lambda: UsersPage().render()),
    ("groups", lambda: GroupsPage().render()),
    ("peers", lambda: PeersPage().render()),
    ("firewall", lambda: FirewallPage().render()),
    ("services", lambda: ServicesPage().render()),
    ("settings", lambda: SettingsPage().render()),
    ("init", lambda: InitPage().render()),
]

_MANAGEMENT_PAGE_BUILDERS: list[tuple[str, str, str, Callable[[], None]]] = [
    ("dashboard", "仪表盘", "/", lambda: DashboardPage().render()),
    ("users", "用户管理", "/users", lambda: UsersPage().render()),
    ("groups", "组管理", "/groups", lambda: GroupsPage().render()),
    ("peers", "对端站点", "/peers", lambda: PeersPage().render()),
    ("firewall", "防火墙规则", "/firewall", lambda: FirewallPage().render()),
    ("certs", "证书管理", "/certs", lambda: CertsPage().render()),
    ("services", "服务管理", "/services", lambda: ServicesPage().render()),
    ("settings", "系统设置", "/settings", lambda: SettingsPage().render()),
]


def _open_page(builder: Callable[[], None]) -> None:
    """在独立 NiceGUI 测试客户端中打开页面。"""

    async def verify_page() -> None:
        async with user_simulation(builder) as user:
            response = await user.open("/")
            assert response.status_code == 200

    asyncio.run(verify_page())


@pytest.mark.parametrize(("page_name", "builder"), _PAGE_BUILDERS)
def test_page_builds_with_current_empty_data(
    page_name: str,
    builder: Callable[[], None],
) -> None:
    """验证当前数据目录为空时页面仍能完成服务端构建。"""
    try:
        _open_page(builder)
    except Exception as exc:
        pytest.fail(f"{page_name} 空数据页面构建失败: {exc}")


@pytest.mark.parametrize("auth_enabled", (False, True))
@pytest.mark.parametrize(
    ("page_name", "page_title", "path", "builder"),
    _MANAGEMENT_PAGE_BUILDERS,
)
def test_management_pages_build_with_auth_disabled_and_enabled_shell(
    page_name: str,
    page_title: str,
    path: str,
    builder: Callable[[], None],
    auth_enabled: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """认证开关不改变各管理页面的服务端构建能力。"""

    monkeypatch.setattr(CertService, "get_expiring", lambda self: [])
    monkeypatch.setattr(CertService, "list_all", lambda self: [])

    def wrapped_builder() -> None:
        render_app_shell(page_title, path, auth_enabled=auth_enabled)
        builder()

    try:
        _open_page(wrapped_builder)
    except Exception as exc:
        pytest.fail(f"{page_name} 认证模式 {auth_enabled} 页面构建失败: {exc}")


def test_certs_page_builds_when_service_returns_empty_data(monkeypatch: pytest.MonkeyPatch) -> None:
    """证书服务返回空集合时，证书页应显示空状态而不是依赖真实 PKI。"""

    monkeypatch.setattr(CertService, "get_expiring", lambda self: [])
    monkeypatch.setattr(CertService, "list_all", lambda self: [])

    _open_page(lambda: CertsPage().render())


def test_data_pages_build_with_representative_records(monkeypatch: pytest.MonkeyPatch) -> None:
    """用最小代表性记录验证数据页不会依赖真实系统文件。"""

    monkeypatch.setattr(
        UserService,
        "list_all",
        lambda self: [
            {
                "username": "alice",
                "group_id": "group-main",
                "status": "active",
                "created_at": "2026-10-20T08:00:00+00:00",
                "cert_serial": "0123456789ABCDEF",
            }
        ],
    )
    monkeypatch.setattr(UserService, "list_ccd_virtual_ipv4_by_username", lambda self: {"alice": "10.8.0.10"})
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
                "user_count": 1,
            }
        ],
    )
    monkeypatch.setattr(
        PeerService,
        "list_all",
        lambda self: [
            {
                "id": "peer-east",
                "name": "华东节点",
                "bound_username": "alice",
                "lan_cidrs": ["192.168.10.0/24"],
                "mesh_route_visible_group_ids": ["group-main"],
            }
        ],
    )
    monkeypatch.setattr(
        FirewallRuleService,
        "list_unified_flat",
        lambda self: [
            {
                "id": "rule-001",
                "owner_id": "group-main",
                "source_subnet": "10.8.0.0/24",
                "dest_ip": "192.168.10.10",
                "dest_port": "443",
                "protocol": "tcp",
                "action": "accept",
                "enabled": True,
            }
        ],
    )
    certs = [
        {
            "common_name": "alice",
            "serial": "0123456789ABCDEF",
            "status": "valid",
            "expires_at": "2030-10-20T08:00:00+00:00",
        }
    ]
    monkeypatch.setattr(CertService, "get_expiring", lambda self: [])
    monkeypatch.setattr(CertService, "list_all", lambda self: certs)

    for builder in (
        lambda: UsersPage().render(),
        lambda: GroupsPage().render(),
        lambda: PeersPage().render(),
        lambda: FirewallPage().render(),
        lambda: CertsPage().render(),
    ):
        _open_page(builder)


def test_status_pages_build_with_representative_snapshot(monkeypatch: pytest.MonkeyPatch) -> None:
    """验证仪表盘与服务页可使用同一份代表性实例快照。"""

    sample_config = Box(
        {
            "instances": {
                "server": {"port": 1194, "proto": "udp", "subnet": "10.8.0.0/24"},
            },
            "openvpn_conf_dir": "Z:/path-not-present",
        },
        default_box=True,
        default_box_attr=None,
    )
    statuses = [
        InstanceStatus(
            name="server",
            active=True,
            client_count=3,
            bytes_received=1024,
            bytes_sent=2048,
        )
    ]
    monkeypatch.setattr("app.ui.pages.dashboard.load_config", lambda: sample_config)
    monkeypatch.setattr("app.ui.pages.services.load_config", lambda: sample_config)
    monkeypatch.setattr(ServiceMonitor, "check_all_instances", lambda self, names: statuses)

    _open_page(lambda: DashboardPage().render())
    _open_page(lambda: ServicesPage().render())


def test_data_pages_build_with_partial_or_invalid_optional_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    """验证缺失可选字段和非法日期不会阻断页面构建。"""

    monkeypatch.setattr(UserService, "list_all", lambda self: [{"username": "partial-user"}])
    monkeypatch.setattr(UserService, "list_ccd_virtual_ipv4_by_username", lambda self: {})
    monkeypatch.setattr(DeviceBindingService, "build_user_binding_aux", lambda self: {})
    monkeypatch.setattr(GroupService, "list_all", lambda self: [{"id": "group-partial", "name": "未完整配置"}])
    monkeypatch.setattr(PeerService, "list_all", lambda self: [{"id": "peer-partial"}])
    monkeypatch.setattr(FirewallRuleService, "list_unified_flat", lambda self: [{"id": "rule-partial"}])
    monkeypatch.setattr(CertService, "get_expiring", lambda self: [])
    monkeypatch.setattr(
        CertService,
        "list_all",
        lambda self: [
            {
                "common_name": "invalid-date",
                "serial": "",
                "status": "valid",
                "expires_at": "not-a-date",
            }
        ],
    )

    for builder in (
        lambda: UsersPage().render(),
        lambda: GroupsPage().render(),
        lambda: PeersPage().render(),
        lambda: FirewallPage().render(),
        lambda: CertsPage().render(),
    ):
        _open_page(builder)
