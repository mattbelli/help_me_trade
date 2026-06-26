"""Session-node strategy dedup / entry-context state."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from db.base import DEFAULT_DB_PATH
from db.base import connect as _connect
from db.base import now_iso as _now


def init_session_node_tables(db_path: Path = DEFAULT_DB_PATH) -> None:
    with _connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS session_node_events (
                key TEXT PRIMARY KEY,
                kind TEXT NOT NULL,
                payload_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL
            );
            """
        )
        conn.commit()


def seen(key: str, *, db_path: Path = DEFAULT_DB_PATH) -> bool:
    init_session_node_tables(db_path)
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT 1 FROM session_node_events WHERE key = ?", (key,)
        ).fetchone()
    return row is not None


def claim(
    key: str,
    kind: str,
    *,
    payload: dict[str, Any] | None = None,
    db_path: Path = DEFAULT_DB_PATH,
) -> bool:
    """Atomically claim a dedup key before a side effect (propose/push).

    Returns True if this caller won the claim (the key was newly inserted),
    False if it already existed. `INSERT OR IGNORE` makes this race-free, so a
    deterministically re-detected trigger can never fire its side effect twice —
    even if a second concurrent caller is ever added. Pair with `forget` to
    release the claim if the guarded side effect did NOT happen.
    """
    init_session_node_tables(db_path)
    now = _now()
    with _connect(db_path) as conn:
        cur = conn.execute(
            """
            INSERT OR IGNORE INTO session_node_events (key, kind, payload_json, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (key, kind, json.dumps(payload or {}), now),
        )
        conn.commit()
        return cur.rowcount > 0


def forget(key: str, *, db_path: Path = DEFAULT_DB_PATH) -> None:
    """Release a previously-claimed key (the guarded side effect didn't happen)."""
    init_session_node_tables(db_path)
    with _connect(db_path) as conn:
        conn.execute("DELETE FROM session_node_events WHERE key = ?", (key,))
        conn.commit()


def mark(
    key: str,
    kind: str,
    *,
    payload: dict[str, Any] | None = None,
    db_path: Path = DEFAULT_DB_PATH,
) -> None:
    init_session_node_tables(db_path)
    now = _now()
    with _connect(db_path) as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO session_node_events (key, kind, payload_json, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (key, kind, json.dumps(payload or {}), now),
        )
        conn.commit()


def get_payload(key: str, *, db_path: Path = DEFAULT_DB_PATH) -> dict[str, Any]:
    init_session_node_tables(db_path)
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT payload_json FROM session_node_events WHERE key = ?", (key,)
        ).fetchone()
    if row is None:
        return {}
    try:
        return json.loads(row["payload_json"] or "{}")
    except (json.JSONDecodeError, TypeError):
        return {}
