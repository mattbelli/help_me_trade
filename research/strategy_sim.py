"""Scale-out exit path simulation on daily bars."""

from __future__ import annotations

import math

from research.labeling import RETURN_CAP_PCT
from signals.indicators import Bar


def simulate_exit(
    bars: list[Bar],
    entry_idx: int,
    *,
    stop_pct: float = 3.0,
    first_target_pct: float = 3.0,
    first_exit_fraction: float = 0.5,
    runner_target_pct: float = 10.0,
    breakeven_after_first: bool = True,
    max_holding: int = 20,
) -> dict | None:
    """Simulate the scale-out exit from a long entry at bars[entry_idx]['c'].

    Returns None if the setup is invalid (bad bars / no forward data).
    """
    if entry_idx < 0 or entry_idx >= len(bars):
        return None

    entry_price = bars[entry_idx]["c"]
    if entry_price <= 0 or not math.isfinite(entry_price):
        return None

    last_j = min(entry_idx + max_holding, len(bars) - 1)
    if last_j <= entry_idx:
        return None

    stop_price = entry_price * (1 - stop_pct / 100)
    first_price = entry_price * (1 + first_target_pct / 100)
    runner_price = entry_price * (1 + runner_target_pct / 100)
    breakeven_price = entry_price
    # After the first partial, the remainder's stop is either breakeven (the
    # founder's strategy) or the original hard stop (variant for sweeps).
    phase2_stop_price = breakeven_price if breakeven_after_first else stop_price
    phase2_stop_return = 0.0 if breakeven_after_first else -stop_pct
    phase2_stop_outcome = (
        "first_then_breakeven" if breakeven_after_first else "first_then_stop"
    )

    phase = 1
    same_day_ambiguous = False
    hit_first_target = False
    hit_runner = False
    hit_stop = False
    timed_out = False
    outcome: str | None = None
    remainder_return: float | None = None
    exit_j: int | None = None

    for j in range(entry_idx + 1, last_j + 1):
        bar = bars[j]
        for key in ("h", "l", "c"):
            price = bar[key]
            if price <= 0 or not math.isfinite(price):
                return None

        if phase == 1:
            high_ret_pct = (bar["h"] / entry_price - 1) * 100
            low_ret_pct = (bar["l"] / entry_price - 1) * 100
            hit_stop_bar = low_ret_pct <= -stop_pct
            hit_first = high_ret_pct >= first_target_pct
            if hit_stop_bar and hit_first:
                same_day_ambiguous = True
                hit_stop = True
                outcome = "stopped"
                remainder_return = -stop_pct
                exit_j = j
                break
            if hit_stop_bar:
                hit_stop = True
                outcome = "stopped"
                remainder_return = -stop_pct
                exit_j = j
                break
            if hit_first:
                hit_first_target = True
                phase = 2
                continue

        else:
            high_ret_pct = (bar["h"] / entry_price - 1) * 100
            hit_be = bar["l"] <= phase2_stop_price
            hit_runner_bar = high_ret_pct >= runner_target_pct
            if hit_be and hit_runner_bar:
                same_day_ambiguous = True
                outcome = phase2_stop_outcome
                remainder_return = phase2_stop_return
                exit_j = j
                break
            if hit_be:
                outcome = phase2_stop_outcome
                remainder_return = phase2_stop_return
                exit_j = j
                break
            if hit_runner_bar:
                hit_runner = True
                outcome = "first_then_runner"
                remainder_return = runner_target_pct
                exit_j = j
                break

    if outcome is None:
        timed_out = True
        exit_j = last_j
        close_price = bars[exit_j]["c"]
        r_close = (close_price / entry_price - 1) * 100
        r_close = max(-100.0, min(RETURN_CAP_PCT, r_close))
        remainder_return = r_close
        if phase == 1:
            outcome = "timeout"
        else:
            outcome = "first_then_timeout"

    assert exit_j is not None
    assert remainder_return is not None
    assert outcome is not None

    if phase == 1 and outcome in ("stopped", "timeout"):
        blended = remainder_return
    else:
        blended = (
            first_exit_fraction * first_target_pct
            + (1 - first_exit_fraction) * remainder_return
        )

    blended = max(-100.0, min(RETURN_CAP_PCT, blended))
    label = 1 if blended > 0 else 0
    holding_days = exit_j - entry_idx

    return {
        "blended_return_pct": blended,
        "label": label,
        "outcome": outcome,
        "holding_days": holding_days,
        "hit_first_target": hit_first_target,
        "hit_runner": hit_runner,
        "hit_stop": hit_stop,
        "timed_out": timed_out,
        "same_day_ambiguous": same_day_ambiguous,
        "entry_price": entry_price,
        "stop_price": stop_price,
        "first_price": first_price,
        "runner_price": runner_price,
    }
