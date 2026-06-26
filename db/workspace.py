"""Workspace layout server-side persistence."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from db.base import DEFAULT_DB_PATH
from db.base import connect as _connect


def init_workspace_tables(db_path: Path = DEFAULT_DB_PATH) -> None:
    with _connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS workspace_state (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            """
        )
        conn.commit()


def load_workspace_layout(db_path: Path = DEFAULT_DB_PATH) -> list | None:
    """Return the stored layout list, or None if absent/invalid (fail-soft)."""
    try:
        init_workspace_tables(db_path)
        with _connect(db_path) as conn:
            row = conn.execute(
                "SELECT value FROM workspace_state WHERE key = 'layout'",
            ).fetchone()
        if row is None:
            return None
        parsed = json.loads(row[0])
        if not isinstance(parsed, list):
            return None
        return parsed
    except Exception:
        return None


def save_workspace_layout(layout: list, db_path: Path = DEFAULT_DB_PATH) -> None:
    """Upsert the workspace layout. Last-write-wins."""
    init_workspace_tables(db_path)
    now = datetime.now(UTC).isoformat()
    value = json.dumps(layout)
    with _connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO workspace_state (key, value, updated_at)
            VALUES ('layout', ?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value,
                                           updated_at = excluded.updated_at
            """,
            (value, now),
        )
        conn.commit()
