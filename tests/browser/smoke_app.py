# -*- coding: utf-8 -*-
"""真实浏览器 smoke app：生产页面 + 内存数据源，不执行系统运维动作。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from box import Box
from nicegui import app, ui

from app.models.config import SystemConfig
import app.core.config as core_config


def _config() -> Box:
    raw = SystemConfig(
        openvpn_bin="/usr/sbin/openvpn",
        easyrsa_dir="/usr/share/easy-rsa/easyrsa",
        server_ip="vpn.example.com",
    ).model_dump()
    raw.update(
        {
            "initialized": True,
            "pki_dir": "/etc/openvpn/pki",
            "global_subnet": "10.224.0.0/16",
            "instances": {
                "server": {
                    "port": 1194,
                    "proto": "udp",
                    "subnet": "10.224.0.0/24",
                }
            },
        }
    )
    return Box(raw, default_box=True, default_box_attr=None)


CONFIG = _config()
core_config.load_config = lambda: CONFIG

from app.services.group.crud import GroupService
from app.services.monitor.service_monitor import InstanceStatus
from app.services.user.device_bind import DeviceBindingService
from app.ui.adapters.dashboard import DashboardSnapshot
from app.ui.components.app_shell import render_app_shell
from app.ui.pages.certs import CertsPage
from app.ui.pages.dashboard import DashboardPage
from app.ui.pages.firewall import FirewallPage
from app.ui.pages.groups import GroupsPage
from app.ui.pages.init_page import InitPage
from app.ui.pages.peers import PeersPage
from app.ui.pages.services import ServicesPage
from app.ui.pages.settings import SettingsPage
from app.ui.pages.users import UsersPage

import app.ui.pages.groups as groups_module
import app.ui.pages.services as services_module
import app.ui.pages.settings as settings_module


groups_module.load_config = lambda: CONFIG
services_module.load_config = lambda: CONFIG
settings_module.load_config = lambda: CONFIG


GROUPS = [
    {
        "id": "group-main",
        "name": "默认用户组",
        "subnet": "10.224.0.0/16",
        "status": "active",
        "user_count": 2,
        "created_at": "2026-10-20T08:00:00+00:00",
    },
    {
        "id": "group-dev",
        "name": "开发组",
        "subnet": "10.224.10.0/24",
        "status": "active",
        "user_count": 1,
        "created_at": "2026-10-20T08:00:00+00:00",
    },
]

USERS = [
    {
        "username": "alice",
        "group_id": "group-dev",
        "status": "active",
        "created_at": "2026-10-20T08:00:00+00:00",
        "cert_serial": "ABCDEF0123456789",
        "ovpn_file_path": "/tmp/alice.ovpn",
    },
    {
        "username": "bob",
        "group_id": "group-main",
        "status": "disabled",
        "created_at": "2026-10-20T08:00:00+00:00",
        "cert_serial": "ABCDEF9876543210",
        "ovpn_file_path": "/tmp/bob.ovpn",
    },
]

PEERS = [
    {
        "id": "peer-001",
        "name": "广州站点",
        "bound_username": "alice",
        "lan_cidrs": ["192.168.10.0/24"],
        "mesh_route_visible_group_ids": ["group-dev"],
        "ssh_host": "203.0.113.8",
        "ssh_username": "root",
        "ssh_port": 22,
        "ssh_password": "configured",
        "ssh_private_key": "",
    }
]

RULES = [
    {
        "id": "rule-001",
        "owner_type": "group",
        "owner_id": "group-dev",
        "deployment_target": "center",
        "action": "accept",
        "protocol": "tcp",
        "source_subnet": "10.224.10.0/24",
        "dest_ip": "172.16.0.10",
        "dest_port": "443",
        "priority": 10,
        "enabled": True,
        "description": "开发访问 API",
    },
    {
        "id": "rule-002",
        "owner_type": "user",
        "owner_id": "bob",
        "deployment_target": "center",
        "action": "drop",
        "protocol": "all",
        "source_ips": ["10.224.0.20"],
        "priority": 20,
        "enabled": False,
        "description": "隔离用户",
    },
]

NOW = datetime.now(timezone.utc).replace(microsecond=0)
CERTS = [
    {
        "common_name": "alice",
        "serial": "ABCDEF0123456789",
        "status": "valid",
        "expires_at": (NOW + timedelta(days=90)).isoformat(),
    },
    {
        "common_name": "bob",
        "serial": "ABCDEF9876543210",
        "status": "valid",
        "expires_at": (NOW + timedelta(days=5)).isoformat(),
    },
]


class FakeUserService:
    def list_all(self):
        return [dict(item) for item in USERS]

    def list_ccd_virtual_ipv4_by_username(self):
        return {"alice": "10.224.10.10", "bob": "10.224.0.20"}

    def get(self, username: str):
        return next(
            (dict(row) for row in USERS if row["username"] == username),
            None,
        )


class FakeGroupService:
    def list_all(self):
        return [dict(item) for item in GROUPS]


class FakePeerService:
    def list_all(self):
        return [dict(item) for item in PEERS]

    def get(self, peer_id: str):
        return dict(PEERS[0]) if peer_id == "peer-001" else None

    def list_bound_usernames(self, *, exclude_peer_id=None):
        return []

    def list_peer_lan_firewall_presets_for_center_form(self):
        return [
            {
                "key": "peer-001:192.168.10.0/24",
                "cidr": "192.168.10.0/24",
                "label": "广州站点 · 192.168.10.0/24",
            }
        ]

    def fetch_remote_peer_filter_chain_snapshot(self, peer_id: str):
        return {
            "peer_id": peer_id,
            "chain": "NICEVPN-PEER",
            "chain_exists": True,
            "chain_rests": [
                "-p tcp -s 10.224.10.0/24 -d 172.16.0.10 --dport 443 -j ACCEPT"
            ],
        }

    def export_peer_manual_markdown(self, _peer_id: str):
        return "# 广州站点\n\n执行示例。\n"

    def export_peer_manual_context(self, _peer_id: str):
        return {
            "overview": {
                "peer_id": "peer-001",
                "bound_username": "alice",
                "global_subnet": "10.224.0.0/16",
                "client_config_path": "/etc/openvpn/client/client.conf",
                "client_log_path": "/var/log/openvpn/client.log",
                "lan_cidrs": ["192.168.10.0/24"],
            },
            "highlights": ["先下载客户端配置，再启动服务。"],
            "commands": ["systemctl enable --now openvpn-client@client"],
            "steps": [
                {
                    "title": "启动客户端",
                    "summary": "启用 systemd 服务并检查状态。",
                    "command": "systemctl enable --now openvpn-client@client",
                }
            ],
        }


class FakeRuleService:
    def __init__(self):
        self.rows = [dict(item) for item in RULES]

    def list_unified_flat(self):
        return [dict(item) for item in self.rows]

    def reorder(self, _owner_id, order):
        by_id = {str(row["id"]): row for row in self.rows}
        if len(order) != len(by_id) or set(order) != set(by_id):
            raise ValueError("测试规则顺序与规则集合不一致")
        self.rows = [by_id[str(rule_id)] for rule_id in order]
        for priority, row in enumerate(self.rows, start=1):
            row["priority"] = priority * 10

    def backup(self):
        return '{"version": 2, "rules_by_owner": {}}'


class FakeCertService:
    def list_all(self):
        return [dict(item) for item in CERTS]

    def get_expiring(self):
        return [dict(CERTS[1])]


class FakeMonitor:
    def check_all_instances(self, _names):
        return [
            InstanceStatus(
                name="server",
                active=True,
                client_count=2,
                bytes_received=1024 * 1024,
                bytes_sent=2 * 1024 * 1024,
            )
        ]


class FakeWizard:
    def __init__(self):
        self.config = {}

    def run_step(self, step, data=None):
        from app.core.init_wizard import StepResult, WizardStep

        if step == WizardStep.DETECT_OPENVPN:
            return StepResult(
                False,
                "未检测到 OpenVPN",
                next_step=WizardStep.INSTALL_OPENVPN,
            )
        return StepResult(False, f"{step.value} pending")


def _shell(title: str, path: str) -> None:
    render_app_shell(title, path, auth_enabled=False)


def _dashboard() -> None:
    _shell("仪表盘", "/")
    page = DashboardPage()
    page._collect_snapshot = lambda: DashboardSnapshot(
        updated_at=NOW,
        instances=FakeMonitor().check_all_instances(["server"]),
        user_count=2,
        group_count=2,
        rule_count=2,
        enabled_rule_count=1,
        cert_min_expiry_days=5,
    )
    page.render()


def _users() -> None:
    _shell("用户管理", "/users")
    page = UsersPage()
    page.user_service = FakeUserService()
    page._load_live_sessions = lambda: {}
    page.render()


def _groups() -> None:
    _shell("组管理", "/groups")
    page = GroupsPage()
    page.group_service = FakeGroupService()
    page.render()


def _peers() -> None:
    _shell("对端站点", "/peers")
    page = PeersPage()
    page.peer_service = FakePeerService()
    page.user_service = FakeUserService()
    page.group_service = FakeGroupService()
    page._has_global_ssh_key = lambda: False
    page.render()


def _peer_manual() -> None:
    _shell("对端部署说明", "/peers/manual")
    page = PeersPage()
    page.peer_service = FakePeerService()
    page.render_manual_page("peer-001")


def _firewall() -> None:
    _shell("防火墙规则", "/firewall")
    page = FirewallPage()
    page.rule_service = FakeRuleService()
    page._peer_service = FakePeerService()
    import app.ui.pages.firewall as firewall_module
    firewall_module.GroupService.list_all = lambda _self: [dict(row) for row in GROUPS]
    firewall_module.UserService.list_all = lambda _self: [dict(row) for row in USERS]
    # Browser smoke 不落盘远端链缓存，保持 nondestructive。
    firewall_module.remote_chain_cache.record_from_fetch = lambda _snap, _peer_id: None
    firewall_module.remote_chain_cache.read_remote_chain_cache = lambda _peer_id: None
    page.render()


def _certs() -> None:
    _shell("证书管理", "/certs")
    page = CertsPage()
    page.cert_service = FakeCertService()
    page.render()


def _services() -> None:
    _shell("服务管理", "/services")
    page = ServicesPage()
    page._monitor = FakeMonitor()
    page._collect_instance_names = lambda: ["server"]
    page.render()


def _settings() -> None:
    _shell("系统设置", "/settings")
    SettingsPage().render()


def _init() -> None:
    import app.ui.pages.init_page as init_module
    from app.ui.theme import setup_theme

    init_module.InitWizard = FakeWizard
    setup_theme()
    page = InitPage()
    page._has_default_group = lambda: False
    page.render()


for route, builder in (
    ("/", _dashboard),
    ("/dashboard", _dashboard),
    ("/users", _users),
    ("/groups", _groups),
    ("/peers", _peers),
    ("/peers/manual", _peer_manual),
    ("/firewall", _firewall),
    ("/certs", _certs),
    ("/services", _services),
    ("/settings", _settings),
    ("/init", _init),
):
    ui.page(route)(builder)


STATIC_DIR = Path(__file__).resolve().parents[2] / "app" / "ui" / "static"
app.add_static_files("/static", STATIC_DIR)


if __name__ in {"__main__", "__mp_main__"}:
    ui.run(show=False, reload=False)
