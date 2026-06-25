# -*- coding: utf-8 -*-
"""用户切换组服务测试。"""

import json
from pathlib import Path

import pytest

from app.services.user import crud as mod


class _AuditStub:
    """记录审计调用，避免测试写入真实审计目录。"""

    def __init__(self):
        self.entries: list[tuple] = []

    def log(self, *args):
        self.entries.append(args)


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture
def change_group_env(monkeypatch, tmp_path: Path):
    """准备隔离的用户、组与 CCD 目录。"""
    users_dir = tmp_path / "users"
    groups_dir = tmp_path / "groups"
    ccd_dir = tmp_path / "ccd"
    users_dir.mkdir()
    groups_dir.mkdir()
    ccd_dir.mkdir()

    monkeypatch.setattr(mod, "USERS_DIR", users_dir)
    monkeypatch.setattr(mod, "GROUPS_DIR", groups_dir)
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
        "users_dir": users_dir,
        "groups_dir": groups_dir,
        "ccd_dir": ccd_dir,
        "sync_calls": sync_calls,
    }


def test_change_group_reassigns_ip_preserves_ccd_blocks_and_kicks(monkeypatch, change_group_env):
    """切换组后重分配新组 IP，并保留 disable、peer iroute、mesh push 块。"""
    users_dir = change_group_env["users_dir"]
    groups_dir = change_group_env["groups_dir"]
    ccd_dir = change_group_env["ccd_dir"]

    _write_json(groups_dir / "g1.json", {"id": "g1", "name": "old", "subnet": "10.8.1.0/24", "status": "active", "user_count": 1})
    _write_json(groups_dir / "g2.json", {"id": "g2", "name": "new", "subnet": "10.8.2.0/24", "status": "active", "user_count": 1})
    _write_json(users_dir / "alice.json", {"username": "alice", "group_id": "g1", "status": "active"})
    _write_json(users_dir / "bob.json", {"username": "bob", "group_id": "g2", "status": "active"})
    (ccd_dir / "alice").write_text(
        "\n".join(
            [
                "disable",
                "ifconfig-push 10.8.1.2 255.255.0.0",
                "# --- ovpn-peer begin peer-a ---",
                "iroute 172.16.1.0 255.255.255.0",
                "# --- ovpn-peer end peer-a ---",
                "# --- ovpn-mesh-peer-routes begin ---",
                'push "route 192.168.1.0 255.255.255.0"',
                "# --- ovpn-mesh-peer-routes end ---",
                "",
            ]
        ),
        encoding="utf-8",
    )
    (ccd_dir / "bob").write_text("ifconfig-push 10.8.2.2 255.255.0.0\n", encoding="utf-8")

    killed: list[str] = []
    monkeypatch.setattr(mod.UserService, "_kill_client_session", lambda self, username: killed.append(username))
    svc = mod.UserService()
    svc.audit = _AuditStub()

    result = svc.change_group("alice", "g2")

    assert result["changed"] is True
    assert result["old_virtual_ip"] == "10.8.1.2"
    assert result["new_virtual_ip"] == "10.8.2.3"
    assert _read_json(users_dir / "alice.json")["group_id"] == "g2"
    assert _read_json(groups_dir / "g1.json")["user_count"] == 0
    assert _read_json(groups_dir / "g2.json")["user_count"] == 2
    ccd_text = (ccd_dir / "alice").read_text(encoding="utf-8")
    assert "disable" in ccd_text
    assert "ifconfig-push 10.8.2.3 255.255.0.0" in ccd_text
    assert "iroute 172.16.1.0 255.255.255.0" in ccd_text
    assert 'push "route 192.168.1.0 255.255.255.0"' in ccd_text
    assert change_group_env["sync_calls"]["count"] == 1
    assert killed == ["alice"]


def test_change_group_full_target_group_raises_before_state_change(monkeypatch, change_group_env):
    """目标组无可用 IP 时明确抛错，且不改用户、计数和 CCD。"""
    users_dir = change_group_env["users_dir"]
    groups_dir = change_group_env["groups_dir"]
    ccd_dir = change_group_env["ccd_dir"]

    _write_json(groups_dir / "g1.json", {"id": "g1", "name": "old", "subnet": "10.8.1.0/24", "status": "active", "user_count": 1})
    _write_json(groups_dir / "g2.json", {"id": "g2", "name": "full", "subnet": "10.8.2.0/30", "status": "active", "user_count": 1})
    _write_json(users_dir / "alice.json", {"username": "alice", "group_id": "g1", "status": "active"})
    _write_json(users_dir / "bob.json", {"username": "bob", "group_id": "g2", "status": "active"})
    old_ccd = "ifconfig-push 10.8.1.2 255.255.0.0\n"
    (ccd_dir / "alice").write_text(old_ccd, encoding="utf-8")
    (ccd_dir / "bob").write_text("ifconfig-push 10.8.2.2 255.255.0.0\n", encoding="utf-8")

    killed: list[str] = []
    monkeypatch.setattr(mod.UserService, "_kill_client_session", lambda self, username: killed.append(username))
    svc = mod.UserService()
    svc.audit = _AuditStub()

    with pytest.raises(ValueError, match="没有可用 IP"):
        svc.change_group("alice", "g2")

    assert _read_json(users_dir / "alice.json")["group_id"] == "g1"
    assert _read_json(groups_dir / "g1.json")["user_count"] == 1
    assert _read_json(groups_dir / "g2.json")["user_count"] == 1
    assert (ccd_dir / "alice").read_text(encoding="utf-8") == old_ccd
    assert change_group_env["sync_calls"]["count"] == 0
    assert killed == []
