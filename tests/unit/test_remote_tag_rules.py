from __future__ import annotations

import pytest

from app.services.firewall.remote_tag_rules import materialize_remote_chain_rows


class FakeTagService:
    def __init__(self, addresses):
        self.addresses = addresses

    def resolve_addresses(self, tag_id):
        if tag_id == "missing":
            raise ValueError("标签不存在")
        return tuple(self.addresses)


def test_materialize_remote_tag_rule_uses_current_member_addresses():
    rows = [
        {
            "rest": "",
            "enabled": True,
            "tag_binding": {
                "tag_id": "tag-ops",
                "action": "accept",
                "protocol": "tcp",
                "dest_ip": "192.168.10.10",
                "dest_port": "443",
            },
        },
        {"rest": "-s 10.9.0.0/24 -j DROP", "enabled": True},
    ]

    rests = materialize_remote_chain_rows(
        rows,
        tag_service=FakeTagService(["10.8.0.10", "10.8.0.11"]),
    )

    assert len(rests) == 3
    assert any("-s 10.8.0.10" in rest and "--dport 443" in rest for rest in rests)
    assert any("-s 10.8.0.11" in rest and "--dport 443" in rest for rest in rests)
    assert rests[-1] == "-s 10.9.0.0/24 -j DROP"


def test_empty_remote_tag_is_fail_closed_and_does_not_create_unrestricted_rule():
    rows = [{
        "rest": "",
        "enabled": True,
        "tag_binding": {
            "tag_id": "tag-empty",
            "action": "accept",
            "protocol": "all",
            "dest_ip": "192.168.10.10",
            "dest_port": "",
        },
    }]

    assert materialize_remote_chain_rows(rows, tag_service=FakeTagService([])) == []


def test_missing_remote_tag_fails_closed():
    rows = [{
        "rest": "",
        "enabled": True,
        "tag_binding": {
            "tag_id": "missing",
            "action": "accept",
            "protocol": "all",
        },
    }]

    with pytest.raises(RuntimeError, match="标签不存在"):
        materialize_remote_chain_rows(rows, tag_service=FakeTagService([]))



def test_remote_tag_sync_rebuilds_peer_chain_from_latest_tag_members(
    tmp_path, monkeypatch,
):
    from app.services.firewall.remote_tag_rules import sync_remote_tag_firewall_rules
    from app.services.peer_instance import remote_chain_cache
    from app.services.peer_instance.service import PeerService
    from app.storage.sqlite_store import SqliteStore

    store = SqliteStore(tmp_path / "remote-tag.sqlite")
    store.upsert_group({
        "id": "g1", "name": "研发", "subnet": "10.8.9.0/24", "status": "active",
    })
    store.create_tag("tag-ops", "运维")
    store.set_tag_members("tag-ops", usernames=[], group_ids=["g1"])
    store.upsert_peer({
        "id": "peer-remote-tag",
        "name": "远端站点",
        "bound_username": "alice",
        "lan_cidrs": ["192.168.50.0/24"],
    })
    monkeypatch.setattr(
        remote_chain_cache, "REMOTE_PEER_CHAINS_DIR", tmp_path / "remote-cache",
    )
    remote_chain_cache.write_remote_chain_cache(
        "peer-remote-tag",
        chain="VPN_PEER_remote",
        chain_exists=True,
        rows=[
            {
                "rest": "",
                "enabled": True,
                "tag_binding": {
                    "tag_id": "tag-ops",
                    "tag_name": "运维",
                    "action": "accept",
                    "protocol": "tcp",
                    "dest_ip": "192.168.50.10",
                    "dest_port": "443",
                },
            },
            {"rest": "-s 10.9.0.0/24 -j DROP", "enabled": True},
        ],
        pending_sync=False,
        last_sync_error=None,
    )
    pushed = []
    monkeypatch.setattr(
        PeerService,
        "apply_remote_peer_filter_chain_rests",
        lambda self, peer_id, rests: pushed.append((peer_id, list(rests))),
    )

    result = sync_remote_tag_firewall_rules(["tag-ops"], store=store)

    assert result["updated"] == ["peer-remote-tag"]
    assert result["failed"] == []
    assert len(pushed) == 1
    peer_id, rests = pushed[0]
    assert peer_id == "peer-remote-tag"
    assert any("-s 10.8.9.0/24" in rest and "--dport 443" in rest for rest in rests)
    assert rests[-1] == "-s 10.9.0.0/24 -j DROP"
    cache = remote_chain_cache.read_remote_chain_cache("peer-remote-tag")
    assert cache is not None
    assert cache["pending_sync"] is False
    assert cache["rows"][0]["tag_binding"]["tag_id"] == "tag-ops"
