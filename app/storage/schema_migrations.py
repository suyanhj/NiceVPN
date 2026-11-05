# -*- coding: utf-8 -*-
"""Historical SQLite schema v2-v4 migrations.

Maintain the original version names and data transformations; no schema
version bump is required when relocating migrations to this module.
"""
from __future__ import annotations

import sqlite3

from app.storage.schema_manager import execute_sql_file
from app.storage.repository_helpers import (
    _now_iso, _load_payload, _dump_payload, _normalize_group_payload,
    _peer_index_values, _firewall_index_values,
)


def migrate_v2_query_indexes(conn: sqlite3.Connection) -> None:
    """补充分页、筛选所需的反规范化索引列，并回填现有 payload。"""

    peer_columns = {
        str(row["name"]) for row in conn.execute("PRAGMA table_info(peers)").fetchall()
    }
    for name, ddl in (
        ("name", "TEXT NOT NULL DEFAULT ''"),
        ("has_lan_cidrs", "INTEGER NOT NULL DEFAULT 0"),
        ("lan_cidrs_text", "TEXT NOT NULL DEFAULT ''"),
        ("center_forward_protocol", "TEXT NOT NULL DEFAULT 'all'"),
        ("center_forward_dest_ip", "TEXT NOT NULL DEFAULT ''"),
        ("center_forward_dest_port", "TEXT NOT NULL DEFAULT ''"),
    ):
        if name not in peer_columns:
            conn.execute(f"ALTER TABLE peers ADD COLUMN {name} {ddl}")

    firewall_columns = {
        str(row["name"])
        for row in conn.execute("PRAGMA table_info(firewall_rules)").fetchall()
    }
    for name, ddl in (
        ("protocol", "TEXT NOT NULL DEFAULT 'all'"),
        ("action", "TEXT NOT NULL DEFAULT 'accept'"),
        ("deployment_target", "TEXT NOT NULL DEFAULT 'center'"),
        ("source_text", "TEXT NOT NULL DEFAULT ''"),
        ("dest_ip", "TEXT NOT NULL DEFAULT ''"),
        ("dest_port", "TEXT NOT NULL DEFAULT ''"),
    ):
        if name not in firewall_columns:
            conn.execute(f"ALTER TABLE firewall_rules ADD COLUMN {name} {ddl}")

    for row in conn.execute("SELECT id, payload FROM peers").fetchall():
        data = _load_payload(str(row["payload"]))
        conn.execute(
            """
            UPDATE peers
            SET name = ?, has_lan_cidrs = ?, lan_cidrs_text = ?,
                center_forward_protocol = ?, center_forward_dest_ip = ?,
                center_forward_dest_port = ?
            WHERE id = ?
            """,
            (*_peer_index_values(data), str(row["id"])),
        )

    for row in conn.execute("SELECT id, payload FROM firewall_rules").fetchall():
        data = _load_payload(str(row["payload"]))
        conn.execute(
            """
            UPDATE firewall_rules
            SET protocol = ?, action = ?, deployment_target = ?,
                source_text = ?, dest_ip = ?, dest_port = ?
            WHERE id = ?
            """,
            (*_firewall_index_values(data), str(row["id"])),
        )

    execute_sql_file(conn, "002_query_indexes.sql")
    conn.execute(
        "INSERT INTO schema_migrations(version, name, applied_at) VALUES (?, ?, ?)",
        (2, "query-pagination-indexes", _now_iso()),
    )



def migrate_v3_normalize_group_cidrs(conn: sqlite3.Connection) -> None:
    """规范历史用户组 CIDR，只修正可合法解析的非网络地址写法。"""

    for row in conn.execute("SELECT id, payload FROM groups").fetchall():
        data = _load_payload(str(row["payload"]))
        normalized = _normalize_group_payload(data)
        if normalized == data:
            continue
        conn.execute(
            "UPDATE groups SET payload = ? WHERE id = ?",
            (_dump_payload(normalized), str(row["id"])),
        )

    conn.execute(
        "INSERT INTO schema_migrations(version, name, applied_at) VALUES (?, ?, ?)",
        (3, "normalize-group-cidrs", _now_iso()),
    )



def migrate_v4_tags(conn: sqlite3.Connection) -> None:
    """标签元数据和用户/组关联；外键级联只删除关联，不删除用户/组。"""
    execute_sql_file(conn, "004_firewall_tags.sql")
    conn.execute(
        "INSERT INTO schema_migrations(version, name, applied_at) VALUES (?, ?, ?)",
        (4, "firewall-tags", _now_iso()),
    )
