"""Earnings milestone push dedup — one alert per symbol/date/milestone."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from db.base import DEFAULT_DB_PATH
from db.base import connect as _connect
from db.base import now_iso as _now


def init_earnings_alert_tables(db_path: Path = DEFAULT_DB_PATH) -> None:
    with _connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS earnings_alerts_sent (
                key TEXT PRIMARY KEY,
                created_at TEXT NOT NULL
            );
            """
        )
        conn.commit()


def was_sent(key: str, *, db_path: Path | None = None) -> bool:
    resolved = db_path if db_path is not None else DEFAULT_DB_PATH
    init_earnings_alert_tables(resolved)
    with _connect(resolved) as conn:
        row = conn.execute(
            "SELECT 1 FROM earnings_alerts_sent WHERE key = ?", (key,)
        ).fetchone()
    return row is not None


def mark_sent(key: str, *, db_path: Path | None = None) -> None:
    resolved = db_path if db_path is not None else DEFAULT_DB_PATH
    init_earnings_alert_tables(resolved)
    with _connect(resolved) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO earnings_alerts_sent (key, created_at) VALUES (?, ?)",
            (key, _now()),
        )
        conn.commit()


def prune_earnings_alerts_sent(
    *, keep_days: int = 90, db_path: Path | None = None
) -> int:
    """Drop dedup keys older than ``keep_days`` — the table grows ~1 row per
    symbol/day forever otherwise (RVOL alert dedup). Keys are only needed while
    their symbol/date window is live, so old rows are pure dead weight. Returns
    the number of rows deleted."""
    resolved = db_path if db_path is not None else DEFAULT_DB_PATH
    init_earnings_alert_tables(resolved)
    cutoff = (datetime.now(UTC) - timedelta(days=keep_days)).isoformat()
    with _connect(resolved) as conn:
        cur = conn.execute(
            "DELETE FROM earnings_alerts_sent WHERE created_at < ?", (cutoff,)
        )
        conn.commit()
        return cur.rowcount
