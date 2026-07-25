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
_RTH_CLOSE_MIN = 16 * 60

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
    fetch_start = start - timedelta(days=config.CONTEXT_SLACK_DAYS)
    return (
        datetime(fetch_start.year, fetch_start.month, fetch_start.day, tzinfo=UTC),
        datetime(end.year, end.month, end.day, 23, 59, 59, tzinfo=UTC),
    )


def fetch(window: str) -> dict[str, int]:
    """Pull and cache bars at the profile's timeframe for every symbol. Project venv only."""
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
        if not config.RTH_OPEN_MIN <= minutes < _RTH_CLOSE_MIN:
            continue
        bars.append({"t": ts, "o": o, "h": h, "l": low, "c": c, "v": v, "et": et})
    return bars


def early_close_sessions(bars: list[dict]) -> set:
    """Session dates whose final RTH bar is an after-hours stub, not a real close.

    See config.EARLY_CLOSE_VOLUME_RATIO — a half-day is invisible in the bar COUNT
    because the SIP tape pads the grid, so it is detected by the last bar's volume
    against that session's own median.
    """
    by_session: dict = {}
    for bar in bars:
        by_session.setdefault(bar["et"].date(), []).append(bar["v"])
    stubs = set()
    for day, volumes in by_session.items():
        median = sorted(volumes)[len(volumes) // 2]
        if median > 0 and volumes[-1] < config.EARLY_CLOSE_VOLUME_RATIO * median:
            stubs.add(day)
    return stubs


def eval_indices(bars: list[dict], window: str) -> list[int]:
    """Indices of bars that open an evaluation point.

    Every point needs CONTEXT_BARS of history behind it, HORIZON_BARS ahead of it,
    and a session date inside the window. The two profiles then differ in where the
    forward window is allowed to sit:

    - clock-time profile (5-minute): the point is one of EVAL_TIMES_ET and the whole
      forward window stays inside the SAME session, so no path crosses a gap.
    - session-close profile (nightly): the point is the last RTH bar of its session
      and the forward window is the whole NEXT session, so every path crosses exactly
      one gap. Sessions shorter than HORIZON_BARS bars (half-days) are skipped —
      their forward window would spill into a second session and cross two gaps —
      and so are early closes padded to full length by after-hours prints, whether
      they are the anchor session or the forward one.
    """
    window_start, _ = config.WINDOWS[window]
    wanted = {(h, m) for h, m in config.EVAL_TIMES_ET}
    stubs = early_close_sessions(bars) if config.EVAL_AT_SESSION_CLOSE else set()
    out: list[int] = []
    for i, bar in enumerate(bars):
        if i < config.CONTEXT_BARS or i + config.HORIZON_BARS >= len(bars):
            continue
        et = bar["et"]
        if et.date() < window_start:
            continue
        if config.EVAL_AT_SESSION_CLOSE:
            next_date = bars[i + 1]["et"].date()
            if next_date == et.date():
                continue
            if bars[i + config.HORIZON_BARS]["et"].date() != next_date:
                continue
            if et.date() in stubs or next_date in stubs:
                continue
        else:
            if (et.hour, et.minute) not in wanted:
                continue
            if bars[i + config.HORIZON_BARS]["et"].date() != et.date():
                continue
        out.append(i)
    return out
