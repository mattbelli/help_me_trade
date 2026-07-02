"""Point-in-time strategy entry predicate and SPY market-context features."""

from __future__ import annotations

import bisect
from datetime import date, timedelta

from research.levels import level_features
from research.volume_profile import compute_profile_features
from signals.indicators import (
    closes,
    distance_from_high,
    pct_change,
    rsi,
    sma,
    volume_vs_avg,
    volumes,
)
from signals.trade_context import (
    HIGH_LOOKBACK,
    MIN_BARS_FOR_CONTEXT,
    SMA50_PERIOD,
    SMA200_PERIOD,
    DatedBar,
)


def _price_vs_ma(close: float, ma: float | None) -> str | None:
    if ma is None:
        return None
    return "above" if close >= ma else "below"


def compute_market_context(spy_bars: list[DatedBar], spy_idx: int) -> dict | None:
    """Build SPY market-context features using only spy_bars[:spy_idx+1] (no look-ahead)."""
    if spy_idx < 0 or spy_idx >= len(spy_bars):
        return None

    prefix = spy_bars[: spy_idx + 1]
    if len(prefix) < MIN_BARS_FOR_CONTEXT:
        return None

    c = closes(prefix)
    close = c[-1]
    sma50 = sma(c, SMA50_PERIOD)
    sma200 = sma(c, SMA200_PERIOD)

    features: dict[str, float | str] = {
        "mkt_ret_1d": pct_change(c, 1),
        "mkt_ret_3d": pct_change(c, 3),
        "mkt_ret_5d": pct_change(c, 5),
        "mkt_rsi_14": rsi(c, 14),
        "mkt_dist_from_high_252": distance_from_high(c, HIGH_LOOKBACK),
        "mkt_vs_sma50": _price_vs_ma(close, sma50),
        "mkt_vs_sma200": _price_vs_ma(close, sma200),
    }

    return {k: v for k, v in features.items() if v is not None}


def is_strategy_entry(
    bars: list[DatedBar],
    idx: int,
    *,
    spy_bars: list[DatedBar],
    spy_idx: int,
    healthy_required: bool = True,
    oversold_3d_max: float = -5.0,
    mkt_down_3d_max: float = -1.5,
    require_capitulation: bool = False,
    cap_day_max: float = -2.0,
    cap_vol_min: float = 1.5,
    side: str = "long",
    far_support_min: float | None = None,
    require_profile_support: bool = False,
    profile_window: int = 60,
    profile_n_bins: int = 100,
    profile_node_tol_pct: float = 0.25,
) -> bool:
    """Return True when all active strategy entry conditions hold at idx (no look-ahead).

    Threshold params use a long-centric sign convention (negative = down / oversold).
    For ``side="short"``, the same magnitudes are sign-flipped: overbought stock and
    market require returns >= -oversold_3d_max and >= -mkt_down_3d_max respectively;
    capitulation requires a big UP day (ret_1d >= -cap_day_max).
    """
    if idx < 0 or idx >= len(bars):
        return False

    prefix = bars[: idx + 1]
    if len(prefix) < MIN_BARS_FOR_CONTEXT:
        return False

    c = closes(prefix)
    close = c[-1]

    if healthy_required:
        sma200 = sma(c, SMA200_PERIOD)
        if sma200 is None:
            return False
        if side == "short":
            # Short rips in downtrends; avoid shorting strong uptrends.
            if close >= sma200:
                return False
        elif close <= sma200:
            return False

    ret_3d = pct_change(c, 3)
    if side == "short":
        stock_min_3d = -oversold_3d_max
        if ret_3d is None or ret_3d < stock_min_3d:
            return False
    elif ret_3d is None or ret_3d > oversold_3d_max:
        return False

    mkt_ctx = compute_market_context(spy_bars, spy_idx)
    if mkt_ctx is None:
        return False
    mkt_ret_3d = mkt_ctx.get("mkt_ret_3d")
    if side == "short":
        mkt_min_3d = -mkt_down_3d_max
        if mkt_ret_3d is None or mkt_ret_3d < mkt_min_3d:
            return False
    elif mkt_ret_3d is None or mkt_ret_3d > mkt_down_3d_max:
        return False

    if require_capitulation:
        ret_1d = pct_change(c, 1)
        if side == "short":
            if ret_1d is None or ret_1d < -cap_day_max:
                return False
        elif ret_1d is None or ret_1d > cap_day_max:
            return False
        vol_ratio = volume_vs_avg(volumes(prefix), 20)
        if vol_ratio is None or vol_ratio < cap_vol_min:
            return False

    if far_support_min is not None:
        feats = level_features(bars, idx)
        dist = feats.get("dist_to_support_pct")
        if dist is None or dist < far_support_min:
            return False

    if require_profile_support:
        prof = compute_profile_features(
            bars,
            idx,
            window=profile_window,
            n_bins=profile_n_bins,
            node_tol_pct=profile_node_tol_pct,
        )
        if prof.get("poc") is None:
            return False
        if not (prof["at_hvn_support"] or prof["below_value"]):
            return False

    return True


def is_profile_reversion_entry(
    bars: list[DatedBar],
    idx: int,
    *,
    spy_bars: list[DatedBar],
    spy_idx: int,
    window: int = 60,
    n_bins: int = 100,
    node_tol_pct: float = 0.25,
    require_market_down: bool = False,
) -> bool:
    """Return True when profile-reversion long entry conditions hold at idx (no look-ahead).

    Entry when price is below value AND at a low-volume node or poor low (structural
    low expected to revert toward POC). Optionally requires SPY market-down context.
    """
    if idx < 0 or idx >= len(bars):
        return False

    feats = compute_profile_features(
        bars,
        idx,
        window=window,
        n_bins=n_bins,
        node_tol_pct=node_tol_pct,
    )
    if feats.get("poc") is None:
        return False

    entry = feats["below_value"] and (feats["at_lvn"] or feats["is_poor_low"])
    if not entry:
        return False

    if require_market_down:
        mkt_ctx = compute_market_context(spy_bars, spy_idx)
        if mkt_ctx is None:
            return False
        mkt_ret_3d = mkt_ctx.get("mkt_ret_3d")
        if mkt_ret_3d is None or mkt_ret_3d >= 0:
            return False

    return True


def is_rsi2_entry(
    bars: list[DatedBar],
    idx: int,
    *,
    spy_bars: list[DatedBar],
    spy_idx: int,
    rsi_period: int = 2,
    rsi_max: float = 10.0,
    trend_required: bool = True,
) -> bool:
    """Return True when Connors RSI-2 mean-reversion entry conditions hold at idx (no look-ahead)."""
    if idx < 0 or idx >= len(bars):
        return False

    prefix = bars[: idx + 1]
    if len(prefix) < MIN_BARS_FOR_CONTEXT:
        return False

    c = closes(prefix)
    close = c[-1]

    if trend_required:
        sma200 = sma(c, SMA200_PERIOD)
        if sma200 is None or close <= sma200:
            return False

    rsi_val = rsi(c, rsi_period)
    if rsi_val is None or rsi_val > rsi_max:
        return False

    return True


def is_consec_down_entry(
    bars: list[DatedBar],
    idx: int,
    *,
    spy_bars: list[DatedBar],
    spy_idx: int,
    down_days: int = 3,
    trend_required: bool = True,
) -> bool:
    """Return True when consecutive-down-closes mean-reversion entry conditions hold at idx."""
    if idx < 0 or idx >= len(bars):
        return False

    prefix = bars[: idx + 1]
    if len(prefix) < MIN_BARS_FOR_CONTEXT:
        return False

    if down_days < 1 or idx < down_days - 1:
        return False

    c = closes(prefix)
    close = c[-1]

    if trend_required:
        sma200 = sma(c, SMA200_PERIOD)
        if sma200 is None or close <= sma200:
            return False

    for step in range(1, down_days):
        if c[idx - step + 1] >= c[idx - step]:
            return False

    return True


def is_momentum_entry(
    bars: list[DatedBar],
    idx: int,
    *,
    spy_bars: list[DatedBar],
    spy_idx: int,
    trend_required: bool = True,
    near_high_max: float = -2.0,
    mom_21d_min: float = 5.0,
    require_market_uptrend: bool = True,
    vol_min: float = 1.2,
) -> bool:
    """Return True when all active momentum/breakout entry conditions hold at idx (no look-ahead)."""
    if idx < 0 or idx >= len(bars):
        return False

    prefix = bars[: idx + 1]
    if len(prefix) < MIN_BARS_FOR_CONTEXT:
        return False

    c = closes(prefix)
    close = c[-1]

    if trend_required:
        sma50 = sma(c, SMA50_PERIOD)
        sma200 = sma(c, SMA200_PERIOD)
        if sma50 is None or sma200 is None:
            return False
        if close <= sma50 or close <= sma200:
            return False

    dist_high = distance_from_high(c, HIGH_LOOKBACK)
    if dist_high is None or dist_high < near_high_max:
        return False

    mom_21d = pct_change(c, 21)
    if mom_21d is None or mom_21d < mom_21d_min:
        return False

    vol_ratio = volume_vs_avg(volumes(prefix), 20)
    if vol_ratio is None or vol_ratio < vol_min:
        return False

    if require_market_uptrend:
        mkt_ctx = compute_market_context(spy_bars, spy_idx)
        if mkt_ctx is None:
            return False
        if mkt_ctx.get("mkt_vs_sma200") != "above":
            return False

    return True


def _trading_days_until(from_date: str, to_date: str) -> int:
    """Approx trading days from from_date (exclusive) to to_date (inclusive).

    Mon-Fri count, HOLIDAY-NAIVE — a live approximation of the backtest's exact
    bar-index gap, since the next earnings is a future date with no bar yet. May
    drift ~1-2 days vs the real calendar over a few weeks (documented caveat).
    """
    try:
        start = date.fromisoformat(from_date[:10])
        end = date.fromisoformat(to_date[:10])
    except ValueError:
        return -1
    if end <= start:
        return 0
    days = 0
    cur = start
    while cur < end:
        cur += timedelta(days=1)
        if cur.weekday() < 5:
            days += 1
    return days


def is_earnings_drift_entry(
    bars: list[DatedBar],
    idx: int,
    *,
    earnings_dates: list[str],
    blowout_pct: float = 5.0,
    entry_lead_td: int = 10,
    exit_lead_td: int = 1,
    min_gap_td: int = 40,
    max_gap_td: int = 90,
) -> bool:
    """Pre-earnings drift after a blowout — LONG-only entry, point-in-time at idx.

    Mirrors research/earnings_strategy.py: if the PRIOR earnings produced a
    blowout-UP reaction (close[j+1]/close[j-1]-1 >= blowout_pct), buy ~entry_lead_td
    trading days before the NEXT earnings (exit ~exit_lead_td days before — never
    holds through the announcement). `earnings_dates` is the symbol's known
    earnings dates (live: from db.earnings_calendar). No look-ahead: only the prior
    earnings' reaction (already realized) is used; the next date only times entry.
    """
    if idx < 0 or idx >= len(bars):
        return False
    today = bars[idx]["d"]

    past = [d for d in earnings_dates if d and d <= today]
    future = [d for d in earnings_dates if d and d > today]
    if not past or not future:
        return False
    last_e = max(past)
    next_e = min(future)

    bar_dates = [b["d"] for b in bars]
    j = bisect.bisect_left(bar_dates, last_e)
    # Need the prior-earnings bar plus the day before and after to score reaction,
    # and j must not run past the loaded bars.
    if j >= len(bars) or j - 1 < 0 or j + 1 >= len(bars):
        return False
    c = [float(b["c"]) for b in bars]
    prev_close = c[j - 1]
    if prev_close <= 0:
        return False
    reaction_pct = (c[j + 1] / prev_close - 1.0) * 100.0
    if reaction_pct < blowout_pct:  # require a positive blowout (long-only thesis)
        return False

    td_to_next = _trading_days_until(today, next_e)
    if td_to_next < 0:
        return False
    gap_td = (idx - j) + td_to_next
    if gap_td < min_gap_td or gap_td > max_gap_td:
        return False

    # In the pre-earnings holding window: at/just-inside the entry lead, but not yet
    # past the exit lead. One-entry-per-signal upstream keeps it from re-firing.
    return exit_lead_td < td_to_next <= entry_lead_td


def is_vix_spike_entry(
    current_vix: float,
    prior_vix: list[float] | tuple[float, ...],
    *,
    vix_spike_min: float = 28.0,
    vix_calm_max: float = 22.0,
    vix_calm_lookback: int = 12,
    vix_calm_min_days: int = 8,
) -> bool:
    """Point-in-time VIX-spike entry: a *sudden* spike from calm. True iff today's VIX
    is above the spike floor AND at least `vix_calm_min_days` of the prior
    `vix_calm_lookback` closes were below the calm ceiling.

    Pure (no I/O) so the live path (reads VIX history from db.regime) and the golden-
    fixture validation share one predicate. `prior_vix` is the closes immediately
    prior to today (order-independent — only the count matters); fewer than
    `vix_calm_lookback` ⇒ insufficient history ⇒ False (fail-closed).
    """
    if len(prior_vix) < vix_calm_lookback:
        return False
    if current_vix <= vix_spike_min:
        return False
    window = prior_vix[:vix_calm_lookback]
    calm_days = sum(1 for v in window if v < vix_calm_max)
    return calm_days >= vix_calm_min_days
