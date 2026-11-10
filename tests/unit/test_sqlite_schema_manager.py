# -*- coding: utf-8 -*-
"""SQLite schema resources, upgrade behavior and transactional rollback."""
from __future__ import annotations

import sqlite3

import pytest

from app.storage.schema_manager import (
    SCHEMA_VERSION,
    ensure_schema,
    execute_sql_file,
    read_sql,
)
from app.storage.sqlite_store import SqliteStore


def test_schema_ddl_is_packaged_and_initializes_v10(tmp_path):
    assert SCHEMA_VERSION == 10
    for name in ("001_initial.sql", "002_query_indexes.sql", "004_firewall_tags.sql"):
        assert "CREATE" in read_sql(name)
    assert "ALTER TABLE tags" in read_sql("005_device_binding_exempt.sql")
    assert "CREATE TABLE" in read_sql("006_enterprise_sso.sql")
    assert "CREATE INDEX" in read_sql("007_sso_multi_enterprise.sql")
    assert "CREATE INDEX" in read_sql("008_sso_contact_profile.sql")
    assert "DROP TABLE" in read_sql("009_sso_unified_cleanup.sql")
    assert "CREATE TABLE" in read_sql("010_sso_session_cache.sql")
    store = SqliteStore(tmp_path / "nicevpn.sqlite")
    store.initialize()
    assert store.schema_version() == 10
    with store.connection() as conn:
        tables = {
            row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
    assert {"users", "groups", "peers", "firewall_rules",
            "tags", "user_tags", "group_tags", "schema_migrations"} <= tables
    store.initialize()
    assert store.schema_version() == 10


def test_ddl_migrations_roll_back_entire_new_database_on_failure(tmp_path):
    path = tmp_path / "interrupted.sqlite"
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        def fail_v2(c):
            c.execute("ALTER TABLE users ADD COLUMN temp_test TEXT")
            raise RuntimeError("injected migration failure")

        with pytest.raises(RuntimeError, match="injected migration failure"):
            ensure_schema(
                conn, migrate_v2=fail_v2, migrate_v3=lambda _: None,
                migrate_v4=lambda _: None, migrate_v5=lambda _: None,
                now_iso=lambda: "test",
            )
        assert conn.execute(
            "SELECT count(*) FROM sqlite_master WHERE type='table' "
            "AND name='users'"
        ).fetchone()[0] == 0
        assert conn.execute(
            "SELECT count(*) FROM sqlite_master WHERE type='table' "
            "AND name='schema_migrations'"
        ).fetchone()[0] == 0
    finally:
        conn.close()


def test_v4_failure_restores_existing_v3_schema(tmp_path):
    path = tmp_path / "existing.sqlite"
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        execute_sql_file(conn, "001_initial.sql")
        conn.execute(
            "INSERT INTO schema_migrations(version, name, applied_at) "
            "VALUES (3, 'previous', 'yesterday')"
        )
        conn.commit()

        def fail_v4(c):
            execute_sql_file(c, "004_firewall_tags.sql")
            raise RuntimeError("stop after v4 ddl")

        with pytest.raises(RuntimeError, match="stop after v4 ddl"):
            ensure_schema(
                conn, migrate_v2=lambda _: None,
                migrate_v3=lambda _: None,
                migrate_v4=fail_v4, migrate_v5=lambda _: None,
                now_iso=lambda: "test",
            )
        assert conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0] == 3
        assert conn.execute(
            "SELECT count(*) FROM sqlite_master WHERE type='table' AND name='tags'"
        ).fetchone()[0] == 0
    finally:
        conn.close()


def test_sql_resource_loader_rejects_unknown_files():
    with pytest.raises(ValueError, match="未知"):
        read_sql("../sqlite_store.py")


def test_future_schema_version_rejected_without_mutating_database(tmp_path):
    path = tmp_path / "future.sqlite"
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        execute_sql_file(conn, "001_initial.sql")
        conn.execute(
            "INSERT INTO schema_migrations VALUES (99, 'future', 'tomorrow')"
        )
        conn.commit()
    finally:
        conn.close()

    with pytest.raises(RuntimeError, match="高于当前程序支持"):
        SqliteStore(path).initialize()

    conn = sqlite3.connect(path)
    try:
        assert conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0] == 99
    finally:
        conn.close()


def test_v8_to_v9_archives_old_auth_rows_and_prunes_legacy_only_when_unified(tmp_path):
    import json

    from app.services.auth.provider_store import SsoProviderStore

    db = tmp_path / "migration.db"
    store = SqliteStore(db)
    store.initialize()
    providers = SsoProviderStore(store, key_path=tmp_path / "secret.key")
    providers.save(
        provider_id="legacy", kind="dingtalk", enterprise_id="dingOLD",
        app_id="old-app", secret="secret-old", enabled=True,
    )
    providers.save_unified(app_id="global-app", secret="secret-unified")

    # Simulate a persisted version 8 database with historic audit records.
    conn = sqlite3.connect(db)
    try:
        conn.execute("DELETE FROM schema_migrations WHERE version>=9")
        execute_sql_file(conn, "006_enterprise_sso.sql")
        conn.execute(
            "INSERT INTO sso_auth_audit "
            "(event_time,instance_id,common_name,provider_kind,enterprise_id,"
            "external_user_id,event_type,outcome,reason_code)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            ("2026-11-09T13:00:00+00:00", "server", "cert1", "dingtalk",
             "dingOLD", "uid", "approved", "success", "enterprise_match"),
        )
        conn.commit()
    finally:
        conn.close()

    upgraded = SqliteStore(db)
    upgraded.initialize()
    assert upgraded.schema_version() == 10
    exported = tmp_path / "logs" / "sso-auth-legacy.jsonl"
    events = [json.loads(line) for line in exported.read_text(encoding="utf-8").splitlines()]
    assert len(events) == 1
    assert events[0]["common_name"] == "cert1"
    assert events[0]["enterprise_id"] == "dingOLD"
    with upgraded.connection() as conn:
        tables = {
            row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        assert "sso_auth_audit" not in tables
        assert "sso_auth_providers" in tables
        assert "sso_login_transactions" in tables
        assert {row[0] for row in conn.execute(
            "SELECT id FROM sso_auth_providers"
        )} == {"dingtalk_unified"}
        assert conn.execute(
            "SELECT COUNT(*) FROM schema_migrations WHERE version=9"
        ).fetchone()[0] == 1

    # A second startup does not replay the archive or recreate dropped data.
    upgraded_again = SqliteStore(db)
    upgraded_again.initialize()
    assert upgraded_again.schema_version() == 10
    assert len(exported.read_text(encoding="utf-8").splitlines()) == 1


def test_v9_keeps_legacy_credentials_until_unified_app_exists(tmp_path):
    from app.services.auth.provider_store import SsoProviderStore

    db = tmp_path / "keep-legacy.db"
    store = SqliteStore(db)
    store.initialize()
    providers = SsoProviderStore(store, key_path=tmp_path / "secret.key")
    providers.save(
        provider_id="legacy", kind="dingtalk", enterprise_id="dingOLD",
        app_id="old-app", secret="secret-old", enabled=True,
    )
    assert providers.get("legacy") is not None
    with pytest.raises(ValueError, match="未启用"):
        providers.cleanup_unified_legacy()
    assert providers.get("legacy") is not None
