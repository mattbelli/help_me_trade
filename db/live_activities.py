"""Live Activity update-token registry — one row per (proposal, device)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from db.base import DEFAULT_DB_PATH
from db.base import connect as _connect
from db.base import now_iso as _now


def init_live_activity_tables(db_path: Path = DEFAULT_DB_PATH) -> None:
    with _connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS live_activities (
                proposal_id TEXT NOT NULL,
                device_token TEXT NOT NULL,
                update_token TEXT NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY (proposal_id, device_token)
            );
            """
        )
        conn.commit()


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
