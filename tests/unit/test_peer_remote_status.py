# -*- coding: utf-8 -*-
"""对端 SSH 连通性与远程操作状态按 Peer ID 隔离。"""

from unittest.mock import patch

import pytest

from app.services.peer_instance import peer_ssh_connect
from app.services.peer_instance.service import PeerService
from app.storage.sqlite_store import SqliteStore


def _peer(peer_id: str, host: str) -> dict:
    return {
        "id": peer_id,
        "name": peer_id,
        "bound_username": f"user-{peer_id}",
        "lan_cidrs": [],
        "ssh_host": host,
        "ssh_username": "root",
        "ssh_port": 22,
        "ssh_password": "secret",
    }


def test_peer_ssh_connection_status_is_recorded_per_peer(tmp_path, monkeypatch):
    store = SqliteStore(tmp_path / "peer-status.sqlite")
    first = _peer("peer-ok", "good.example")
    second = _peer("peer-failed", "bad.example")
    store.upsert_peer(first)
    store.upsert_peer(second)

    import app.storage.sqlite_store as sqlite_store_module
    monkeypatch.setattr(sqlite_store_module, "get_default_store", lambda: store)
    monkeypatch.setattr(peer_ssh_connect, "load_config", lambda: {})

    def fake_connect(host, port, username, **kwargs):
        if host == "bad.example":
            raise TimeoutError("timed out")
        return object()

    monkeypatch.setattr(peer_ssh_connect, "connect_peer_ssh_client", fake_connect)

    peer_ssh_connect.connect_peer_ssh_client_from_row(first)
    with pytest.raises(TimeoutError, match="timed out"):
        peer_ssh_connect.connect_peer_ssh_client_from_row(second)

    first_status = store.get_peer("peer-ok")["remote_status"]
    second_status = store.get_peer("peer-failed")["remote_status"]
    assert first_status["ssh_status"] == "connected"
    assert second_status["ssh_status"] == "failed"
    assert "timed out" in second_status["ssh_error"]


def test_remote_operation_failure_does_not_erase_ssh_success(tmp_path):
    store = SqliteStore(tmp_path / "peer-operation.sqlite")
    store.upsert_peer(_peer("peer-1", "peer.example"))
    store.update_peer_remote_status(
        "peer-1",
        ssh_status="connected",
        ssh_checked_at="2026-11-16T18:00:00+00:00",
    )
    service = PeerService(store=store)

    with patch(
        "app.core.config.load_config",
        return_value={"global_subnet": "10.8.0.0/16"},
    ):
        with patch(
            "app.services.peer_instance.remote_peer_iptables.apply_peer_site_iptables_via_ssh",
            side_effect=TimeoutError("iptables timed out"),
        ):
            with pytest.raises(TimeoutError, match="iptables timed out"):
                service.deploy_peer_site_firewall_via_ssh("peer-1")

    state = store.get_peer("peer-1")["remote_status"]
    assert state["ssh_status"] == "connected"
    assert state["last_operation"] == "对端防火墙下发"
    assert state["last_operation_status"] == "failed"
    assert "iptables timed out" in state["last_operation_error"]


def test_peer_upsert_preserves_remote_status_and_ssh_edit_resets_it(tmp_path):
    store = SqliteStore(tmp_path / "peer-status-reset.sqlite")
    row = _peer("peer-1", "old.example")
    store.upsert_peer(row)
    store.update_peer_remote_status(
        "peer-1",
        ssh_status="connected",
        ssh_checked_at="2026-11-16T18:00:00+00:00",
    )

    store.upsert_peer({**row, "name": "更新名称"})
    assert store.get_peer("peer-1")["remote_status"]["ssh_status"] == "connected"

    service = PeerService(store=store)
    service.update("peer-1", {"ssh_host": "new.example"})
    state = store.get_peer("peer-1")["remote_status"]
    assert state["ssh_status"] == "unknown"
    assert state["ssh_checked_at"] == ""



def test_openvpn_probe_records_ssh_failure_for_peer(tmp_path):
    store = SqliteStore(tmp_path / "peer-probe.sqlite")
    store.upsert_peer(_peer("peer-probe", "probe.example"))
    service = PeerService(store=store)

    with patch("app.core.config.load_config", return_value={}):
        with patch(
            "app.services.peer_instance.remote_openvpn.detect_openvpn_via_ssh",
            return_value={"connected": False, "ssh_error": "timed out"},
        ):
            result = service.probe_openvpn_via_ssh("peer-probe")

    assert result["connected"] is False
    state = store.get_peer("peer-probe")["remote_status"]
    assert state["ssh_status"] == "failed"
    assert "timed out" in state["ssh_error"]
    assert state["last_operation"] == "OpenVPN 探测"
    assert state["last_operation_status"] == "failed"
