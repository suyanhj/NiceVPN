# -*- coding: utf-8 -*-
"""对端 update：仅当 LAN/中心策略/mesh 可见性等语义变化时才刷 iptables/CCD/mesh"""

import os as _real_os
from unittest.mock import patch

import pytest

from app.services.peer_instance import service as peer_mod
from app.services.peer_instance.service import PeerService


class PeerOsStub:
    """仅占位 ``os.name``，其余委托真实 ``os``，避免改写全局 ``os.name`` 导致 pathlib/pytest 异常。"""

    def __init__(self, plat_name: str):
        object.__setattr__(self, "name", plat_name)

    def __getattr__(self, item: str):
        return getattr(_real_os, item)


def _patch_peer_os(monkeypatch, plat_name: str) -> None:
    monkeypatch.setattr(peer_mod, "os", PeerOsStub(plat_name))


@pytest.fixture()
def isolated_peers(monkeypatch, tmp_path):
    d = tmp_path / "peers"
    d.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(peer_mod, "PEERS_DIR", d)
    yield d


def _mk_row(**kwargs) -> dict:
    base = {
        "id": "p1",
        "name": "site",
        "bound_username": "alice",
        "lan_cidrs": ["10.1.0.0/24"],
        "mesh_route_visible_group_ids": [],
        "center_forward_enabled": True,
        "center_forward_priority": 500_000,
        "center_forward_protocol": "all",
        "center_forward_dest_ip": "",
        "center_forward_dest_port": "",
        "center_forward_rule_description": "",
        "ssh_host": "1.2.3.4",
        "ssh_username": "root",
        "ssh_port": 22,
        "ssh_auth": "none",
        "ssh_password": "",
        "ssh_private_key": "",
        "ssh_private_key_passphrase": "",
        "ssh_openvpn_binary": "",
        "masquerade_on_peer": False,
        "auto_install_on_peer": False,
        "created_at": "t0",
        "updated_at": "t0",
    }
    base.update(kwargs)
    return base


def test_update_ssh_only_skips_mesh_ccd_iptables(monkeypatch, isolated_peers):
    _patch_peer_os(monkeypatch, "nt")
    row = _mk_row(id="p1")
    svc = PeerService()
    with patch.object(svc, "_apply_ccd_and_vpn_forward"), patch.object(svc, "sync_all_mesh_push_routes_in_ccd"):
        svc.create(dict(row))
    with patch("app.services.firewall.rule_service.FirewallRuleService") as m_fw_cls, patch.object(
        svc, "_merge_ccd"
    ) as m_merge, patch.object(svc, "sync_all_mesh_push_routes_in_ccd") as m_mesh:
        svc.update("p1", {"ssh_host": "9.9.9.9"})
        m_fw_cls.assert_not_called()
        m_merge.assert_not_called()
        m_mesh.assert_not_called()


def test_update_mesh_groups_triggers_mesh_only(monkeypatch, isolated_peers):
    row = _mk_row(id="p1", mesh_route_visible_group_ids=["g1"])
    svc = PeerService()
    with patch.object(svc, "_apply_ccd_and_vpn_forward"), patch.object(svc, "sync_all_mesh_push_routes_in_ccd"):
        svc.create(dict(row))

    _patch_peer_os(monkeypatch, "posix")
    with patch("app.services.firewall.rule_service.FirewallRuleService") as m_fw_cls, patch.object(
        svc, "_merge_ccd"
    ) as m_merge, patch.object(svc, "sync_all_mesh_push_routes_in_ccd") as m_mesh:
        svc.update("p1", {"mesh_route_visible_group_ids": ["g2"]})
        m_fw_cls.assert_not_called()
        m_merge.assert_not_called()
        m_mesh.assert_called_once()


def test_update_lan_triggers_all(monkeypatch, isolated_peers):
    row = _mk_row(id="p1")
    svc = PeerService()
    with patch.object(svc, "_apply_ccd_and_vpn_forward"), patch.object(svc, "sync_all_mesh_push_routes_in_ccd"):
        svc.create(dict(row))

    _patch_peer_os(monkeypatch, "posix")
    with patch("app.services.firewall.rule_service.FirewallRuleService") as m_fw_cls, patch.object(
        svc, "_merge_ccd"
    ) as m_merge, patch.object(svc, "sync_all_mesh_push_routes_in_ccd") as m_mesh:
        svc.update("p1", {"lan_cidrs": ["10.99.0.0/24"]})
        m_fw_cls.return_value.refresh_vpn_forward_only.assert_called_once()
        m_merge.assert_called_once()
        m_mesh.assert_called_once()
