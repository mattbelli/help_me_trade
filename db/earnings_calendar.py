"""Live earnings calendar — recent + upcoming earnings dates per symbol.

Populated from Finnhub on a daily schedule (see signals/earnings_refresh.py). The
earnings-drift strategy reads it at scan time: the LAST earnings date (to score the
prior reaction) and the NEXT earnings date (to time the pre-earnings entry/exit).

Separate from the research `earnings.db` (EDGAR 8-K history used for backtests) —
this table is the *live forward-looking* feed the running scan depends on.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from db.base import DEFAULT_DB_PATH
from db.base import connect as _connect
from db.base import now_iso as _now


def init_earnings_calendar_tables(db_path: Path = DEFAULT_DB_PATH) -> None:
    with _connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS earnings_calendar (
                symbol TEXT NOT NULL,
                date TEXT NOT NULL,
                hour TEXT,
                source TEXT NOT NULL DEFAULT 'finnhub',
                fetched_at TEXT NOT NULL,
                PRIMARY KEY (symbol, date)
            );
            CREATE INDEX IF NOT EXISTS idx_earnings_cal_symbol
                ON earnings_calendar (symbol);
            """
        )
        conn.commit()


def upsert_earnings(
    rows: list[dict[str, Any]],
    *,
    db_path: Path = DEFAULT_DB_PATH,
) -> int:
    """Insert/refresh (symbol, date) rows. Each row: {symbol, date, hour?, source?}.

    `source` is per-row because one refresh mixes providers (forward dates from a
    vendor calendar, past announce dates from SEC EDGAR). It updates on conflict, so
    a date later confirmed by an authoritative source gets re-tagged.
    """
    init_earnings_calendar_tables(db_path)
    now = _now()
    cleaned: list[tuple[str, str, str | None, str, str]] = []
    for r in rows:
        symbol = str(r.get("symbol", "")).strip().upper()
        date = str(r.get("date", "")).strip()
        if not symbol or not date:
            continue
        hour = r.get("hour")
        source = str(r.get("source") or "finnhub").strip() or "finnhub"
        cleaned.append((symbol, date, str(hour) if hour else None, source, now))
    if not cleaned:
        return 0
    with _connect(db_path) as conn:
        conn.executemany(
            """
            INSERT INTO earnings_calendar (symbol, date, hour, source, fetched_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(symbol, date) DO UPDATE SET
                hour = COALESCE(excluded.hour, earnings_calendar.hour),
                source = excluded.source,
                fetched_at = excluded.fetched_at
            """,
            cleaned,
        )
        conn.commit()
    return len(cleaned)


def earnings_dates_for(
    symbol: str,
    *,
    db_path: Path = DEFAULT_DB_PATH,
) -> list[str]:
    """All known earnings dates for a symbol, ascending."""
    init_earnings_calendar_tables(db_path)
    with _connect(db_path) as conn:
        rows = conn.execute(
            "SELECT date FROM earnings_calendar WHERE symbol = ? ORDER BY date",
            (symbol.strip().upper(),),
        ).fetchall()
    return [row["date"] for row in rows]


def next_earnings_date(
    symbol: str,
    on_date: str,
    *,
    db_path: Path = DEFAULT_DB_PATH,
) -> str | None:
    """Earliest known earnings date strictly after `on_date` (the FORWARD date)."""
    init_earnings_calendar_tables(db_path)
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT MIN(date) AS d FROM earnings_calendar "
            "WHERE symbol = ? AND date > ?",
            (symbol.strip().upper(), on_date),
        ).fetchone()
    return row["d"] if row and row["d"] else None


def last_earnings_date(
    symbol: str,
    on_date: str,
    *,
    db_path: Path = DEFAULT_DB_PATH,
) -> str | None:
    """Most recent known earnings date on or before `on_date` (the prior event)."""
    init_earnings_calendar_tables(db_path)
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT MAX(date) AS d FROM earnings_calendar "
            "WHERE symbol = ? AND date <= ?",
            (symbol.strip().upper(), on_date),
        ).fetchone()
    return row["d"] if row and row["d"] else None


def earnings_hour(
    symbol: str,
    date: str,
    *,
    db_path: Path = DEFAULT_DB_PATH,
) -> str | None:
    """Finnhub hour for a symbol/date (`bmo` / `amc` / `dmh`), or None if unknown."""
    init_earnings_calendar_tables(db_path)
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT hour FROM earnings_calendar WHERE symbol = ? AND date = ?",
            (symbol.strip().upper(), date),
        ).fetchone()
    if row is None or not row["hour"]:
        return None
    hour = str(row["hour"]).strip().lower()
    return hour or None


def earnings_calendar_count(db_path: Path = DEFAULT_DB_PATH) -> int:
    init_earnings_calendar_tables(db_path)
    with _connect(db_path) as conn:
        return int(conn.execute("SELECT COUNT(*) FROM earnings_calendar").fetchone()[0])
