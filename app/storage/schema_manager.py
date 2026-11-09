# -*- coding: utf-8 -*-
"""SQLite schema bootstrap and versioned migration runner.

DDL resources are shipped alongside the Python package.  SQL statements are
executed one by one inside a single explicit transaction; unlike
sqlite3.Connection.executescript this never implicitly commits prior work.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Callable

SCHEMA_VERSION = 5
_SQL_DIRECTORY = Path(__file__).resolve().parent / "sql"
_KNOWN_SQL = frozenset({
    "001_initial.sql",
    "002_query_indexes.sql",
    "004_firewall_tags.sql",
    "005_device_binding_exempt.sql",
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


def ensure_schema(
    conn: sqlite3.Connection,
    *,
    migrate_v2: Callable[[sqlite3.Connection], None],
    migrate_v3: Callable[[sqlite3.Connection], None],
    migrate_v4: Callable[[sqlite3.Connection], None],
    migrate_v5: Callable[[sqlite3.Connection], None],
    now_iso: Callable[[], str],
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
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
