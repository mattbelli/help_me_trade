"""Flow-outcome filler: underlying forward returns after each options-flow event.

The founder's calibration rule applied to flow — every event outcome-tracked
mechanically (taken or not), so "do big sweeps mark tops?" becomes a query, not
an anecdote. Fill-only; analysis lives in ``tools/flow_outcomes.py``.

Horizons (underlying price, raw signed returns — sign analysis at query time):
  ret_15m / ret_1h  from the print ts (1Min bars; tape events only — the EOD
                    scan's 21:00-stamped events have no meaningful intraday ts)
  ret_eod           print → that day's regular close (daily bar close)
  ret_1d / ret_5d   event day's close → close 1 / 5 trading days later

Adapter is injected (Alpaca — SIP bars); fail-soft per symbol; an event is
marked ``outcomes_done`` once every horizon that will ever be computable is.
"""

from __future__ import annotations

import logging
from bisect import bisect_left
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from db.options_flow import events_missing_outcomes, stamp_outcomes

logger = logging.getLogger(__name__)

_EOD_TS_SUFFIX = "T21:00:00Z"  # the nightly scan's synthetic stamp
_MAX_BAR_GAP_S = 900  # base/horizon bar must be within 15min of target


def _parse_ts(ts: str) -> datetime | None:
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(UTC)
    except ValueError:
        return None


def _bar_epochs(bars: list[Any]) -> tuple[list[float], list[float]]:
    """(sorted epoch list, close list) — assumes bars sorted asc by time."""
    epochs, closes = [], []
    for b in bars:
        dt = _parse_ts(getattr(b, "time", "") or "")
        if dt is None:
            continue
        epochs.append(dt.timestamp())
        closes.append(float(getattr(b, "close", 0.0)))
    return epochs, closes


def _close_at(epochs: list[float], closes: list[float], target: float) -> float | None:
    """Close of the first bar at/after ``target`` (within _MAX_BAR_GAP_S)."""
    i = bisect_left(epochs, target)
    if i >= len(epochs) or epochs[i] - target > _MAX_BAR_GAP_S:
        return None
    return closes[i]


def _ret(base: float | None, px: float | None) -> float | None:
    if base is None or px is None or base <= 0:
        return None
    return (px - base) / base


def _daily_close_map(daily: list[Any]) -> tuple[list[str], list[float]]:
    """(sorted trading dates, closes) from 1Day bars."""
    dates, closes = [], []
    for b in daily:
        t = (getattr(b, "time", "") or "")[:10]
        if t:
            dates.append(t)
            closes.append(float(getattr(b, "close", 0.0)))
    return dates, closes


def fill_outcomes(
    adapter: Any,
    *,
    now_utc: datetime | None = None,
    limit: int = 2000,
    db_path: Path | None = None,
) -> dict[str, int]:
    """Fill missing outcome horizons for events whose horizons have elapsed."""
    now = now_utc or datetime.now(UTC)
    events = events_missing_outcomes(limit=limit, db_path=db_path)
    if not events:
        return {"events": 0, "stamped": 0, "done": 0}

    # one minute-bars fetch per (symbol, event-day); one daily fetch per symbol
    minute_cache: dict[tuple[str, str], tuple[list[float], list[float]]] = {}
    daily_cache: dict[str, tuple[list[str], list[float]]] = {}
    stamped = done = 0

    for ev in events:
        try:
            ts = _parse_ts(ev["ts"] or "")
            sym = (ev["underlying"] or "").strip().upper()
            if ts is None or not sym:
                stamp_outcomes(ev["id"], done=True, db_path=db_path)
                done += 1
                continue
            day = ts.date().isoformat()
            is_eod_stamp = (ev["ts"] or "").endswith(_EOD_TS_SUFFIX)

            if sym not in daily_cache:
                try:
                    daily_cache[sym] = _daily_close_map(
                        adapter.get_stock_bars(sym, timeframe="1Day", days=30)
                    )
                except Exception:
                    logger.exception("flow outcomes: daily bars failed for %s", sym)
                    daily_cache[sym] = ([], [])
            dates, dcloses = daily_cache[sym]

            # --- daily horizons: event-day close -> +1d / +5d trading closes
            ret_eod = ret_1d = ret_5d = None
            di = bisect_left(dates, day)
            day_close = (
                dcloses[di] if di < len(dates) and dates[di] == day else None
            )
            if day_close is not None:
                if di + 1 < len(dates):
                    ret_1d = _ret(day_close, dcloses[di + 1])
                if di + 5 < len(dates):
                    ret_5d = _ret(day_close, dcloses[di + 5])

            # --- intraday horizons (tape events only)
            ret_15m = ret_1h = None
            if not is_eod_stamp and day_close is not None:
                key = (sym, day)
                if key not in minute_cache:
                    day_start = datetime.fromisoformat(day).replace(tzinfo=UTC)
                    try:
                        minute_cache[key] = _bar_epochs(
                            adapter.get_stock_bars_range(
                                sym,
                                timeframe="1Min",
                                start=day_start,
                                end=day_start + timedelta(days=1),
                            )
                        )
                    except Exception:
                        logger.exception(
                            "flow outcomes: 1Min bars failed for %s %s", sym, day
                        )
                        minute_cache[key] = ([], [])
                epochs, closes = minute_cache[key]
                base = _close_at(epochs, closes, ts.timestamp())
                ret_15m = _ret(base, _close_at(epochs, closes, ts.timestamp() + 900))
                ret_1h = _ret(base, _close_at(epochs, closes, ts.timestamp() + 3600))
                ret_eod = _ret(base, day_close)

            # done once ret_5d is computable-or-never: 5 trading days elapsed OR
            # the daily series simply ends >7 calendar days after the event.
            horizon_elapsed = (now - ts) > timedelta(days=9)
            all_done = ret_5d is not None or horizon_elapsed

            if any(v is not None for v in (ret_15m, ret_1h, ret_eod, ret_1d, ret_5d)):
                stamp_outcomes(
                    ev["id"],
                    ret_15m=ret_15m,
                    ret_1h=ret_1h,
                    ret_eod=ret_eod,
                    ret_1d=ret_1d,
                    ret_5d=ret_5d,
                    done=all_done,
                    db_path=db_path,
                )
                stamped += 1
                if all_done:
                    done += 1
            elif horizon_elapsed:
                # nothing will ever be computable (no bars) — stop re-selecting
                stamp_outcomes(ev["id"], done=True, db_path=db_path)
                done += 1
        except Exception:
            logger.exception("flow outcomes: event %s failed", ev.get("id"))

    return {"events": len(events), "stamped": stamped, "done": done}
