# -*- coding: utf-8 -*-
"""对端链本地工作副本的读写与同步标记。"""

import uuid

from app.services.peer_instance.remote_chain_cache import (
    delete_remote_chain_cache,
    mark_pending_apply,
    read_remote_chain_cache,
    record_from_fetch,
    rests_to_push_list,
    write_remote_chain_cache,
)


def test_read_missing_returns_none() -> None:
    pid = f"__no_such__{uuid.uuid4()}"
    assert read_remote_chain_cache(pid) is None


def test_write_read_round_trip_and_fetch_record(tmp_path, monkeypatch) -> None:
    """落库、读回；``record_from_fetch`` 清 pending 并设 pulled。"""
    monkeypatch.setattr(
        "app.services.peer_instance.remote_chain_cache.REMOTE_PEER_CHAINS_DIR",
        tmp_path,
    )
    pid = str(uuid.uuid4())
    ch = f"VPN_PEER_abc{pid[:4]}"
    write_remote_chain_cache(
        pid,
        chain=ch,
        chain_exists=True,
        rows=[
            {"rest": "-j RETURN", "enabled": True},
            {"rest": "-j ACCEPT", "enabled": True},
        ],
        pending_sync=True,
        last_sync_error="x",
    )
    out = read_remote_chain_cache(pid)
    assert out is not None
    assert out["pending_sync"] is True
    assert out["rests"] == ["-j RETURN", "-j ACCEPT"]
    assert [r.get("rest") for r in out["rows"]] == ["-j RETURN", "-j ACCEPT"]
    record_from_fetch(
        {
            "chain": ch,
            "chain_exists": True,
            "chain_rests": ["-j ACCEPT"],
        },
        pid,
    )
    out2 = read_remote_chain_cache(pid)
    assert out2 is not None
    assert out2["pending_sync"] is False
    assert out2["last_sync_error"] is None
    assert out2["rests"] == ["-j ACCEPT"]



def test_remote_cache_preserves_logical_tag_rows_without_raw_rest(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "app.services.peer_instance.remote_chain_cache.REMOTE_PEER_CHAINS_DIR",
        tmp_path,
    )
    pid = str(uuid.uuid4())
    binding = {
        "tag_id": "tag-ops",
        "tag_name": "运维",
        "action": "accept",
        "protocol": "tcp",
        "dest_ip": "192.168.10.10",
        "dest_port": "443",
    }
    write_remote_chain_cache(
        pid,
        chain="VPN_PEER_test",
        chain_exists=True,
        rows=[
            {"rest": "-s 10.8.0.0/24 -j ACCEPT", "enabled": True},
            {"rest": "", "enabled": True, "tag_binding": binding},
        ],
        pending_sync=True,
        last_sync_error=None,
    )

    cache = read_remote_chain_cache(pid)
    assert cache is not None
    assert cache["rows"][1]["rest"] == ""
    assert cache["rows"][1]["tag_binding"] == binding
    assert rests_to_push_list(cache["rows"]) == ["-s 10.8.0.0/24 -j ACCEPT"]



def test_delete_remote_chain_cache_removes_working_copy_and_prepush_backup(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "app.services.peer_instance.remote_chain_cache.REMOTE_PEER_CHAINS_DIR",
        tmp_path,
    )
    pid = str(uuid.uuid4())
    write_remote_chain_cache(
        pid,
        chain="VPN_PEER_test",
        chain_exists=True,
        rows=[{"rest": "-j ACCEPT", "enabled": True}],
        pending_sync=False,
        last_sync_error=None,
    )
    mark_pending_apply(
        pid, "VPN_PEER_test", True,
        [{"rest": "-j DROP", "enabled": True}],
    )

    delete_remote_chain_cache(pid)

    assert read_remote_chain_cache(pid) is None
    assert not (tmp_path / f"{pid}.prepush.bak").exists()
