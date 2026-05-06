# -*- coding: utf-8 -*-
"""对端 CCD iroute 块合并单元测试"""

from app.services.peer_instance.ccd_merge import (
    build_iroute_block,
    build_mesh_peer_push_routes_block,
    merge_mesh_peer_push_routes_into_ccd,
    merge_peer_block_into_ccd,
    sorted_unique_ipv4_cidrs,
    strip_peer_block_from_ccd,
)


def test_build_iroute_block_ipv4():
    blk = build_iroute_block("p1", ["192.168.1.0/24"])
    assert "# --- ovpn-peer begin p1 ---" in blk
    assert "iroute 192.168.1.0 255.255.255.0" in blk
    assert "# --- ovpn-peer end p1 ---" in blk


def test_merge_replaces_existing_block():
    base = "ifconfig-push 10.8.0.2 255.255.255.0\n"
    once = merge_peer_block_into_ccd(base, "abc", ["10.1.0.0/16"])
    assert once.count("ovpn-peer begin abc") == 1
    twice = merge_peer_block_into_ccd(once, "abc", ["10.2.0.0/24"])
    assert twice.count("ovpn-peer begin abc") == 1
    assert "iroute 10.2.0.0 255.255.255.0" in twice
    assert "iroute 10.1.0.0 255.255.0.0" not in twice


def test_strip_peer_block():
    text = merge_peer_block_into_ccd(
        "ifconfig-push 10.8.0.2 255.255.255.0\n",
        "x",
        ["172.16.0.0/24"],
    )
    out = strip_peer_block_from_ccd(text, "x")
    assert "ovpn-peer" not in out
    assert "ifconfig-push" in out


def test_sorted_unique_ipv4_cidrs():
    assert sorted_unique_ipv4_cidrs(["10.1.0.0/16", "10.1.0.0/16", "192.168.0.0/24"]) == [
        "10.1.0.0/16",
        "192.168.0.0/24",
    ]


def test_mesh_then_peer_then_mesh_is_stable():
    """mesh 与 peer 合并交替执行时，peer 应始终位于 mesh 之前，避免无意义二次写入。"""
    hdr = "ifconfig-push 10.224.200.2 255.255.0.0\n"
    pid = "3eef8d3c-7849-4365-a00a-c7837bf1c751"
    with_mesh = merge_mesh_peer_push_routes_into_ccd(hdr, ["10.3.0.0/16"])
    with_peer = merge_peer_block_into_ccd(with_mesh, pid, ["10.3.0.0/16"])
    peer_pos = with_peer.index("ovpn-peer begin")
    mesh_pos = with_peer.index("ovpn-mesh-peer-routes begin")
    assert peer_pos < mesh_pos
    remesh = merge_mesh_peer_push_routes_into_ccd(with_peer, ["10.3.0.0/16"])
    same_peer = merge_peer_block_into_ccd(remesh, pid, ["10.3.0.0/16"])
    assert remesh == with_peer
    assert same_peer == remesh


def test_mesh_push_block_and_merge():
    blk = build_mesh_peer_push_routes_block(["10.2.0.0/24"])
    assert "ovpn-mesh-peer-routes begin" in blk
    assert 'push "route 10.2.0.0 255.255.255.0"' in blk
    base = "ifconfig-push 10.8.0.2 255.255.255.0\n"
    once = merge_mesh_peer_push_routes_into_ccd(base, ["10.0.0.0/8"])
    assert once.count("ovpn-mesh-peer-routes begin") == 1
    twice = merge_mesh_peer_push_routes_into_ccd(once, ["172.16.0.0/24"])
    assert twice.count("ovpn-mesh-peer-routes begin") == 1
    assert "172.16.0.0" in twice
    assert "10.0.0.0" not in twice
    cleared = merge_mesh_peer_push_routes_into_ccd(twice, [])
    assert "ovpn-mesh-peer-routes" not in cleared
    assert "ifconfig-push" in cleared
