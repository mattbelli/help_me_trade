"""Shared SQLite schema migration helpers."""

from __future__ import annotations

import sqlite3


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return {row[1] for row in rows}


def _add_column_if_missing(
    conn: sqlite3.Connection, table: str, column: str, decl: str
) -> bool:
    """Add a column when absent. Returns True if the column was added."""
    if column in _table_columns(conn, table):
        return False
    conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
    return True
