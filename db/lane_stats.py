"""Per-lane daily counters — the substrate every liveness alarm reads.

All alarm rules read these tables, never in-memory counters (the founding
incident: a self-maintained counter that always printed 0 while the lane was
dead). Date is the ET calendar date string; the journal/server clock is UTC, so
counters are keyed on ET — never ``date.today()``.
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from db.base import DEFAULT_DB_PATH
from db.base import connect as _connect
from db.base import now_iso as _now

logger = logging.getLogger(__name__)

_ET = ZoneInfo("America/New_York")


def et_today() -> str:
    """Current calendar date in America/New_York (never the UTC server date)."""
    return datetime.now(_ET).date().isoformat()


def init_lane_stats_tables(db_path: Path = DEFAULT_DB_PATH) -> None:
    with _connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS lane_stats (
                lane       TEXT NOT NULL,
                date       TEXT NOT NULL,
                counter    TEXT NOT NULL,
                value      REAL NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (lane, date, counter)
            );
            """
        )
        conn.commit()


def bump(
    lane: str,
    counter: str,
    n: float = 1,
    *,
    db_path: Path = DEFAULT_DB_PATH,
) -> None:
    """Add ``n`` to the (lane, ET-today, counter) cell as an UPSERT.

    Fail-soft law: a counter write must never propagate into its caller (poll
    loops, the nightly scan, the trading path). A failed bump loses one count,
    logged.
    """
    try:
        init_lane_stats_tables(db_path)
        with _connect(db_path) as conn:
            conn.execute(
                """
                INSERT INTO lane_stats (lane, date, counter, value, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(lane, date, counter) DO UPDATE SET
                    value = lane_stats.value + excluded.value,
                    updated_at = excluded.updated_at
                """,
                (lane, et_today(), counter, float(n), _now()),
            )
            conn.commit()
    except Exception:
        logger.exception("lane_stats bump failed: lane=%s counter=%s", lane, counter)


def gauge(
    lane: str,
    counter: str,
    value: float,
    *,
    db_path: Path = DEFAULT_DB_PATH,
) -> None:
    """Set the (lane, ET-today, counter) cell to ``value`` (last-write-wins).

    For point-in-time measurements — max_open_age_days and friends — where
    bump's additive UPSERT would inflate the reading. Same fail-soft law.
    """
    try:
        init_lane_stats_tables(db_path)
        with _connect(db_path) as conn:
            conn.execute(
                """
                INSERT INTO lane_stats (lane, date, counter, value, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(lane, date, counter) DO UPDATE SET
                    value = excluded.value,
                    updated_at = excluded.updated_at
                """,
                (lane, et_today(), counter, float(value), _now()),
            )
            conn.commit()
    except Exception:
        logger.exception("lane_stats gauge failed: lane=%s counter=%s", lane, counter)


def day(lane: str, date: str, *, db_path: Path = DEFAULT_DB_PATH) -> dict[str, float]:
    """Every counter for one lane on one ET date, as ``{counter: value}``."""
    init_lane_stats_tables(db_path)
    with _connect(db_path) as conn:
        rows = conn.execute(
            "SELECT counter, value FROM lane_stats WHERE lane = ? AND date = ?",
            (lane, date),
        ).fetchall()
    return {str(r["counter"]): float(r["value"]) for r in rows}


def series(
    lane: str, counter: str, days: int, *, db_path: Path = DEFAULT_DB_PATH
) -> list[tuple[str, float]]:
    """The ``(date, value)`` points for one counter over its most recent
    ``days`` recorded dates, oldest first."""
    init_lane_stats_tables(db_path)
    with _connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT date, value FROM lane_stats
            WHERE lane = ? AND counter = ?
            ORDER BY date DESC
            LIMIT ?
            """,
            (lane, counter, days),
        ).fetchall()
    return [(str(r["date"]), float(r["value"])) for r in reversed(rows)]
