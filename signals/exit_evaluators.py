"""Pure per-strategy exit evaluators — single-cycle hold / scale / exit decisions."""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from research.strategy_features import _trading_days_until
from trading.market_hours import is_after_close, is_market_open, session_close_time

_ET = ZoneInfo("America/New_York")
# The final-session exit fires inside the LAST N minutes of the session — late
# enough to fill near the close the backtest models, early enough to leave ~45
# loop passes. SESSION-RELATIVE, not a fixed 15:45: on a half-day (13:00 ET
# close) a fixed window opens 2h45m AFTER the close, the day-TIF order queues to
# the NEXT open — the print day for a bmo reporter — and the position holds
# through the announcement (board: market-hours-half-day-early-close).
_EXIT_WINDOW_LEAD_MIN = 15


@dataclass(frozen=True)
class ExitDecision:
    action: str  # "hold" | "scale" | "exit"
    reason: str
    exit_fraction: float = 0.0
    new_high_water_mark: float | None = None
    new_first_target_hit: bool | None = None
    detail: dict[str, Any] = field(default_factory=dict)


def evaluate_dip_exit(
    *,
    entry_price: float,
    current_price: float,
    bar_high: float | None = None,
    bar_low: float | None = None,
    high_water_mark: float | None,
    first_target_hit: bool,
    holding_days: int,
    params: dict[str, Any],
) -> ExitDecision:
    if entry_price <= 0 or not math.isfinite(entry_price):
        return ExitDecision("hold", "hold", detail={"error": "bad_entry_price"})

    stop_pct = params.get("stop_pct", -3.0)
    first_target_pct = params.get("first_target_pct", 3.0)
    trail_pct = params.get("trail_pct", 11.0)
    max_holding = int(params.get("max_holding_days", 20))
    first_exit_fraction = params.get("first_exit_fraction", 0.5)
    breakeven_price = entry_price

    bar_high = bar_high if bar_high is not None else current_price
    bar_low = bar_low if bar_low is not None else current_price

    high_ret = (bar_high / entry_price - 1) * 100
    low_ret = (bar_low / entry_price - 1) * 100

    base_detail: dict[str, Any] = {
        "entry_price": entry_price,
        "current_price": current_price,
        "bar_high": bar_high,
        "bar_low": bar_low,
        "high_ret": high_ret,
        "low_ret": low_ret,
        "holding_days": holding_days,
        "first_target_hit": first_target_hit,
    }

    if holding_days >= max_holding:
        return ExitDecision(
            "exit",
            "time_cap",
            exit_fraction=1.0,
            detail={**base_detail, "max_holding_days": max_holding},
        )

    if not first_target_hit:
        if stop_pct is not None:
            stop_pct_mag = abs(stop_pct)
            if low_ret <= -stop_pct_mag:
                return ExitDecision(
                    "exit",
                    "stop",
                    exit_fraction=1.0,
                    detail={
                        **base_detail,
                        "stop_pct_mag": stop_pct_mag,
                        "stop_threshold_ret": -stop_pct_mag,
                    },
                )
        if first_target_pct is not None and high_ret >= first_target_pct:
            return ExitDecision(
                "scale",
                "first_target",
                exit_fraction=first_exit_fraction,
                new_first_target_hit=True,
                new_high_water_mark=bar_high,
                detail={
                    **base_detail,
                    "first_target_pct": first_target_pct,
                    "first_exit_fraction": first_exit_fraction,
                },
            )
        return ExitDecision("hold", "hold", detail=base_detail)

    hwm = high_water_mark if high_water_mark is not None else bar_high

    if trail_pct is None:
        new_hwm = max(hwm, bar_high)
        return ExitDecision(
            "hold",
            "hold",
            new_high_water_mark=new_hwm if new_hwm > hwm else None,
            detail={**base_detail, "high_water_mark": hwm},
        )

    trail_stop = hwm * (1 - trail_pct / 100)
    effective_stop = max(breakeven_price, trail_stop)

    phase2_detail = {
        **base_detail,
        "high_water_mark": hwm,
        "trail_pct": trail_pct,
        "trail_stop": trail_stop,
        "breakeven_price": breakeven_price,
        "effective_stop": effective_stop,
    }

    if bar_low <= effective_stop:
        reason = "breakeven" if effective_stop == breakeven_price else "trail"
        return ExitDecision(
            "exit",
            reason,
            exit_fraction=1.0,
            detail=phase2_detail,
        )

    new_hwm = max(hwm, bar_high)
    return ExitDecision(
        "hold",
        "hold",
        new_high_water_mark=new_hwm,
        detail=phase2_detail,
    )


def _et_now(stamp: str) -> datetime | None:
    """`stamp` as a tz-aware America/New_York instant, or None if unknowable.

    A naive stamp (e.g. a date-only `"2026-01-06"`) has no knowable ET offset, so
    neither its ET calendar date nor any session judgement can be derived from it.
    Callers must treat None as "no intraday clock", never as "before close".
    """
    try:
        dt = datetime.fromisoformat(stamp)
    except ValueError:
        return None
    return dt.astimezone(_ET) if dt.utcoffset() is not None else None


def evaluate_earnings_exit(
    *,
    today: str,
    earnings_dates: list[str],
    holding_days: int | None = None,
    params: dict[str, Any],
    hour_for: Callable[[str], str | None] | None = None,
) -> ExitDecision:
    """Earnings-drift exit: never hold through a print; time the fill the backtest models.

    All dates are ET calendar dates. `today` arrives as UTC (`now_iso()`), whose date has
    already rolled over by 20:00 ET — reading it as the trading date fires the exit a full
    session early and forfeits a day of drift.

    `hour_for(date) -> "bmo" | "amc" | "dmh" | None` resolves the forward event's session.
    Rules, where `td` is trading days to the print:

    * `td == 0` (report day) — EXIT unconditionally. The backstop: an `amc` print is still
      ahead, a `bmo` print already happened and this is damage control.
    * `td == 1`, `amc` — EXIT only once the session has closed. The print lands after the
      close, so the position holds through it and the `day` order the broker queues fills
      at the report day's OPEN, capturing the overnight gap that carries most of the edge
      (PC 2010-2026: +0.227%/trade conditional on `amc`).
    * `td == 1`, anything else — EXIT only inside the LAST 15 MINUTES of the session
      (15:45-16:00 ET on a full day, 12:45-13:00 on a half-day close), filling near the
      close as the backtest does. It must NOT fire after the close: a queued `day` order
      releases at the report day's open, which for a `bmo` reporter is AFTER its print.
    * `td > 1` — hold.

    FAILS CLOSED: anything that is not exactly `"amc"` (None, other sessions, an unknown
    hour, a raising `hour_for`) takes the non-`amc` path. A wrong `amc` means holding
    through the print, the event this sleeve exists to dodge.
    """
    exit_lead_td = int(params.get("exit_lead_td", 1))
    et_now = _et_now(today)
    today_key = et_now.date().isoformat() if et_now is not None else today[:10]

    future = sorted(d for d in earnings_dates if d and d[:10] >= today_key)
    next_e: str | None = future[0] if future else None
    td: int | None = None
    session: str | None = None
    rule: str | None = None

    if next_e is not None:
        if hour_for is not None:
            try:
                session = hour_for(next_e)
            except Exception:
                session = None
        td = _trading_days_until(today_key, next_e)

        if td <= 0:
            # Report day — out regardless of session, the backstop for a missed window.
            # td < 0 needs dates that will not parse; exiting is the safe read of that too.
            rule, should_exit = "report_day", True
        elif td <= exit_lead_td:
            if et_now is None:
                # No intraday clock: fall back to the date-granularity rule, which exits
                # on the lead day rather than risking a hold into the print.
                rule, should_exit = "lead_td", True
            elif session == "amc":
                rule, should_exit = "amc_after_close", is_after_close(et_now)
            else:
                rule = "lead_td_close"
                close_h, close_m = session_close_time(et_now.date())
                window_open = close_h * 60 + close_m - _EXIT_WINDOW_LEAD_MIN
                should_exit = (
                    is_market_open(et_now)
                    and et_now.hour * 60 + et_now.minute >= window_open
                )
        else:
            rule, should_exit = "hold_until_lead", False

        if should_exit:
            return ExitDecision(
                "exit",
                "earnings",
                exit_fraction=1.0,
                detail={
                    "next_earnings": next_e,
                    "td_to_earnings": td,
                    "exit_lead_td": exit_lead_td,
                    "session": session,
                    "exit_rule": rule,
                },
            )

    max_holding_raw = params.get("max_holding_days")
    if (
        max_holding_raw is not None
        and holding_days is not None
        and holding_days >= int(max_holding_raw)
    ):
        detail: dict[str, Any] = {
            "next_earnings": next_e,
            "max_holding_days": int(max_holding_raw),
            "holding_days": holding_days,
        }
        if td is not None:
            detail["td_to_earnings"] = td
        return ExitDecision("exit", "time_cap", exit_fraction=1.0, detail=detail)

    detail = {"next_earnings": next_e}
    if td is not None:
        detail["td_to_earnings"] = td
        # A hold at td == 1 is a decision, not an absence of one: record why.
        detail["session"] = session
        detail["exit_rule"] = rule
    return ExitDecision("hold", "hold", detail=detail)


def evaluate_vix_exit(
    *,
    current_vix: float | None,
    holding_days: int,
    params: dict[str, Any],
) -> ExitDecision:
    """SPY vix-spike sleeve: exit on VIX reversion (< vix_exit_below) or a max trading-day hold.

    Exit precedence: the time cap is checked FIRST and fires UNCONDITIONALLY (even with no VIX
    data) — the hard backstop against an open-forever position during a FRED outage. Below the
    cap, missing VIX ⇒ HOLD (never sell the reversion leg on absent/stale data)."""
    max_holding = int(params.get("max_holding_days", 60))
    vix_exit_below = params.get("vix_exit_below", 20.0)
    base_detail = {"current_vix": current_vix, "holding_days": holding_days}

    if holding_days >= max_holding:
        return ExitDecision(
            "exit",
            "time_cap",
            exit_fraction=1.0,
            detail={**base_detail, "max_holding_days": max_holding},
        )
    if current_vix is None:
        return ExitDecision("hold", "hold", detail={**base_detail, "error": "no_vix"})
    if vix_exit_below is not None and current_vix < float(vix_exit_below):
        return ExitDecision(
            "exit",
            "vix_reversion",
            exit_fraction=1.0,
            detail={**base_detail, "vix_exit_below": vix_exit_below},
        )
    return ExitDecision("hold", "hold", detail=base_detail)
