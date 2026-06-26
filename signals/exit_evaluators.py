"""Pure per-strategy exit evaluators — single-cycle hold / scale / exit decisions."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from research.strategy_features import _trading_days_until


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

    stop_pct_mag = abs(params.get("stop_pct", -3.0))
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
        if high_ret >= first_target_pct:
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


def evaluate_earnings_exit(
    *,
    today: str,
    earnings_dates: list[str],
    holding_days: int | None = None,
    params: dict[str, Any],
) -> ExitDecision:
    exit_lead_td = int(params.get("exit_lead_td", 1))
    today_key = today[:10]

    future = sorted(d for d in earnings_dates if d and d[:10] > today_key)
    next_e: str | None = future[0] if future else None
    td: int | None = None

    if next_e is not None:
        td = _trading_days_until(today, next_e)
        if 0 <= td <= exit_lead_td:
            return ExitDecision(
                "exit",
                "earnings",
                exit_fraction=1.0,
                detail={
                    "next_earnings": next_e,
                    "td_to_earnings": td,
                    "exit_lead_td": exit_lead_td,
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
    return ExitDecision("hold", "hold", detail=detail)
