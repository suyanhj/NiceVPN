# -*- coding: utf-8 -*-
"""对端 ``bound_username`` 全局唯一：同一 VPN 用户只能绑定一个对端实例"""

from unittest.mock import patch

import pytest

from app.services.peer_instance import service as peer_mod
from app.services.peer_instance.service import PeerService


@pytest.fixture()
def isolated_peers_dir(monkeypatch, tmp_path):
    d = tmp_path / "peers"
    d.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(peer_mod, "PEERS_DIR", d)
    yield d


def test_create_second_peer_same_user_raises(isolated_peers_dir):
    svc = PeerService()
    with patch.object(svc, "_apply_ccd_and_vpn_forward"), patch.object(svc, "sync_all_mesh_push_routes_in_ccd"):
        svc.create({"name": "a", "bound_username": "alice", "lan_cidrs": ["10.1.0.0/24"]})
        with pytest.raises(ValueError, match="只能绑定一个"):
            svc.create({"name": "b", "bound_username": "alice", "lan_cidrs": ["10.2.0.0/24"]})


def test_update_keep_same_username_ok(isolated_peers_dir):
    svc = PeerService()
    with patch.object(svc, "_apply_ccd_and_vpn_forward"), patch.object(svc, "sync_all_mesh_push_routes_in_ccd"):
        row = svc.create({"name": "a", "bound_username": "alice", "lan_cidrs": ["10.1.0.0/24"]})
    pid = str(row["id"])
    with patch.object(svc, "_apply_ccd_and_vpn_forward"), patch.object(svc, "sync_all_mesh_push_routes_in_ccd"):
        svc.update(pid, {"name": "a2", "lan_cidrs": ["10.1.0.0/24"], "bound_username": "alice"})


def test_update_conflict_with_other_peer_raises(isolated_peers_dir):
    svc = PeerService()
    with patch.object(svc, "_apply_ccd_and_vpn_forward"), patch.object(svc, "sync_all_mesh_push_routes_in_ccd"):
        r1 = svc.create({"name": "p1", "bound_username": "alice", "lan_cidrs": ["10.1.0.0/24"]})
        svc.create({"name": "p2", "bound_username": "bob", "lan_cidrs": ["10.2.0.0/24"]})
    pid_a = str(r1["id"])
    with patch.object(svc, "_apply_ccd_and_vpn_forward"), patch.object(svc, "sync_all_mesh_push_routes_in_ccd"):
        with pytest.raises(ValueError, match="只能绑定一个"):
            svc.update(pid_a, {"bound_username": "bob"})


def test_list_bound_usernames_exclude_peer(isolated_peers_dir):
    svc = PeerService()
    with patch.object(svc, "_apply_ccd_and_vpn_forward"), patch.object(svc, "sync_all_mesh_push_routes_in_ccd"):
        r1 = svc.create({"name": "p1", "bound_username": "alice", "lan_cidrs": ["10.1.0.0/24"]})
        svc.create({"name": "p2", "bound_username": "bob", "lan_cidrs": ["10.2.0.0/24"]})
    pid1 = str(r1["id"])
    assert svc.list_bound_usernames() == {"alice", "bob"}
    assert svc.list_bound_usernames(exclude_peer_id=pid1) == {"bob"}
