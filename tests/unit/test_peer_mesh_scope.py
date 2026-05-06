# -*- coding: utf-8 -*-
"""对端 mesh 路由按组可见策略单元测试"""

from app.services.peer_instance.service import mesh_lan_cidrs_for_user_group


def test_visible_empty_means_all_groups():
    peers = [{"lan_cidrs": ["10.1.0.0/24"], "mesh_route_visible_group_ids": []}]
    assert mesh_lan_cidrs_for_user_group(peers, "any-gid") == ["10.1.0.0/24"]
    assert mesh_lan_cidrs_for_user_group(peers, "") == ["10.1.0.0/24"]


def test_visible_list_restricts():
    peers = [{"lan_cidrs": ["10.1.0.0/24"], "mesh_route_visible_group_ids": ["ga", "gb"]}]
    assert mesh_lan_cidrs_for_user_group(peers, "ga") == ["10.1.0.0/24"]
    assert mesh_lan_cidrs_for_user_group(peers, "gc") == []


def test_mixed_peers_union():
    peers = [
        {"lan_cidrs": ["10.0.0.0/24"], "mesh_route_visible_group_ids": []},
        {"lan_cidrs": ["192.168.0.0/24"], "mesh_route_visible_group_ids": ["g1"]},
    ]
    u1 = mesh_lan_cidrs_for_user_group(peers, "g1")
    assert set(u1) == {"192.168.0.0/24", "10.0.0.0/24"}
    u2 = mesh_lan_cidrs_for_user_group(peers, "g2")
    assert u2 == ["10.0.0.0/24"]
