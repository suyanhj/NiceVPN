# -*- coding: utf-8 -*-
"""设备绑定豁免：组/标签继承、总闸、原子快照、OpenVPN 脚本精确匹配。"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services.user import device_bind_policy as policy
from app.scripts.device_bind_exemption import exemption_source
from app.storage.sqlite_store import SqliteStore


@pytest.fixture()
def store(tmp_path):
    db = SqliteStore(tmp_path / "db.sqlite")
    db.upsert_group({
        "id": "root", "name": "默认组", "status": "active",
        "subnet": "10.8.0.0/16", "device_binding_exempt": False,
    })
    db.upsert_group({
        "id": "mgmt", "name": "管理组", "status": "active",
        "subnet": "10.8.1.0/24", "device_binding_exempt": True,
    })
    for username, group_id, status in [
        ("alice", "root", "active"), ("boss", "mgmt", "active"),
        ("disabled", "mgmt", "disabled"),
    ]:
        db.upsert_user({"username": username, "group_id": group_id, "status": status})
    return db


def test_group_and_tag_inheritance_with_priority_and_revocation(store):
    assert policy.build_device_bind_exemptions(store) == {"boss": "group:mgmt"}
    store.create_tag("t1", "出差", device_binding_exempt=True)
    assert store.get_tag("t1")["device_binding_exempt"] is True
    store.set_tag_members("t1", usernames=["alice"], group_ids=["mgmt"])
    assert policy.build_device_bind_exemptions(store) == {
        "alice": "tag:t1", "boss": "group_tag:t1",
    }
    store.set_tag_members("t1", usernames=[], group_ids=[])
    assert policy.build_device_bind_exemptions(store) == {"boss": "group:mgmt"}
    store.update_tag("t1", name="出差", device_binding_exempt=False)
    assert store.get_tag("t1")["device_binding_exempt"] is False
    store.upsert_group({
        "id": "mgmt", "name": "管理组", "status": "disabled",
        "device_binding_exempt": True,
    })
    assert policy.build_device_bind_exemptions(store) == {}


def test_generated_snapshot_is_exact_and_mode_gate_preserves_configuration(store, tmp_path, monkeypatch):
    snapshot = tmp_path / "exemptions.json"
    mode_path = tmp_path / "mode"
    monkeypatch.setattr(policy, "DEVICE_BIND_EXEMPTIONS_FILE", snapshot)
    monkeypatch.setattr(policy, "DEVICE_BIND_MODE_FILE", mode_path)
    from app.storage import sqlite_store
    monkeypatch.setattr(sqlite_store, "get_default_store", lambda: store)
    policy.sync_device_bind_mode_file("strict_hwaddr", exemption_enabled=True)
    assert mode_path.read_text(encoding="utf-8").splitlines() == [
        "strict_hwaddr", "exemptions_enabled=1",
    ]
    assert exemption_source(snapshot, "boss") == "group:mgmt"
    assert exemption_source(snapshot, "bo") is None
    assert exemption_source(snapshot, "boss\nalice") is None
    policy.sync_device_bind_mode_file("weak_log", exemption_enabled=True)
    assert mode_path.read_text(encoding="utf-8").splitlines()[1] == "exemptions_enabled=0"
    policy.sync_device_bind_mode_file("strict_hwaddr", exemption_enabled=False)
    assert "exemptions_enabled=0" in mode_path.read_text(encoding="utf-8")
    assert store.get_group("mgmt")["device_binding_exempt"] is True


def test_script_cli_fail_closed_and_audit_contract(tmp_path):
    script = Path(__file__).resolve().parents[2] / "app" / "scripts" / "device_bind_exemption.py"
    p = tmp_path / "snap.json"
    p.write_text(json.dumps({"version": 1, "users": {"boss": "group:mgmt"}}), encoding="utf-8")

    def run(username):
        return subprocess.run(
            [sys.executable, str(script), "--file", str(p), "--username", username],
            capture_output=True, text=True, check=False,
        )

    assert run("boss").returncode == 0
    assert run("boss").stdout.strip() == "group:mgmt"
    assert run("alice").returncode == 1
    p.write_text("{broken", encoding="utf-8")
    assert run("boss").returncode == 2
    sh = (Path(__file__).resolve().parents[2] / "app" / "scripts" / "device-bind.sh").read_text()
    assert sh.index('# ---------- 模式 A：仅记录 ----------') < sh.index('EXEMPT_SWITCH=""')
    assert 'export DEVICE_BIND_DECISION="exempt"' in sh
    assert sh.index('export DEVICE_BIND_DECISION="exempt"') < sh.index('# ---------- 模式 C：强绑定')


def test_sqlite_commits_refresh_active_snapshot(store, tmp_path, monkeypatch):
    from app.storage import sqlite_store
    snapshot = tmp_path / "exemptions.json"
    monkeypatch.setattr(policy, "DEVICE_BIND_EXEMPTIONS_FILE", snapshot)
    monkeypatch.setattr(sqlite_store, "get_default_store", lambda: store)
    monkeypatch.setattr(
        "app.core.config.load_config",
        lambda: SimpleNamespace(get=lambda key, default=None: {
            "initialized": True, "device_bind_mode": "strict_hwaddr",
            "device_bind_exemption_enabled": True,
        }.get(key, default)),
    )
    store.create_tag("t2", "管理豁免", device_binding_exempt=True)
    store.set_tag_members("t2", usernames=["alice"])
    assert exemption_source(snapshot, "alice") == "tag:t2"
    store.set_tag_members("t2", usernames=[])
    assert exemption_source(snapshot, "alice") is None
    assert exemption_source(snapshot, "boss") == "group:mgmt"


def test_irrelevant_repository_updates_do_not_trigger_policy_rebuild(
    store, monkeypatch,
):
    """修改普通用户描述、组名称、标签说明不应刷新运行时名单。"""
    from app.storage import sqlite_store

    monkeypatch.setattr(sqlite_store, "get_default_store", lambda: store)
    refreshes: list[str] = []
    monkeypatch.setattr(
        policy, "refresh_device_bind_exemption_snapshot",
        lambda _store: refreshes.append("refresh"),
    )

    # 初次写入的对象已存在；其有效豁免条件不发生变化。
    store.upsert_user({
        "username": "alice", "group_id": "root", "status": "active",
        "description": "仅更新备注",
    })
    store.upsert_group({
        "id": "root", "name": "普通员工（改名）",
        "status": "active", "subnet": "10.8.0.0/16",
        "device_binding_exempt": False,
    })
    store.create_tag("normal", "普通标签", device_binding_exempt=False)
    store.update_tag("normal", name="普通标签修改", description="仅备注")
    store.replace_entity_tags("user", "alice", ["normal"])
    assert refreshes == []

    store.create_tag("exempt", "豁免标签", device_binding_exempt=True)
    store.replace_entity_tags("user", "alice", ["normal", "exempt"])
    assert refreshes == ["refresh"]

    store.replace_entity_tags("user", "alice", ["normal", "exempt"])
    assert refreshes == ["refresh"]

    store.upsert_user({
        "username": "alice", "group_id": "mgmt", "status": "active",
    })
    assert refreshes == ["refresh", "refresh"]
    store.update_tag("exempt", name="取消豁免", device_binding_exempt=False)
    assert refreshes == ["refresh", "refresh", "refresh"]


def test_atomic_snapshot_noop_preserves_file_mtime(store, tmp_path, monkeypatch):
    """相同派生内容不产生新文件或更新时间；成员取消后原子覆盖删旧。"""
    from app.storage import sqlite_store

    snapshot = tmp_path / "exemptions.json"
    monkeypatch.setattr(policy, "DEVICE_BIND_EXEMPTIONS_FILE", snapshot)
    monkeypatch.setattr(sqlite_store, "get_default_store", lambda: store)

    policy.sync_device_bind_exemption_snapshot()
    first_mtime = snapshot.stat().st_mtime_ns
    policy.sync_device_bind_exemption_snapshot()
    assert snapshot.stat().st_mtime_ns == first_mtime

    store.create_tag("another", "豁免", device_binding_exempt=True)
    # 新标签还没有关联成员，不应产生新快照。
    policy.sync_device_bind_exemption_snapshot()
    assert snapshot.stat().st_mtime_ns == first_mtime
    store.set_tag_members("another", usernames=["alice"])
    policy.sync_device_bind_exemption_snapshot()
    assert exemption_source(snapshot, "alice") == "tag:another"
    store.set_tag_members("another", usernames=[])
    policy.sync_device_bind_exemption_snapshot()
    assert exemption_source(snapshot, "alice") is None
    assert exemption_source(snapshot, "boss") == "group:mgmt"
