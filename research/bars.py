"""Daily bar cache — source-agnostic SQLite store for offline research."""

from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from pathlib import Path

from brokers.alpaca import StockBar
from db.base import ROOT, connect
from signals.trade_context import DatedBar, _stockbar_to_dated
from trading.market_hours import last_session_close

logger = logging.getLogger(__name__)

RESEARCH_DATA_DIR = ROOT / "data" / "research"
DEFAULT_BARS_DB_PATH = RESEARCH_DATA_DIR / "bars.db"

_CREATE_DAILY_BARS = """
CREATE TABLE IF NOT EXISTS daily_bars (
    symbol TEXT NOT NULL,
    date TEXT NOT NULL,
    o REAL NOT NULL,
    h REAL NOT NULL,
    l REAL NOT NULL,
    c REAL NOT NULL,
    v REAL NOT NULL,
    source TEXT NOT NULL,
    PRIMARY KEY (symbol, date, source)
)
"""
_CREATE_DAILY_BARS_INDEX = """
CREATE INDEX IF NOT EXISTS idx_daily_bars_symbol_date
    ON daily_bars (symbol, date)
"""


def _ensure_schema(conn) -> None:
    conn.execute(_CREATE_DAILY_BARS)
    conn.execute(_CREATE_DAILY_BARS_INDEX)
    conn.commit()


def _last_complete_session(now: datetime | None = None) -> date:
    """ET date of the most recently CLOSED regular session (half-day aware)."""
    return last_session_close(now or datetime.now(timezone.utc)).date()


def _fetch_alpaca(symbol: str, start: date, end: date) -> list[DatedBar]:
    """Fetch daily bars from Alpaca (dev/testing adapter; Norgate path plugs in via cache_bars)."""
    from brokers.factory import get_broker

    broker = get_broker("alpaca")
    start_dt = datetime(start.year, start.month, start.day, tzinfo=timezone.utc)
    end_dt = datetime(end.year, end.month, end.day, 23, 59, 59, tzinfo=timezone.utc)
    stock_bars: list[StockBar] = broker.get_stock_bars_range(
        symbol, timeframe="1Day", start=start_dt, end=end_dt
    )
    dated = [_stockbar_to_dated(bar) for bar in stock_bars]
    dated.sort(key=lambda b: b["d"])
    return dated


def cache_bars(
    symbol: str,
    bars: list[DatedBar],
    *,
    source: str = "alpaca",
    db_path: Path = DEFAULT_BARS_DB_PATH,
) -> None:
    """Upsert daily bars (used by Alpaca fetch and future Norgate loaders).

    Bars for a session that has not closed yet are DROPPED here rather than at
    each caller: this is the one chokepoint every writer passes through, and a
    stored partial is permanent (nothing re-reads a date the cache already
    covers).
    """
    sym = symbol.strip().upper()
    if not sym or not bars:
        return
    cutoff = _last_complete_session().isoformat()
    bars = [bar for bar in bars if bar["d"] <= cutoff]
    if not bars:
        return
    conn = connect(db_path)
    try:
        _ensure_schema(conn)
        conn.executemany(
            """
            INSERT OR REPLACE INTO daily_bars
                (symbol, date, o, h, l, c, v, source)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    sym,
                    bar["d"],
                    bar["o"],
                    bar["h"],
                    bar["l"],
                    bar["c"],
                    bar["v"],
                    source,
                )
                for bar in bars
            ],
        )
        conn.commit()
    finally:
        conn.close()


def _load_cached(
    symbol: str,
    start: date,
    end: date,
    source: str,
    db_path: Path,
) -> list[DatedBar]:
    conn = connect(db_path)
    try:
        _ensure_schema(conn)
        rows = conn.execute(
            """
            SELECT date, o, h, l, c, v
            FROM daily_bars
            WHERE symbol = ? AND source = ? AND date >= ? AND date <= ?
            ORDER BY date ASC
            """,
            (symbol, source, start.isoformat(), end.isoformat()),
        ).fetchall()
    finally:
        conn.close()
    return [
        {
            "d": row["date"],
            "o": row["o"],
            "h": row["h"],
            "l": row["l"],
            "c": row["c"],
            "v": row["v"],
        }
        for row in rows
    ]


def _needs_fetch(cached: list[DatedBar], start: date, end: date) -> bool:
    if not cached:
        return True
    first = date.fromisoformat(cached[0]["d"])
    last = date.fromisoformat(cached[-1]["d"])
    return first > start or last < end


def cached_symbols(
    source: str = "alpaca",
    *,
    db_path: Path = DEFAULT_BARS_DB_PATH,
) -> list[str]:
    """Return the distinct symbols cached for a given source, sorted."""
    conn = connect(db_path)
    try:
        _ensure_schema(conn)
        rows = conn.execute(
            "SELECT DISTINCT symbol FROM daily_bars WHERE source = ? ORDER BY symbol ASC",
            (source,),
        ).fetchall()
    finally:
        conn.close()
    return [row["symbol"] for row in rows]


def get_daily_bars(
    symbol: str,
    start: date,
    end: date,
    *,
    source: str = "alpaca",
    db_path: Path = DEFAULT_BARS_DB_PATH,
) -> list[DatedBar]:
    """Return cached daily bars in [start, end], fetching and upserting gaps from the source.

    ``end`` is clamped to the last CLOSED session. Mid-session the source
    returns a PARTIAL bar for today — a couple of hours of volume and an
    intraday print as the "close" — and ``INSERT OR REPLACE`` freezes it in the
    store as that day's final bar, which nothing ever revisits (``_needs_fetch``
    sees the date covered). This cache holds completed sessions only; callers
    that need a live price use the quote path, not the daily-bar store.
    """
    sym = symbol.strip().upper()
    end = min(end, _last_complete_session())
    if end < start:
        return []
    cached = _load_cached(sym, start, end, source, db_path)
    if _needs_fetch(cached, start, end):
        if source != "alpaca":
            # Never auto-fetch from Alpaca to fill a gap in a non-Alpaca (e.g. Norgate)
            # source: it would tag survivor-biased data as survivorship-free and silently
            # poison the dataset. Norgate bars are pre-loaded via research.norgate_loader.
            logger.warning(
                "%s: cache gap for source=%s (%s..%s); not auto-fetching. "
                "Pre-load this source's bars first.",
                sym,
                source,
                start,
                end,
            )
            return cached
        logger.info("Fetching %s bars %s..%s (source=%s)", sym, start, end, source)
        fetched = _fetch_alpaca(sym, start, end)
        cache_bars(sym, fetched, source=source, db_path=db_path)
        cached = _load_cached(sym, start, end, source, db_path)
    return cached

