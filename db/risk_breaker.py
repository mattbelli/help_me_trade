"""Daily-loss breaker state — per-account ET-day baseline and trip stage."""

from __future__ import annotations

import sqlite3
import time
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from db.base import DEFAULT_DB_PATH
from db.base import connect as _connect
from db.base import now_iso as _now

_ET = ZoneInfo("America/New_York")


def et_today() -> str:
    """Current calendar date in America/New_York (matches breaker session boundaries)."""
    return datetime.now(_ET).date().isoformat()


def init_risk_breaker_tables(db_path: Path = DEFAULT_DB_PATH) -> None:
    with _connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS risk_breaker_state (
                et_date TEXT NOT NULL,
                account_key TEXT NOT NULL,
                baseline_equity TEXT NOT NULL,
                stage TEXT NOT NULL DEFAULT 'armed',
                tripped_at TEXT,
                PRIMARY KEY (et_date, account_key)
            );
            """
        )


def get_or_create_baseline(
    account_key: str,
    equity: Decimal,
    *,
    db_path: Path = DEFAULT_DB_PATH,
) -> dict[str, Any]:
    """Return today's breaker row, inserting baseline on first exposure-increasing check.

    TODO: Baseline is lazy (first order of the day). Overnight loss before that first
    order is invisible until a scheduled session-open re-baseline job exists (#follow-up).
    """
    init_risk_breaker_tables(db_path)
    et_date = et_today()
    last_exc: sqlite3.OperationalError | None = None
    for attempt in range(3):
        try:
            with _connect(db_path) as conn:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO risk_breaker_state
                        (et_date, account_key, baseline_equity, stage, tripped_at)
                    VALUES (?, ?, ?, 'armed', NULL)
                    """,
                    (et_date, account_key, str(equity)),
                )
                conn.commit()
                row = conn.execute(
                    """
                    SELECT et_date, account_key, baseline_equity, stage, tripped_at
                    FROM risk_breaker_state
                    WHERE et_date = ? AND account_key = ?
                    """,
                    (et_date, account_key),
                ).fetchone()
            if row is None:
                raise RuntimeError(
                    f"risk_breaker_state row missing after upsert for {account_key!r}"
                )
            return {
                "et_date": row["et_date"],
                "account_key": row["account_key"],
                "baseline_equity": Decimal(str(row["baseline_equity"])),
                "stage": row["stage"],
                "tripped_at": row["tripped_at"],
            }
        except sqlite3.OperationalError as exc:
            last_exc = exc
            if attempt < 2:
                time.sleep(0.05)
                continue
            raise
    raise last_exc  # pragma: no cover


def trip(
    account_key: str,
    stage: str,
    *,
    db_path: Path = DEFAULT_DB_PATH,
) -> None:
    init_risk_breaker_tables(db_path)
    et_date = et_today()
    now = _now()
    with _connect(db_path) as conn:
        conn.execute(
            """
            UPDATE risk_breaker_state
            SET stage = ?, tripped_at = ?
            WHERE et_date = ? AND account_key = ?
            """,
            (stage, now, et_date, account_key),
        )
        conn.commit()


def current_stage(
    account_key: str,
    *,
    db_path: Path = DEFAULT_DB_PATH,
) -> str:
    init_risk_breaker_tables(db_path)
    et_date = et_today()
    with _connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT stage FROM risk_breaker_state
            WHERE et_date = ? AND account_key = ?
            """,
            (et_date, account_key),
        ).fetchone()
    return str(row["stage"]) if row else "armed"
