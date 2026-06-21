"""Scale-out exit path simulation on daily bars."""

from __future__ import annotations

import math

from research.labeling import RETURN_CAP_PCT
from signals.indicators import Bar


def _short_return_pct(entry_price: float, exit_price: float) -> float:
    """Short P&L in percent: profit when exit_price < entry_price."""
    return (entry_price - exit_price) / entry_price * 100


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
    trail_pct: float | None = None,
    side: str = "long",
) -> dict | None:
    """Simulate the scale-out exit from an entry at bars[entry_idx]['c'].

    ``side="long"``: buy at entry, profit when price rises (default).
    ``side="short"``: sell at entry, profit when price falls (research only).

    Returns None if the setup is invalid (bad bars / no forward data).
    """
    if side == "short":
        return _simulate_exit_short(
            bars,
            entry_idx,
            stop_pct=stop_pct,
            first_target_pct=first_target_pct,
            first_exit_fraction=first_exit_fraction,
            runner_target_pct=runner_target_pct,
            breakeven_after_first=breakeven_after_first,
            max_holding=max_holding,
            trail_pct=trail_pct,
        )

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
    hit_trail = False
    timed_out = False
    outcome: str | None = None
    remainder_return: float | None = None
    exit_j: int | None = None
    first_target_bar_idx: int | None = None
    peak_high: float | None = None

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
                first_target_bar_idx = j
                phase = 2
                continue

        else:
            if trail_pct is not None:
                # Trailing stop on the runner: peak uses only bars through j-1
                # (no same-bar look-ahead between peak high and stop check).
                if peak_high is None:
                    assert first_target_bar_idx is not None
                    peak_high = bars[first_target_bar_idx]["h"]
                trail_stop = peak_high * (1 - trail_pct / 100)
                effective_stop = max(breakeven_price, trail_stop)
                if bar["l"] <= effective_stop:
                    hit_trail = True
                    outcome = "first_then_trail"
                    remainder_return = (effective_stop / entry_price - 1) * 100
                    exit_j = j
                    break
                peak_high = max(peak_high, bar["h"])
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
        "hit_trail": hit_trail,
        "timed_out": timed_out,
        "same_day_ambiguous": same_day_ambiguous,
        "entry_price": entry_price,
        "stop_price": stop_price,
        "first_price": first_price,
        "runner_price": runner_price,
        "side": side,
    }


def _simulate_exit_short(
    bars: list[Bar],
    entry_idx: int,
    *,
    stop_pct: float,
    first_target_pct: float,
    first_exit_fraction: float,
    runner_target_pct: float,
    breakeven_after_first: bool,
    max_holding: int,
    trail_pct: float | None,
) -> dict | None:
    if entry_idx < 0 or entry_idx >= len(bars):
        return None

    entry_price = bars[entry_idx]["c"]
    if entry_price <= 0 or not math.isfinite(entry_price):
        return None

    last_j = min(entry_idx + max_holding, len(bars) - 1)
    if last_j <= entry_idx:
        return None

    stop_price = entry_price * (1 + stop_pct / 100)
    first_price = entry_price * (1 - first_target_pct / 100)
    runner_price = entry_price * (1 - runner_target_pct / 100)
    breakeven_price = entry_price
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
    hit_trail = False
    timed_out = False
    outcome: str | None = None
    remainder_return: float | None = None
    exit_j: int | None = None
    first_target_bar_idx: int | None = None
    trough_low: float | None = None

    for j in range(entry_idx + 1, last_j + 1):
        bar = bars[j]
        for key in ("h", "l", "c"):
            price = bar[key]
            if price <= 0 or not math.isfinite(price):
                return None

        if phase == 1:
            hit_stop_bar = bar["h"] >= stop_price
            hit_first = bar["l"] <= first_price
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
                first_target_bar_idx = j
                phase = 2
                continue

        else:
            if trail_pct is not None:
                # Trailing stop on the runner: trough uses only bars through j-1
                # (no same-bar look-ahead between trough low and stop check).
                if trough_low is None:
                    assert first_target_bar_idx is not None
                    trough_low = bars[first_target_bar_idx]["l"]
                trail_stop = trough_low * (1 + trail_pct / 100)
                effective_stop = min(breakeven_price, trail_stop)
                if bar["h"] >= effective_stop:
                    hit_trail = True
                    outcome = "first_then_trail"
                    remainder_return = _short_return_pct(entry_price, effective_stop)
                    exit_j = j
                    break
                trough_low = min(trough_low, bar["l"])
            else:
                hit_be = bar["h"] >= phase2_stop_price
                hit_runner_bar = bar["l"] <= runner_price
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
        r_close = _short_return_pct(entry_price, close_price)
        # The -100 floor understates short-squeeze losses: a long cannot lose
        # more than 100%, but a short can (gap-ups can also blow through the
        # stop). This is a known optimistic bias for short backtests.
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
        "hit_trail": hit_trail,
        "timed_out": timed_out,
        "same_day_ambiguous": same_day_ambiguous,
        "entry_price": entry_price,
        "stop_price": stop_price,
        "first_price": first_price,
        "runner_price": runner_price,
        "side": "short",
    }
