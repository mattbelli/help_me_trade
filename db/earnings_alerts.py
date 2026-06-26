"""Earnings milestone push dedup — one alert per symbol/date/milestone."""

from __future__ import annotations

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
