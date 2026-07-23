"""Live earnings calendar — recent + upcoming earnings dates per symbol.

Populated from Finnhub on a daily schedule (see signals/earnings_refresh.py). The
earnings-drift strategy reads it at scan time: the LAST earnings date (to score the
prior reaction) and the NEXT earnings date (to time the pre-earnings entry/exit).

Separate from the research `earnings.db` (EDGAR 8-K history used for backtests) —
this table is the *live forward-looking* feed the running scan depends on.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from datetime import date as _date
from pathlib import Path
from typing import Any

from db.base import DEFAULT_DB_PATH
from db.base import connect as _connect
from db.base import now_iso as _now

logger = logging.getLogger(__name__)


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


# A past-dated vendor row is only a phantom if the vendor abandoned it well BEFORE
# the event: real dates keep getting restamped up to the event day. 3 days absorbs
# one missed daily refresh + the UTC-stamp-vs-ET-event-date skew, so a real event
# whose refresh hiccuped the day before can never be pruned at its own boundary
# (Alpha Vantage is forward-only and drops a date the moment it becomes today —
# without this margin an AMC report could vanish on its print day and blind the
# exit engine).
_PAST_ABANDON_MARGIN_DAYS = 3

# Blast-radius cap: a normal day withdraws ~1% of in-window vendor rows (first-ever
# run: the accumulated backlog measured 48%). Anything past 60% means the confirmed
# set is broken (truncated vendor response), not that half the calendar moved.
_PRUNE_MAX_FRACTION = 0.6
_PRUNE_SMALL_COUNT = 25  # tiny universes: absolute counts this small are never a wipe


def prune_withdrawn_estimates(
    symbols: list[str],
    confirmed: set[tuple[str, str]],
    *,
    start: str,
    end: str,
    today: str | None = None,
    db_path: Path = DEFAULT_DB_PATH,
) -> int:
    """Delete vendor estimate rows that no current fetch asserts anymore.

    Vendors move earnings estimates and silently withdraw stale ones; the
    append-only upsert fossilized withdrawn dates as phantom events (GOOGL kept
    showing a Finnhub 07-28 "next earnings" after the real 07-22 report).
    `confirmed` is every (symbol, date) the CURRENT refresh returned; a non-edgar
    row inside the fetched [start, end] window that isn't in it was withdrawn:
      - future rows: deleted (the vendor stopped listing the estimate);
      - today's rows: never touched (a vendor dropping a same-day date is its
        forward-only window rolling over, and EDGAR's confirmation lands later);
      - past rows: deleted only if last asserted ≥ _PAST_ABANDON_MARGIN_DAYS
        before the event date (abandoned-before-event = phantom; dropped-after
        = a rolling window forgetting real history, which EDGAR owns).

    edgar rows are never touched. Note the source column can't distinguish
    vendors (Alpha Vantage rows default to 'finnhub' in the upsert), so callers
    must build `confirmed` from ALL vendor fetches and skip pruning entirely if
    any configured vendor failed — an empty-but-200 vendor response must count
    as a failure, or a broken feed reads as everything-withdrawn.
    """
    init_earnings_calendar_tables(db_path)
    universe = {s.strip().upper() for s in symbols if s and s.strip()}
    if not universe:
        return 0
    confirmed_norm = {
        (str(s).strip().upper(), str(d).strip()) for s, d in confirmed
    }
    today_d = (
        _date.fromisoformat(today) if today else datetime.now(UTC).date()
    )
    with _connect(db_path) as conn:
        rows = conn.execute(
            "SELECT symbol, date, fetched_at FROM earnings_calendar "
            "WHERE source != 'edgar' AND date >= ? AND date <= ?",
            (start, end),
        ).fetchall()
        candidates = [r for r in rows if r["symbol"] in universe]
        withdrawn: list[tuple[str, str]] = []
        for r in candidates:
            if (r["symbol"], r["date"]) in confirmed_norm:
                continue
            try:
                event_d = _date.fromisoformat(str(r["date"])[:10])
                stamp_d = _date.fromisoformat(str(r["fetched_at"])[:10])
            except ValueError:
                continue  # unparsable — keep, never guess a deletion
            if event_d > today_d or (
                event_d < today_d
                and (event_d - stamp_d).days >= _PAST_ABANDON_MARGIN_DAYS
            ):
                withdrawn.append((r["symbol"], r["date"]))
        if (
            len(withdrawn) > _PRUNE_SMALL_COUNT
            and len(withdrawn) > _PRUNE_MAX_FRACTION * len(candidates)
        ):
            logger.warning(
                "Earnings prune aborted: %d of %d in-window vendor rows read as "
                "withdrawn — confirmed set looks broken, keeping everything",
                len(withdrawn),
                len(candidates),
            )
            return 0
        if withdrawn:
            conn.executemany(
                "DELETE FROM earnings_calendar WHERE symbol = ? AND date = ?",
                withdrawn,
            )
            conn.commit()
    return len(withdrawn)


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
