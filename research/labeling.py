"""Triple-barrier labeling for setup outcomes."""

from __future__ import annotations

import math

from signals.indicators import Bar, atr, closes, highs, lows

# Long positions cannot lose more than 100%; upper cap bounds implausible
# data-artifact tails on timeout exits (no real ≤10-day hold returns >100%).
RETURN_CAP_PCT = 100.0


def triple_barrier_label(
    bars: list[Bar],
    entry_idx: int,
    *,
    upper_atr_mult: float = 2.0,
    lower_atr_mult: float = 2.0,
    max_holding: int = 10,
    atr_window: int = 14,
) -> dict | None:
    """Label a setup using forward bars; features must use only bars[:entry_idx+1]."""
    if entry_idx < 0 or entry_idx >= len(bars):
        return None

    prefix = bars[: entry_idx + 1]
    atr_val = atr(highs(prefix), lows(prefix), closes(prefix), atr_window)
    if atr_val is None:
        return None

    entry_price = bars[entry_idx]["c"]
    if entry_price <= 0 or not math.isfinite(entry_price):
        return None

    upper = entry_price + upper_atr_mult * atr_val
    lower = entry_price - lower_atr_mult * atr_val

    last_j = min(entry_idx + max_holding, len(bars) - 1)
    outcome: str | None = None
    exit_price: float | None = None
    exit_idx: int | None = None

    for j in range(entry_idx + 1, last_j + 1):
        bar = bars[j]
        for key in ("h", "l", "c"):
            price = bar[key]
            if price <= 0 or not math.isfinite(price):
                return None
        hit_upper = bar["h"] >= upper
        hit_lower = bar["l"] <= lower
        if hit_upper and hit_lower:
            outcome = "loss"
            exit_price = lower
            exit_idx = j
            break
        if hit_upper:
            outcome = "win"
            exit_price = upper
            exit_idx = j
            break
        if hit_lower:
            outcome = "loss"
            exit_price = lower
            exit_idx = j
            break

    if outcome is None:
        outcome = "timeout"
        exit_idx = last_j
        exit_price = bars[exit_idx]["c"]

    return_pct = (exit_price / entry_price - 1) * 100
    return_pct = max(-100.0, min(RETURN_CAP_PCT, return_pct))
    label = 1 if return_pct > 0 else 0
    holding_days = exit_idx - entry_idx

    return {
        "outcome": outcome,
        "label": label,
        "return_pct": return_pct,
        "holding_days": holding_days,
        "exit_idx": exit_idx,
        "entry_price": entry_price,
        "upper": upper,
        "lower": lower,
        "atr": atr_val,
    }
