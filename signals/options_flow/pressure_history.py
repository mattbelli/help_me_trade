"""Daily index-pressure trend — per-day gauge tilt joined with the day's tape.

One row per session: the open-window and close-window net premium tilt (summed
across all gauge underlyings) plus how SPY actually finished that day
(close-vs-prior-close %). Feeds /api/index-pressure/history, the widget's
trend view, and the perception resource's ``history`` filter.

Honesty: the join is CO-OCCURRENCE display — tilt next to result so drift
toward bull/bear is visible over weeks — never an accuracy score, never a
prediction. A missing spy_pct (today mid-session, bars gap) stays None.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from db.base import DEFAULT_DB_PATH
from db.index_pressure import daily_history

logger = logging.getLogger(__name__)


def _spy_day_returns(dates: list[str]) -> dict[str, float]:
    """date -> SPY close-vs-prior-close % for the given ET dates. Fail-soft:
    a bars outage returns {} and the trend renders without day results."""
    if not dates:
        return {}
    try:
        from research.bars import get_daily_bars

        start = date.fromisoformat(min(dates)) - timedelta(days=10)
        bars = get_daily_bars("SPY", start, date.fromisoformat(max(dates)))
    except Exception:  # noqa: BLE001 — perception-side join, never load-bearing
        logger.warning("pressure_trend: SPY daily bars unavailable", exc_info=True)
        return {}
    out: dict[str, float] = {}
    prev_close: float | None = None
    for bar in bars:
        close = float(bar["c"])
        if prev_close and prev_close > 0:
            out[str(bar["d"])] = round((close / prev_close - 1.0) * 100.0, 2)
        prev_close = close
    return out


def pressure_trend(
    days: int = 40, *, db_path: Path = DEFAULT_DB_PATH
) -> list[dict[str, Any]]:
    """Merged per-day rows, oldest first: {date, open, close, spy_pct}."""
    by_date: dict[str, dict[str, Any]] = {}
    for window in ("open", "close"):
        for row in daily_history(window=window, days=days, db_path=db_path):
            d = row.pop("date")
            by_date.setdefault(d, {"date": d, "open": None, "close": None})[window] = row
    spy = _spy_day_returns(list(by_date))
    return [
        {**by_date[d], "spy_pct": spy.get(d)} for d in sorted(by_date)[-max(days, 1):]
    ]
