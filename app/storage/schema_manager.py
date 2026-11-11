# -*- coding: utf-8 -*-
"""SQLite schema bootstrap and versioned migration runner.

DDL resources are shipped alongside the Python package.  SQL statements are
executed one by one inside a single explicit transaction; unlike
sqlite3.Connection.executescript this never implicitly commits prior work.
"""
from __future__ import annotations

import json
import os
import sqlite3
import tempfile
from pathlib import Path
from typing import Callable

SCHEMA_VERSION = 12
_SQL_DIRECTORY = Path(__file__).resolve().parent / "sql"
_KNOWN_SQL = frozenset({
    "001_initial.sql",
    "002_query_indexes.sql",
    "004_firewall_tags.sql",
    "005_device_binding_exempt.sql",
    "006_enterprise_sso.sql",
    "007_sso_multi_enterprise.sql",
    "008_sso_contact_profile.sql",
    "009_sso_unified_cleanup.sql",
    "010_sso_session_cache.sql",
    "011_sso_online_lease.sql",
    "012_sso_verified_enterprise_name.sql",
})


def read_sql(name: str) -> str:
    if name not in _KNOWN_SQL:
        raise ValueError(f"未知 SQLite schema 资源: {name}")
    path = _SQL_DIRECTORY / name
    try:
        script = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise RuntimeError(f"无法读取 SQLite schema 资源 {path}") from exc
    if not script.strip():
        raise RuntimeError(f"SQLite schema 资源为空: {path}")
    return script


def execute_sql_file(conn: sqlite3.Connection, name: str) -> None:
    """Execute DDL safely without executescript's implicit COMMIT."""
    sql = read_sql(name)
    buffer = ""
    for line in sql.splitlines(keepends=True):
        buffer += line
        if not sqlite3.complete_statement(buffer):
            continue
        statement = buffer.strip()
        if statement:
            conn.execute(statement)
        buffer = ""
    if buffer.strip():
        raise ValueError(f"SQLite schema SQL 不完整: {name}")


def _export_legacy_sso_audit(conn: sqlite3.Connection, path: Path | None) -> None:
    """Archive old SQLite auth events into a private JSONL before dropping the table.

    A failed export aborts the transaction, preserving the original data.
    Repeated attempts replace the deterministic archive atomically.
    """
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='sso_auth_audit'"
    ).fetchone()
    if row is None:
        return
    if conn.execute("SELECT 1 FROM sso_auth_audit LIMIT 1").fetchone() is None:
        return
    if path is None:
        raise RuntimeError("旧 SSO 审计含记录，迁移时必须提供历史日志导出路径")
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=".sso-legacy-", dir=path.parent)
    try:
        if os.name != "nt":
            os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            for event in conn.execute(
                "SELECT event_time,instance_id,common_name,provider_kind,"
                "enterprise_id,external_user_id,event_type,outcome,reason_code "
                "FROM sso_auth_audit ORDER BY id"
            ):
                output.write(json.dumps(dict(event), ensure_ascii=False, sort_keys=True))
                output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def ensure_schema(
    conn: sqlite3.Connection,
    *,
    migrate_v2: Callable[[sqlite3.Connection], None],
    migrate_v3: Callable[[sqlite3.Connection], None],
    migrate_v4: Callable[[sqlite3.Connection], None],
    migrate_v5: Callable[[sqlite3.Connection], None],
    now_iso: Callable[[], str],
    migrate_v6: Callable[[sqlite3.Connection], None] | None = None,
    legacy_sso_audit_export_path: Path | None = None,
) -> None:
    """Preserve historical schema v1-v4 semantics in a single transaction."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        execute_sql_file(conn, "001_initial.sql")
        check = conn.execute("PRAGMA quick_check").fetchone()
        if check is None or str(check[0]).lower() != "ok":
            raise RuntimeError(
                f"SQLite quick_check 失败: {check[0] if check else 'no result'}"
            )
        row = conn.execute(
            "SELECT MAX(version) AS version FROM schema_migrations"
        ).fetchone()
        current = int(row["version"] or 0)
        if current > SCHEMA_VERSION:
            raise RuntimeError(
                f"数据库 schema v{current} 高于当前程序支持的 v{SCHEMA_VERSION}"
            )
        if current < 1:
            conn.execute(
                "INSERT INTO schema_migrations(version, name, applied_at) "
                "VALUES (?, ?, ?)",
                (1, "initial-structured-metadata", now_iso()),
            )
            current = 1
        if current < 2:
            migrate_v2(conn)
            current = 2
        if current < 3:
            migrate_v3(conn)
            current = 3
        if current < 4:
            migrate_v4(conn)
            current = 4
        if current < 5:
            migrate_v5(conn)
            current = 5
        if current < 6:
            if migrate_v6 is None:
                tag_columns = {
                    str(row["name"])
                    for row in conn.execute("PRAGMA table_info(tags)")
                }
                if "sso_required" not in tag_columns:
                    conn.execute(
                        "ALTER TABLE tags ADD COLUMN sso_required INTEGER "
                        "NOT NULL DEFAULT 0 CHECK (sso_required IN (0, 1))"
                    )
                execute_sql_file(conn, "006_enterprise_sso.sql")
            else:
                migrate_v6(conn)
            conn.execute(
                "INSERT INTO schema_migrations(version, name, applied_at) VALUES (?, ?, ?)",
                (6, "enterprise-sso-policy", now_iso()),
            )
            current = 6
        if current < 7:
            provider_columns = {
                str(row["name"])
                for row in conn.execute("PRAGMA table_info(sso_auth_providers)")
            }
            if "display_name" not in provider_columns:
                conn.execute(
                    "ALTER TABLE sso_auth_providers "
                    "ADD COLUMN display_name TEXT NOT NULL DEFAULT ''"
                )
            execute_sql_file(conn, "007_sso_multi_enterprise.sql")
            conn.execute(
                "INSERT INTO schema_migrations(version, name, applied_at) VALUES (?, ?, ?)",
                (7, "sso-multi-enterprise", now_iso()),
            )
        if current < 8:
            provider_columns = {
                str(row["name"])
                for row in conn.execute("PRAGMA table_info(sso_auth_providers)")
            }
            if "read_user_profile" not in provider_columns:
                conn.execute(
                    "ALTER TABLE sso_auth_providers ADD COLUMN read_user_profile "
                    "INTEGER NOT NULL DEFAULT 0 CHECK (read_user_profile IN (0, 1))"
                )
            execute_sql_file(conn, "008_sso_contact_profile.sql")
            conn.execute(
                "INSERT INTO schema_migrations(version, name, applied_at) VALUES (?, ?, ?)",
                (8, "sso-optional-contact-user-read", now_iso()),
            )
        if current < 9:
            _export_legacy_sso_audit(conn, legacy_sso_audit_export_path)
            execute_sql_file(conn, "009_sso_unified_cleanup.sql")
            # Keep old provider credentials until an enabled unified app
            # exists. Once switched, old provider rows cannot be used.
            unified = conn.execute(
                "SELECT 1 FROM sso_auth_providers "
                "WHERE id='dingtalk_unified' AND enabled=1"
            ).fetchone()
            if unified is not None:
                conn.execute(
                    "UPDATE sso_login_transactions SET status='denied', "
                    "completed_at=CAST(strftime('%s','now') AS INTEGER) "
                    "WHERE status='pending' AND provider_id!='dingtalk_unified'"
                )
                conn.execute(
                    "DELETE FROM sso_auth_providers WHERE id!='dingtalk_unified'"
                )
            conn.execute(
                "INSERT INTO schema_migrations(version, name, applied_at) VALUES (?, ?, ?)",
                (9, "sso-unified-cleanup", now_iso()),
            )
        if current < 10:
            execute_sql_file(conn, "010_sso_session_cache.sql")
            conn.execute(
                "INSERT INTO schema_migrations(version, name, applied_at) VALUES (?, ?, ?)",
                (10, "sso-persistent-session-cache", now_iso()),
            )
        if current < 11:
            columns = {
                str(row["name"])
                for row in conn.execute("PRAGMA table_info(sso_auth_sessions)")
            }
            if "active_client_id" not in columns and "active_connected_at" not in columns:
                execute_sql_file(conn, "011_sso_online_lease.sql")
            else:
                # Prior test/partial upgrade may have applied one or both
                # ALTERs before recording the migration version.
                if "active_client_id" not in columns:
                    conn.execute("ALTER TABLE sso_auth_sessions ADD COLUMN active_client_id INTEGER")
                if "active_connected_at" not in columns:
                    conn.execute("ALTER TABLE sso_auth_sessions ADD COLUMN active_connected_at INTEGER")
            conn.execute(
                "INSERT INTO schema_migrations(version, name, applied_at) VALUES (?, ?, ?)",
                (11, "sso-online-lease", now_iso()),
            )
        if current < 12:
            columns = {
                str(row["name"])
                for row in conn.execute("PRAGMA table_info(sso_auth_sessions)")
            }
            if "enterprise_name" not in columns:
                execute_sql_file(conn, "012_sso_verified_enterprise_name.sql")
            conn.execute(
                "INSERT INTO schema_migrations(version, name, applied_at) VALUES (?, ?, ?)",
                (12, "sso-verified-enterprise-name", now_iso()),
            )
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
