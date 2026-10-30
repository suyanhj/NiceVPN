# -*- coding: utf-8 -*-
"""SQLite repository 与 legacy JSON 自动迁移测试。"""

from __future__ import annotations

import json
import os
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


@pytest.mark.skipif(os.name == "nt", reason="POSIX permissions only")
def test_sqlite_database_permissions_are_private(tmp_path: Path) -> None:
    path = tmp_path / "db.sqlite"
    store = SqliteStore(path)
    store.initialize()
    assert (path.stat().st_mode & 0o777) == 0o600


def test_structured_services_do_not_read_legacy_json_directories() -> None:
    root = Path(__file__).resolve().parents[2]
    contracts = {
        "app/services/user/crud.py": ("USERS_DIR", "GROUPS_DIR", "read_json(", "write_json_atomic("),
        "app/services/group/crud.py": ("GROUPS_DIR", "read_json(", "write_json_atomic("),
        "app/services/peer_instance/service.py": ("PEERS_DIR", "read_json(", "write_json_atomic("),
        "app/services/firewall/rule_service.py": ("FIREWALL_DIR", "read_json(", "write_json_atomic("),
    }
    for rel, forbidden in contracts.items():
        source = (root / rel).read_text(encoding="utf-8")
        for token in forbidden:
            assert token not in source, f"{rel} must not use legacy storage token: {token}"


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

    assert store.list_usernames() == ["alice", "bob"]
    assert store.list_usernames(include_deleted=True) == ["alice", "bob", "deleted-user"]


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
    if os.name != "nt":
        assert (result.backup_dir.stat().st_mode & 0o777) == 0o700
        assert (
            (result.backup_dir / "peers" / "p1.json").stat().st_mode & 0o777
        ) == 0o600

    migration.reset_storage_ready_for_tests()
    second = migration.ensure_storage_ready(paths["db"])
    assert second.migrated is False


def test_existing_unmarked_database_with_legacy_json_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    paths = _legacy_layout(monkeypatch, tmp_path)
    SqliteStore(paths["db"]).initialize()
    legacy = paths["users"] / "alice.json"
    _write_json(legacy, {
        "username": "alice",
        "group_id": "g1",
        "status": "active",
    })

    migration.reset_storage_ready_for_tests()
    with pytest.raises(RuntimeError, match="未完成标记"):
        migration.ensure_storage_ready(paths["db"])

    assert legacy.is_file()


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
    assert not (paths["db"].parent / "nicevpn.db.migrating-wal").exists()
    assert not (paths["db"].parent / "nicevpn.db.migrating-shm").exists()


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


def test_schema_v2_adds_query_index_columns(tmp_path: Path) -> None:
    store = SqliteStore(tmp_path / "db.sqlite")
    store.initialize()

    assert store.schema_version() == 2
    with store.connection() as conn:
        peer_columns = {
            str(row["name"]) for row in conn.execute("PRAGMA table_info(peers)").fetchall()
        }
        firewall_columns = {
            str(row["name"])
            for row in conn.execute("PRAGMA table_info(firewall_rules)").fetchall()
        }

    assert {
        "name",
        "has_lan_cidrs",
        "lan_cidrs_text",
        "center_forward_protocol",
        "center_forward_dest_ip",
        "center_forward_dest_port",
    } <= peer_columns
    assert {
        "protocol",
        "action",
        "deployment_target",
        "source_text",
        "dest_ip",
        "dest_port",
    } <= firewall_columns


def test_users_groups_and_peers_support_database_pages(tmp_path: Path) -> None:
    store = SqliteStore(tmp_path / "db.sqlite")
    store.upsert_group({
        "id": "g-a",
        "name": "Alpha",
        "status": "active",
        "created_at": "2026-01-01T00:00:00+00:00",
    })
    store.upsert_group({
        "id": "g-b",
        "name": "Beta",
        "status": "disabled",
        "created_at": "2026-01-02T00:00:00+00:00",
    })

    for index in range(75):
        store.upsert_user({
            "username": f"user-{index:03d}",
            "group_id": "g-a" if index % 2 == 0 else "g-b",
            "status": "active" if index % 3 else "disabled",
            "created_at": f"2026-01-{(index % 28) + 1:02d}T00:00:00+00:00",
        })

    rows, total = store.list_users_page(
        offset=50,
        limit=25,
        group_ids=["g-a"],
        sort_by="username",
    )
    assert total == 38
    assert len(rows) == 0  # 过滤后不足 51 条，offset 确实由 SQLite 生效

    rows, total = store.list_users_page(
        offset=10,
        limit=10,
        search="user-0",
        group_ids=["g-a"],
        sort_by="username",
    )
    assert total == 38
    assert len(rows) == 10
    assert all(row["group_id"] == "g-a" for row in rows)

    rows, total = store.list_users_page(
        limit=50,
        search_values=["user-001", "user-074"],
        search_mode="equals",
        search_operator="OR",
        sort_by="username",
    )
    assert total == 2
    assert [row["username"] for row in rows] == ["user-001", "user-074"]

    rows, total = store.list_users_page(
        limit=50,
        search_values=["user-", "07"],
        search_mode="contains",
        search_operator="AND",
        sort_by="username",
    )
    assert total == 6
    assert [row["username"] for row in rows] == [
        "user-007",
        "user-070",
        "user-071",
        "user-072",
        "user-073",
        "user-074",
    ]

    group_rows, group_total = store.list_groups_page(
        limit=1,
        search="a",
        sort_by="name",
    )
    assert group_total == 2
    assert [row["name"] for row in group_rows] == ["Alpha"]

    for index in range(6):
        store.upsert_peer({
            "id": f"p-{index}",
            "name": f"Site {index}",
            "bound_username": f"peer-user-{index}",
            "lan_cidrs": [f"192.168.{index}.0/24"],
            "center_forward_priority": 100 + index,
        })
    peer_rows, peer_total = store.list_peers_page(
        offset=2,
        limit=2,
        search="Site",
        sort_by="name",
    )
    assert peer_total == 6
    assert [row["id"] for row in peer_rows] == ["p-2", "p-3"]


def test_firewall_unified_query_pages_filter_without_loading_all_payloads(
    tmp_path: Path,
) -> None:
    store = SqliteStore(tmp_path / "db.sqlite")
    store.upsert_group({"id": "g1", "name": "开发组", "status": "active"})
    store.upsert_peer({
        "id": "peer-1",
        "name": "广州站点",
        "bound_username": "peer-user",
        "lan_cidrs": ["192.168.10.0/24"],
        "center_forward_priority": 30,
        "center_forward_enabled": True,
        "center_forward_protocol": "udp",
        "center_forward_dest_ip": "10.0.0.0/8",
        "center_forward_dest_port": "53",
    })
    store.replace_firewall_owner(
        "g1",
        [
            {
                "id": "rule-1",
                "owner_type": "group",
                "owner_id": "g1",
                "priority": 10,
                "enabled": True,
                "protocol": "tcp",
                "action": "accept",
                "deployment_target": "center",
                "source_subnet": "10.8.0.0/24",
                "dest_ip": "172.16.0.10",
                "dest_port": "443",
                "description": "allow web admin",
            },
            {
                "id": "rule-2",
                "owner_type": "group",
                "owner_id": "g1",
                "priority": 20,
                "enabled": False,
                "protocol": "all",
                "action": "drop",
                "deployment_target": "center",
                "source_ips": ["10.8.0.20"],
                "dest_ip": "",
                "dest_port": "",
                "description": "legacy blocked path",
            },
        ],
    )

    rows, total = store.list_firewall_unified_page(limit=2)
    assert total == 3
    assert [row["row_id"] for row in rows] == ["rule-1", "rule-2"]

    searched, searched_total = store.list_firewall_unified_page(
        search="443",
        limit=50,
    )
    assert searched_total == 1
    assert searched[0]["row_id"] == "rule-1"

    multi_dest, multi_dest_total = store.list_firewall_unified_page(
        text_filters={
            "dest": (["172.16.", "10.0."], "contains", "OR"),
        },
        limit=50,
    )
    assert multi_dest_total == 2
    assert {row["row_id"] for row in multi_dest} == {"rule-1", "__center_peer__peer-1"}

    description_rows, description_total = store.list_firewall_unified_page(
        text_filters={
            "description": (["web", "admin"], "contains", "AND"),
        },
        limit=50,
    )
    assert description_total == 1
    assert description_rows[0]["row_id"] == "rule-1"

    excluded, excluded_total = store.list_firewall_unified_page(
        text_filters={
            "id": (["rule-1", "peer-1"], "notContains", "AND"),
        },
        limit=50,
    )
    assert excluded_total == 1
    assert excluded[0]["row_id"] == "rule-2"

    peer_rows, peer_total = store.list_firewall_unified_page(
        protocols=["udp"],
        kind_keys=["center_peer"],
        limit=50,
    )
    assert peer_total == 1
    assert peer_rows[0]["row_kind"] == "center_peer"
    assert peer_rows[0]["source_id"] == "peer-1"

    disabled, disabled_total = store.list_firewall_unified_page(
        enabled_values=[False],
        actions=["drop"],
        limit=50,
    )
    assert disabled_total == 1
    assert disabled[0]["row_id"] == "rule-2"

    facets = store.list_firewall_unified_facets()
    assert {"tcp", "udp", "all"} <= set(facets["protocols"])
    assert {"accept", "drop"} <= set(facets["actions"])
    assert {"center", "center_peer"} <= set(facets["kind_keys"])
