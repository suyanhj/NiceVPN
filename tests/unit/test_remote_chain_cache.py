# -*- coding: utf-8 -*-
"""对端链本地工作副本的读写与同步标记。"""

import uuid

from app.services.peer_instance.remote_chain_cache import (
    read_remote_chain_cache,
    record_from_fetch,
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
