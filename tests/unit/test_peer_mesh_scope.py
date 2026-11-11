# -*- coding: utf-8 -*-
"""对端 mesh 路由按组可见策略单元测试"""

from app.models.peer_instance import PeerInstance
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



def test_visible_tag_allows_direct_user_or_inherited_group_tag():
    peers = [{
        "lan_cidrs": ["10.20.0.0/24"],
        "mesh_route_visible_group_ids": [],
        "mesh_route_visible_tag_ids": ["tag-ops"],
    }]
    assert mesh_lan_cidrs_for_user_group(peers, "group-a", {"tag-ops"}) == ["10.20.0.0/24"]
    assert mesh_lan_cidrs_for_user_group(peers, "group-a", {"tag-other"}) == []


def test_visible_group_and_tag_conditions_are_or_not_and():
    peers = [{
        "lan_cidrs": ["10.30.0.0/24"],
        "mesh_route_visible_group_ids": ["group-a"],
        "mesh_route_visible_tag_ids": ["tag-ops"],
    }]
    assert mesh_lan_cidrs_for_user_group(peers, "group-a", set()) == ["10.30.0.0/24"]
    assert mesh_lan_cidrs_for_user_group(peers, "group-b", {"tag-ops"}) == ["10.30.0.0/24"]
    assert mesh_lan_cidrs_for_user_group(peers, "group-b", {"tag-other"}) == []


def test_empty_group_and_tag_visibility_means_all_users():
    peers = [{
        "lan_cidrs": ["10.40.0.0/24"],
        "mesh_route_visible_group_ids": [],
        "mesh_route_visible_tag_ids": [],
    }]
    assert mesh_lan_cidrs_for_user_group(peers, "", set()) == ["10.40.0.0/24"]



def test_peer_model_normalizes_tag_visibility_ids_and_legacy_defaults():
    legacy = PeerInstance(
        id="peer-legacy",
        name="legacy",
        bound_username="alice",
    )
    assert legacy.mesh_route_visible_tag_ids == []

    peer = PeerInstance(
        id="peer-tag",
        name="tagged",
        bound_username="bob",
        mesh_route_visible_tag_ids=[" tag-a ", "tag-a", "", "tag-b"],
    )
    assert peer.mesh_route_visible_tag_ids == ["tag-a", "tag-b"]



def test_mesh_sync_uses_direct_and_active_group_tags_and_clears_disabled_users(tmp_path, monkeypatch):
    from pathlib import Path

    from app.services.peer_instance import service as peer_module
    from app.services.peer_instance.service import PeerService
    from app.storage.sqlite_store import SqliteStore

    store = SqliteStore(tmp_path / "mesh-tags.sqlite")
    store.upsert_group({
        "id": "g-active", "name": "活动组",
        "subnet": "10.8.1.0/24", "status": "active",
    })
    store.upsert_group({
        "id": "g-disabled", "name": "停用组",
        "subnet": "10.8.2.0/24", "status": "disabled",
    })
    for username, group_id, status in [
        ("alice", "g-active", "active"),
        ("bob", "g-disabled", "active"),
        ("carol", "g-active", "disabled"),
        ("dave", "g-active", "active"),
    ]:
        store.upsert_user({
            "username": username, "group_id": group_id, "status": status,
        })
    store.create_tag("tag-group", "活动组标签")
    store.create_tag("tag-user", "个人标签")
    store.create_tag("tag-disabled-group", "停用组标签")
    store.set_tag_members("tag-group", usernames=[], group_ids=["g-active"])
    store.set_tag_members("tag-user", usernames=["dave"], group_ids=[])
    store.set_tag_members("tag-disabled-group", usernames=[], group_ids=["g-disabled"])
    store.upsert_peer({
        "id": "peer-tags",
        "name": "标签站点",
        "bound_username": "alice",
        "lan_cidrs": ["192.168.50.0/24"],
        "mesh_route_visible_group_ids": [],
        "mesh_route_visible_tag_ids": ["tag-group", "tag-user", "tag-disabled-group"],
    })

    ccd_dir = tmp_path / "ccd"
    ccd_dir.mkdir()
    for username in ("alice", "bob", "carol", "dave"):
        (ccd_dir / username).write_text("ifconfig-push 10.8.1.10 255.255.255.0\n", encoding="utf-8")
    monkeypatch.setattr(peer_module, "CCD_DIR", ccd_dir)
    monkeypatch.setattr(peer_module, "fix_path_for_openvpn_shared_data", lambda _path: None)

    PeerService(store=store).sync_all_mesh_push_routes_in_ccd()

    alice = (ccd_dir / "alice").read_text(encoding="utf-8")
    bob = (ccd_dir / "bob").read_text(encoding="utf-8")
    carol = (ccd_dir / "carol").read_text(encoding="utf-8")
    dave = (ccd_dir / "dave").read_text(encoding="utf-8")
    route = 'push "route 192.168.50.0 255.255.255.0"'

    assert route in alice  # group-inherited tag
    assert route not in bob  # inactive group tag is ignored
    assert route not in carol  # disabled users have mesh routes removed
    assert route in dave  # direct user tag
