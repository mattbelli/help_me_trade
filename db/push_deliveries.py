"""Per-device push delivery ledger for proposal notifications."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from db.base import DEFAULT_DB_PATH
from db.base import connect as _connect
from db.base import now_iso as _now


def init_push_delivery_tables(db_path: Path = DEFAULT_DB_PATH) -> None:
    with _connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS push_deliveries (
                proposal_id TEXT NOT NULL,
                device_token TEXT NOT NULL,
                channel TEXT NOT NULL,
                status TEXT NOT NULL,
                detail TEXT,
                attempts INTEGER NOT NULL DEFAULT 1,
                last_attempt_at TEXT NOT NULL,
                PRIMARY KEY (proposal_id, device_token)
            );

            CREATE INDEX IF NOT EXISTS idx_push_deliveries_proposal
                ON push_deliveries(proposal_id);
            """
        )
        conn.commit()


def record_delivery(
    proposal_id: str,
    device_token: str,
    channel: str,
    status: str,
    *,
    detail: str | None = None,
    db_path: Path = DEFAULT_DB_PATH,
) -> None:
    """UPSERT a delivery attempt — increments attempts on conflict."""
    init_push_delivery_tables(db_path)
    now = _now()
    with _connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO push_deliveries (
                proposal_id, device_token, channel, status, detail,
                attempts, last_attempt_at
            ) VALUES (?, ?, ?, ?, ?, 1, ?)
            ON CONFLICT(proposal_id, device_token) DO UPDATE SET
                status = excluded.status,
                detail = excluded.detail,
                attempts = push_deliveries.attempts + 1,
                last_attempt_at = excluded.last_attempt_at
            """,
            (proposal_id, device_token, channel, status, detail, now),
        )
        conn.commit()


def list_deliveries(
    proposal_id: str,
    *,
    db_path: Path = DEFAULT_DB_PATH,
) -> list[dict[str, Any]]:
    init_push_delivery_tables(db_path)
    with _connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT proposal_id, device_token, channel, status, detail,
                   attempts, last_attempt_at
            FROM push_deliveries
            WHERE proposal_id = ?
            ORDER BY last_attempt_at ASC
            """,
            (proposal_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def undelivered_tokens(
    proposal_id: str,
    *,
    max_attempts: int = 3,
    db_path: Path = DEFAULT_DB_PATH,
) -> list[str]:
    """Tokens with status='failed' and attempts below max_attempts."""
    init_push_delivery_tables(db_path)
    with _connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT device_token FROM push_deliveries
            WHERE proposal_id = ?
              AND status = 'failed'
              AND attempts < ?
            ORDER BY last_attempt_at ASC
            """,
            (proposal_id, max_attempts),
        ).fetchall()
    return [str(row["device_token"]) for row in rows]


def has_sent_delivery(
    proposal_id: str,
    *,
    db_path: Path = DEFAULT_DB_PATH,
) -> bool:
    init_push_delivery_tables(db_path)
    with _connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT 1 FROM push_deliveries
            WHERE proposal_id = ? AND status = 'sent'
            LIMIT 1
            """,
            (proposal_id,),
        ).fetchone()
    return row is not None


def delivery_channel(
    proposal_id: str,
    device_token: str,
    *,
    db_path: Path = DEFAULT_DB_PATH,
) -> str | None:
    init_push_delivery_tables(db_path)
    with _connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT channel FROM push_deliveries
            WHERE proposal_id = ? AND device_token = ?
            """,
            (proposal_id, device_token),
        ).fetchone()
    return str(row["channel"]) if row else None


def retries_exhausted_with_no_sent(
    proposal_id: str,
    *,
    max_attempts: int = 3,
    db_path: Path = DEFAULT_DB_PATH,
) -> bool:
    """True when every delivery row failed and all attempts are exhausted."""
    deliveries = list_deliveries(proposal_id, db_path=db_path)
    if not deliveries:
        return False
    if any(d["status"] == "sent" for d in deliveries):
        return False
    return all(
        d["status"] == "failed" and int(d["attempts"]) >= max_attempts
        for d in deliveries
    )
