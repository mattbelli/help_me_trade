"""Shared SQLite schema migration helpers."""

from __future__ import annotations

import functools
import sqlite3
from collections.abc import Callable
from pathlib import Path


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


# --- once-per-process schema checks --------------------------------------------
#
# The db/ idiom is "every accessor calls init_<x>_tables() first" — cheap in
# isolation (~1-2ms: CREATE IF NOT EXISTS + a few dozen PRAGMA table_info), but
# the fusion intake and the idea sweep call these thousands of times per pass,
# and on 2026-08-17 that re-checking was a measurable slice of a core all day.
# Memoised per (path, inode): a DB file deleted and recreated at the same path
# (test fixtures) gets a new inode and re-runs the DDL.
_SCHEMA_READY: set[tuple[str, int]] = set()


def _schema_key(db_path: Path) -> tuple[str, int] | None:
    try:
        return (str(db_path), db_path.stat().st_ino)
    except OSError:
        return None


def once_per_db(init_fn: Callable[[Path], None]) -> Callable[[Path], None]:
    """Decorator for ``init_<x>_tables(db_path)``: run the DDL once per DB file
    per process. Keeps the wrapped function's default ``db_path``."""

    @functools.wraps(init_fn)
    def wrapper(*args, **kwargs) -> None:  # noqa: ANN002, ANN003
        db_path = args[0] if args else kwargs.get("db_path", init_fn.__defaults__[0])
        key = _schema_key(db_path)
        if key is not None and (init_fn.__qualname__, *key) in _SCHEMA_READY:
            return
        init_fn(*args, **kwargs)
        key = _schema_key(db_path)
        if key is not None:
            _SCHEMA_READY.add((init_fn.__qualname__, *key))

    return wrapper
