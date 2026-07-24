"""Bar loading and evaluation-point selection.

Split by venv on purpose: ``fetch`` needs the project venv (brokers, Alpaca creds)
and imports product code lazily; everything the ``probe`` command calls reads the
cached SQLite table with the standard library only. That is what lets the probe run
under `.venv-kronos` without the app's dependency tree — and it keeps the isolation
promise in the README honest rather than aspirational.
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from research.kronos_probe import config

logger = logging.getLogger(__name__)

_ET = ZoneInfo("America/New_York")
_RTH_OPEN_MIN = 9 * 60 + 30
_RTH_CLOSE_MIN = 16 * 60

# Calendar slack before the window start so the first evaluation point already has
# CONTEXT_BARS of RTH history behind it (512 RTH 5-min bars ~ 6.5 sessions).
_CONTEXT_SLACK_DAYS = 20

# Mirrors research.intraday_bars.DEFAULT_INTRADAY_DB_PATH without importing it.
_DB_PATH = config.OUTPUT_DIR.parent / "intraday_bars.db"


def to_et(ts: str) -> datetime:
    text = ts.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(_ET)


def _window_bounds(window: str) -> tuple[datetime, datetime]:
    start, end = config.WINDOWS[window]
    fetch_start = start - timedelta(days=_CONTEXT_SLACK_DAYS)
    return (
        datetime(fetch_start.year, fetch_start.month, fetch_start.day, tzinfo=UTC),
        datetime(end.year, end.month, end.day, 23, 59, 59, tzinfo=UTC),
    )


def fetch(window: str) -> dict[str, int]:
    """Pull and cache 5-minute bars for every probe symbol. Project venv only."""
    from research.intraday_bars import get_minute_bars

    start, end = _window_bounds(window)
    counts: dict[str, int] = {}
    for symbol in config.SYMBOLS:
        bars = get_minute_bars(
            symbol, start, end, source="alpaca", timeframe=config.TIMEFRAME
        )
        counts[symbol] = len(bars)
        logger.info("%s: %d bars cached", symbol, len(bars))
    return counts


def load_rth(symbol: str, window: str) -> list[dict]:
    """Cached bars for the window, RTH only, ascending. Reads SQLite; no network."""
    start, end = _window_bounds(window)
    conn = sqlite3.connect(f"file:{_DB_PATH}?mode=ro", uri=True)
    try:
        rows = conn.execute(
            """
            SELECT ts, o, h, l, c, v
            FROM minute_bars
            WHERE symbol = ? AND source = 'alpaca' AND timeframe = ?
              AND ts >= ? AND ts <= ?
            ORDER BY ts
            """,
            (
                symbol.upper(),
                config.TIMEFRAME,
                start.isoformat().replace("+00:00", "Z"),
                end.isoformat().replace("+00:00", "Z"),
            ),
        ).fetchall()
    finally:
        conn.close()

    bars: list[dict] = []
    for ts, o, h, low, c, v in rows:
        et = to_et(ts)
        minutes = et.hour * 60 + et.minute
        if not _RTH_OPEN_MIN <= minutes < _RTH_CLOSE_MIN:
            continue
        bars.append({"t": ts, "o": o, "h": h, "l": low, "c": c, "v": v, "et": et})
    return bars


def eval_indices(bars: list[dict], window: str) -> list[int]:
    """Indices of bars that open an evaluation point.

    A point qualifies when its ET clock time is one of the pre-registered times, it
    has CONTEXT_BARS of history behind it, HORIZON_BARS ahead of it, and the whole
    forward window stays inside the same session (no overnight gap inside a path).
    """
    window_start, _ = config.WINDOWS[window]
    wanted = {(h, m) for h, m in config.EVAL_TIMES_ET}
    out: list[int] = []
    for i, bar in enumerate(bars):
        if i < config.CONTEXT_BARS or i + config.HORIZON_BARS >= len(bars):
            continue
        et = bar["et"]
        if (et.hour, et.minute) not in wanted or et.date() < window_start:
            continue
        if bars[i + config.HORIZON_BARS]["et"].date() != et.date():
            continue
        out.append(i)
    return out
