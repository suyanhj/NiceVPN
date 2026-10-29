# -*- coding: utf-8 -*-
"""用户切换组服务测试。"""

from pathlib import Path

import pytest

from app.services.user import crud as mod
from app.storage.sqlite_store import SqliteStore


class _AuditStub:
    def __init__(self):
        self.entries: list[tuple] = []

    def log(self, *args):
        self.entries.append(args)


@pytest.fixture
def change_group_env(monkeypatch, tmp_path: Path):
    ccd_dir = tmp_path / "ccd"
    ccd_dir.mkdir()
    store = SqliteStore(tmp_path / "db.sqlite")

    monkeypatch.setattr(mod, "CCD_DIR", ccd_dir)
    monkeypatch.setattr(mod, "load_config", lambda: {"global_subnet": "10.8.0.0/16"})
    monkeypatch.setattr(mod, "fix_path_for_openvpn_shared_data", lambda path: None)

    import app.services.peer_instance.service as peer_mod

    sync_calls = {"count": 0}

    class PeerServiceStub:
        def sync_all_mesh_push_routes_in_ccd(self):
            sync_calls["count"] += 1

    monkeypatch.setattr(peer_mod, "PeerService", PeerServiceStub)
    return {
        "store": store,
        "ccd_dir": ccd_dir,
        "sync_calls": sync_calls,
    }


def _seed_group(store: SqliteStore, gid: str, name: str, subnet: str, users: int) -> None:
    store.upsert_group({
        "id": gid,
        "name": name,
        "subnet": subnet,
        "status": "active",
        "user_count": users,
    })


def test_change_group_reassigns_ip_preserves_ccd_blocks_and_kicks(monkeypatch, change_group_env):
    store = change_group_env["store"]
    ccd_dir = change_group_env["ccd_dir"]
    _seed_group(store, "g1", "old", "10.8.1.0/24", 1)
    _seed_group(store, "g2", "new", "10.8.2.0/24", 1)
    store.upsert_user({"username": "alice", "group_id": "g1", "status": "active"})
    store.upsert_user({"username": "bob", "group_id": "g2", "status": "active"})

    (ccd_dir / "alice").write_text(
        "\n".join([
            "disable",
            "ifconfig-push 10.8.1.2 255.255.0.0",
            "# --- ovpn-peer begin peer-a ---",
            "iroute 172.16.1.0 255.255.255.0",
            "# --- ovpn-peer end peer-a ---",
            "# --- ovpn-mesh-peer-routes begin ---",
            'push "route 192.168.1.0 255.255.255.0"',
            "# --- ovpn-mesh-peer-routes end ---",
            "",
        ]),
        encoding="utf-8",
    )
    (ccd_dir / "bob").write_text("ifconfig-push 10.8.2.2 255.255.0.0\n", encoding="utf-8")

    killed: list[str] = []
    monkeypatch.setattr(mod.UserService, "_kill_client_session", lambda self, username: killed.append(username))
    svc = mod.UserService(store)
    svc.audit = _AuditStub()

    result = svc.change_group("alice", "g2")

    assert result["changed"] is True
    assert result["old_virtual_ip"] == "10.8.1.2"
    assert result["new_virtual_ip"] == "10.8.2.3"
    assert store.get_user("alice")["group_id"] == "g2"
    assert store.get_group("g1")["user_count"] == 0
    assert store.get_group("g2")["user_count"] == 2
    ccd_text = (ccd_dir / "alice").read_text(encoding="utf-8")
    assert "disable" in ccd_text
    assert "ifconfig-push 10.8.2.3 255.255.0.0" in ccd_text
    assert "iroute 172.16.1.0 255.255.255.0" in ccd_text
    assert 'push "route 192.168.1.0 255.255.255.0"' in ccd_text
    assert change_group_env["sync_calls"]["count"] == 1
    assert killed == ["alice"]


def test_change_group_full_target_group_raises_before_state_change(monkeypatch, change_group_env):
    store = change_group_env["store"]
    ccd_dir = change_group_env["ccd_dir"]
    _seed_group(store, "g1", "old", "10.8.1.0/24", 1)
    _seed_group(store, "g2", "full", "10.8.2.0/30", 1)
    store.upsert_user({"username": "alice", "group_id": "g1", "status": "active"})
    store.upsert_user({"username": "bob", "group_id": "g2", "status": "active"})

    old_ccd = "ifconfig-push 10.8.1.2 255.255.0.0\n"
    (ccd_dir / "alice").write_text(old_ccd, encoding="utf-8")
    (ccd_dir / "bob").write_text("ifconfig-push 10.8.2.2 255.255.0.0\n", encoding="utf-8")

    killed: list[str] = []
    monkeypatch.setattr(mod.UserService, "_kill_client_session", lambda self, username: killed.append(username))
    svc = mod.UserService(store)
    svc.audit = _AuditStub()

    with pytest.raises(ValueError, match="没有可用 IP"):
        svc.change_group("alice", "g2")

    assert store.get_user("alice")["group_id"] == "g1"
    assert store.get_group("g1")["user_count"] == 1
    assert store.get_group("g2")["user_count"] == 1
    assert (ccd_dir / "alice").read_text(encoding="utf-8") == old_ccd
    assert change_group_env["sync_calls"]["count"] == 0
    assert killed == []
