"""Live Activity update-token registry — one row per (proposal, device)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from db.base import DEFAULT_DB_PATH
from db.base import connect as _connect
from db.base import now_iso as _now
from db.schema_helpers import _add_column_if_missing, _table_columns


def _migrate_live_activities_schema(conn) -> None:
    """Allow nullable update_token and add started_at on legacy tables."""
    cols = _table_columns(conn, "live_activities")
    if not cols:
        return
    info = conn.execute("PRAGMA table_info(live_activities)").fetchall()
    update_notnull = next(
        (row[3] for row in info if row[1] == "update_token"),
        0,
    )
    if update_notnull or "started_at" not in cols:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS live_activities_new (
                proposal_id TEXT NOT NULL,
                device_token TEXT NOT NULL,
                update_token TEXT,
                started_at TEXT,
                created_at TEXT NOT NULL,
                PRIMARY KEY (proposal_id, device_token)
            );
            INSERT INTO live_activities_new (
                proposal_id, device_token, update_token, started_at, created_at
            )
            SELECT
                proposal_id,
                device_token,
                update_token,
                NULL,
                created_at
            FROM live_activities;
            DROP TABLE live_activities;
            ALTER TABLE live_activities_new RENAME TO live_activities;
            """
        )
    else:
        _add_column_if_missing(conn, "live_activities", "started_at", "TEXT")


def init_live_activity_tables(db_path: Path = DEFAULT_DB_PATH) -> None:
    with _connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS live_activities (
                proposal_id TEXT NOT NULL,
                device_token TEXT NOT NULL,
                update_token TEXT,
                started_at TEXT,
                created_at TEXT NOT NULL,
                PRIMARY KEY (proposal_id, device_token)
            );
            """
        )
        _migrate_live_activities_schema(conn)
        conn.commit()


def record_activity_start(
    proposal_id: str,
    device_token: str,
    *,
    db_path: Path = DEFAULT_DB_PATH,
) -> None:
    pid = str(proposal_id or "").strip()
    dev = str(device_token or "").strip()
    if not (pid and dev):
        return
    init_live_activity_tables(db_path)
    now = _now()
    with _connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO live_activities (
                proposal_id, device_token, update_token, started_at, created_at
            )
            VALUES (?, ?, NULL, ?, ?)
            ON CONFLICT(proposal_id, device_token) DO UPDATE SET
                started_at = excluded.started_at
            """,
            (pid, dev, now, now),
        )
        conn.commit()


def has_other_pending_start(
    device_token: str,
    proposal_id: str,
    *,
    db_path: Path = DEFAULT_DB_PATH,
) -> bool:
    dev = str(device_token or "").strip()
    pid = str(proposal_id or "").strip()
    if not (dev and pid):
        return False
    init_live_activity_tables(db_path)
    with _connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT 1
            FROM live_activities
            WHERE device_token = ?
              AND update_token IS NULL
              AND proposal_id != ?
            LIMIT 1
            """,
            (dev, pid),
        ).fetchone()
    return row is not None


def bind_update_token_latest(
    device_token: str,
    update_token: str,
    *,
    db_path: Path = DEFAULT_DB_PATH,
) -> str | None:
    dev = str(device_token or "").strip()
    upd = str(update_token or "").strip()
    if not (dev and upd):
        return None
    init_live_activity_tables(db_path)
    with _connect(db_path) as conn:
        conn.execute(
            """
            UPDATE live_activities
            SET update_token = ?
            WHERE rowid = (
                SELECT rowid
                FROM live_activities
                WHERE device_token = ?
                  AND update_token IS NULL
                ORDER BY started_at DESC, created_at DESC
                LIMIT 1
            )
            """,
            (upd, dev),
        )
        row = conn.execute(
            """
            SELECT proposal_id
            FROM live_activities
            WHERE device_token = ? AND update_token = ?
            """,
            (dev, upd),
        ).fetchone()
        conn.commit()
    if row is None:
        return None
    return str(row["proposal_id"])


def record_update_token(
    proposal_id: str,
    device_token: str,
    update_token: str,
    *,
    db_path: Path = DEFAULT_DB_PATH,
) -> None:
    pid = str(proposal_id or "").strip()
    dev = str(device_token or "").strip()
    upd = str(update_token or "").strip()
    if not (pid and dev and upd):
        return
    init_live_activity_tables(db_path)
    now = _now()
    with _connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO live_activities (proposal_id, device_token, update_token, created_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(proposal_id, device_token) DO UPDATE SET
                update_token = excluded.update_token,
                created_at = excluded.created_at
            """,
            (pid, dev, upd, now),
        )
        conn.commit()


def list_update_tokens(
    proposal_id: str,
    *,
    db_path: Path = DEFAULT_DB_PATH,
) -> list[dict[str, Any]]:
    pid = str(proposal_id or "").strip()
    if not pid:
        return []
    init_live_activity_tables(db_path)
    with _connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT device_token, update_token
            FROM live_activities
            WHERE proposal_id = ?
              AND update_token IS NOT NULL
            ORDER BY created_at DESC
            """,
            (pid,),
        ).fetchall()
    return [{"device_token": row["device_token"], "update_token": row["update_token"]} for row in rows]


def clear_for_proposal(
    proposal_id: str,
    *,
    db_path: Path = DEFAULT_DB_PATH,
) -> None:
    pid = str(proposal_id or "").strip()
    if not pid:
        return
    init_live_activity_tables(db_path)
    with _connect(db_path) as conn:
        conn.execute("DELETE FROM live_activities WHERE proposal_id = ?", (pid,))
        conn.commit()
