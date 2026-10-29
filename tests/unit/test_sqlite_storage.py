# -*- coding: utf-8 -*-
"""SQLite repository 与 legacy JSON 自动迁移测试。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.storage import migration
from app.storage.sqlite_store import SqliteStore


def _write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def _legacy_layout(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict[str, Path]:
    paths = {
        "users": tmp_path / "data" / "users",
        "groups": tmp_path / "data" / "groups",
        "peers": tmp_path / "data" / "peers",
        "firewall": tmp_path / "data" / "firewall",
        "backups": tmp_path / "backups",
        "db": tmp_path / "data" / "nicevpn.db",
    }
    for key in ("users", "groups", "peers", "firewall"):
        paths[key].mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(migration, "USERS_DIR", paths["users"])
    monkeypatch.setattr(migration, "GROUPS_DIR", paths["groups"])
    monkeypatch.setattr(migration, "PEERS_DIR", paths["peers"])
    monkeypatch.setattr(migration, "FIREWALL_DIR", paths["firewall"])
    monkeypatch.setattr(migration, "BACKUPS_DIR", paths["backups"])
    migration.reset_storage_ready_for_tests()
    return paths


def test_repository_query_and_pagination(tmp_path: Path) -> None:
    store = SqliteStore(tmp_path / "db.sqlite")
    store.upsert_group({
        "id": "g1",
        "name": "默认用户组",
        "subnet": "10.8.0.0/16",
        "status": "active",
        "created_at": "2026-01-01T00:00:00+00:00",
    })
    for username, status in (
        ("alice", "active"),
        ("bob", "disabled"),
        ("deleted", "deleted"),
    ):
        store.upsert_user({
            "username": username,
            "group_id": "g1",
            "status": status,
        })

    rows, total = store.list_users_page(limit=1, offset=0, group_ids=["g1"])
    assert total == 2
    assert [row["username"] for row in rows] == ["alice"]
    assert store.list_active_usernames_by_group("g1") == ["alice"]
    assert store.count_users() == 2
    assert store.count_users(include_deleted=True) == 3


def test_legacy_json_migrates_once_and_is_archived(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    paths = _legacy_layout(monkeypatch, tmp_path)

    _write_json(paths["groups"] / "g1.json", {
        "id": "g1",
        "name": "默认用户组",
        "subnet": "10.8.0.0/16",
        "status": "active",
        "user_count": 1,
    })
    _write_json(paths["users"] / "alice.json", {
        "username": "alice",
        "group_id": "g1",
        "status": "active",
    })
    _write_json(paths["peers"] / "p1.json", {
        "id": "p1",
        "name": "site-a",
        "bound_username": "alice",
        "lan_cidrs": ["172.16.0.0/24"],
    })
    _write_json(paths["firewall"] / "g1.json", {
        "rules": [{
            "id": "r1",
            "owner_type": "group",
            "owner_id": "g1",
            "action": "accept",
            "priority": 10,
            "enabled": True,
        }],
    })
    (paths["users"] / "alice.json.lock").write_text("", encoding="utf-8")
    (paths["firewall"] / "g1.prepush.bak").write_text("legacy", encoding="utf-8")

    result = migration.ensure_storage_ready(paths["db"])

    assert result.migrated is True
    assert result.users == 1
    assert result.groups == 1
    assert result.peers == 1
    assert result.firewall_rules == 1
    assert result.backup_dir is not None
    assert paths["db"].is_file()

    store = SqliteStore(paths["db"])
    assert store.get_user("alice")["group_id"] == "g1"
    assert store.get_group("g1")["name"] == "默认用户组"
    assert store.get_peer("p1")["bound_username"] == "alice"
    assert store.get_firewall_owner("g1")[0]["id"] == "r1"
    assert store.get_meta("storage_backend") == "sqlite"

    assert not (paths["users"] / "alice.json").exists()
    assert not (paths["groups"] / "g1.json").exists()
    assert not (paths["peers"] / "p1.json").exists()
    assert not (paths["firewall"] / "g1.json").exists()
    assert (result.backup_dir / "users" / "alice.json").is_file()
    assert (result.backup_dir / "users" / "alice.json.lock").is_file()
    assert (result.backup_dir / "firewall" / "g1.prepush.bak").is_file()

    migration.reset_storage_ready_for_tests()
    second = migration.ensure_storage_ready(paths["db"])
    assert second.migrated is False


def test_migration_failure_keeps_legacy_json_and_no_live_database(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    paths = _legacy_layout(monkeypatch, tmp_path)
    broken = paths["users"] / "broken.json"
    broken.write_text("{not-json", encoding="utf-8")

    with pytest.raises(RuntimeError, match="legacy JSON"):
        migration.ensure_storage_ready(paths["db"])

    assert broken.is_file()
    assert not paths["db"].exists()
    assert not (paths["db"].parent / "nicevpn.db.migrating").exists()


def test_device_bindings_are_not_part_of_structured_sqlite_migration(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    paths = _legacy_layout(monkeypatch, tmp_path)
    runtime_bindings = tmp_path / "etc-openvpn" / "device_bindings"
    runtime_bindings.mkdir(parents=True)
    binding = runtime_bindings / "dev-1.json"
    binding.write_text('{"username":"alice"}', encoding="utf-8")

    migration.ensure_storage_ready(paths["db"])

    assert binding.is_file()
