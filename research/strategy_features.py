"""Point-in-time strategy entry predicate and SPY market-context features."""

from __future__ import annotations

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

    return True
