"""Unit tests for research.strategy_features — market context and entry predicate."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from research.strategy_features import (
    compute_market_context,
    is_consec_down_entry,
    is_momentum_entry,
    is_rsi2_entry,
    is_strategy_entry,
)
from signals.trade_context import MIN_BARS_FOR_CONTEXT, DatedBar


def _dated_bars_from_closes(
    close_series: list[float],
    start_date: date,
    *,
    volume: float | list[float] = 1000.0,
) -> list[DatedBar]:
    bars: list[DatedBar] = []
    d = start_date
    for i, c in enumerate(close_series):
        prev_c = close_series[i - 1] if i > 0 else c
        if isinstance(volume, list):
            v = volume[i]
        else:
            v = volume
        bars.append(
            {
                "d": d.isoformat(),
                "o": prev_c if i > 0 else c,
                "h": c + 1.0,
                "l": c - 1.0,
                "c": c,
                "v": v,
            }
        )
        d += timedelta(days=1)
    return bars


def _uptrend_closes(n: int, *, start: float = 100.0, step: float = 0.35) -> list[float]:
    return [start + i * step for i in range(n)]


def _append_three_day_dip(closes: list[float], drop_pct: float) -> list[float]:
    """Append a 3-bar decline ending drop_pct below the prior close."""
    peak = closes[-1]
    factor = 1.0 + drop_pct / 100.0
    return closes + [peak, peak * (factor ** (1 / 3)), peak * (factor ** (2 / 3)), peak * factor]


def _healthy_stock_bars(*, drop_pct: float = -6.0) -> tuple[list[DatedBar], int]:
    """Uptrend above SMA200 with a multi-day dip on the last bar."""
    base_len = MIN_BARS_FOR_CONTEXT + 4
    closes = _uptrend_closes(base_len - 4)
    closes = _append_three_day_dip(closes, drop_pct)
    bars = _dated_bars_from_closes(closes, date(2024, 1, 1))
    return bars, len(bars) - 1


def _spy_bars(*, drop_pct: float = -2.0) -> tuple[list[DatedBar], int]:
    """SPY series with a configurable 3-day move into the last bar."""
    base_len = MIN_BARS_FOR_CONTEXT + 4
    closes = [450.0] * (base_len - 4)
    closes = _append_three_day_dip(closes, drop_pct)
    bars = _dated_bars_from_closes(closes, date(2024, 1, 1))
    return bars, len(bars) - 1


def test_market_context_shape() -> None:
    spy_bars, spy_idx = _spy_bars(drop_pct=-4.0)
    ctx = compute_market_context(spy_bars, spy_idx)

    assert ctx is not None
    assert set(ctx) >= {
        "mkt_ret_1d",
        "mkt_ret_3d",
        "mkt_ret_5d",
        "mkt_rsi_14",
        "mkt_dist_from_high_252",
        "mkt_vs_sma50",
        "mkt_vs_sma200",
    }
    assert ctx["mkt_ret_3d"] == pytest.approx(-4.0, abs=0.01)
    assert ctx["mkt_ret_3d"] < 0
    assert ctx["mkt_dist_from_high_252"] <= 0

    short_bars = _dated_bars_from_closes([100.0] * (MIN_BARS_FOR_CONTEXT - 1), date(2024, 1, 1))
    assert compute_market_context(short_bars, len(short_bars) - 1) is None


def _append_three_day_rally(closes: list[float], rally_pct: float) -> list[float]:
    """Append a 3-bar rally ending rally_pct above the prior close."""
    base = closes[-1]
    factor = 1.0 + rally_pct / 100.0
    return closes + [base, base * (factor ** (1 / 3)), base * (factor ** (2 / 3)), base * factor]


def _weak_stock_bars(*, rally_pct: float = 6.0) -> tuple[list[DatedBar], int]:
    """Downtrend below SMA200 with a multi-day rally on the last bar."""
    base_len = MIN_BARS_FOR_CONTEXT + 4
    closes = [220.0 - i * 0.45 for i in range(base_len - 4)]
    closes = _append_three_day_rally(closes, rally_pct)
    bars = _dated_bars_from_closes(closes, date(2024, 1, 1))
    return bars, len(bars) - 1


def _spy_bars_rally(*, rally_pct: float = 2.0) -> tuple[list[DatedBar], int]:
    """SPY series with a configurable 3-day rally into the last bar."""
    base_len = MIN_BARS_FOR_CONTEXT + 4
    closes = [450.0] * (base_len - 4)
    closes = _append_three_day_rally(closes, rally_pct)
    bars = _dated_bars_from_closes(closes, date(2024, 1, 1))
    return bars, len(bars) - 1


def test_entry_passes_with_defaults() -> None:
    stock_bars, idx = _healthy_stock_bars(drop_pct=-6.0)
    spy_bars, spy_idx = _spy_bars(drop_pct=-2.0)

    assert is_strategy_entry(stock_bars, idx, spy_bars=spy_bars, spy_idx=spy_idx) is True


def test_entry_fails_when_not_healthy() -> None:
    n = MIN_BARS_FOR_CONTEXT + 4
    closes = [220.0 - i * 0.45 for i in range(n - 4)]
    closes = _append_three_day_dip(closes, -6.0)
    stock_bars = _dated_bars_from_closes(closes, date(2024, 1, 1))
    idx = len(stock_bars) - 1
    spy_bars, spy_idx = _spy_bars(drop_pct=-2.0)

    assert is_strategy_entry(stock_bars, idx, spy_bars=spy_bars, spy_idx=spy_idx) is False


def test_entry_fails_when_not_oversold() -> None:
    stock_bars, idx = _healthy_stock_bars(drop_pct=-2.0)
    spy_bars, spy_idx = _spy_bars(drop_pct=-2.0)

    assert is_strategy_entry(stock_bars, idx, spy_bars=spy_bars, spy_idx=spy_idx) is False


def test_entry_fails_when_market_up() -> None:
    stock_bars, idx = _healthy_stock_bars(drop_pct=-6.0)
    spy_bars, spy_idx = _spy_bars(drop_pct=1.0)

    assert is_strategy_entry(stock_bars, idx, spy_bars=spy_bars, spy_idx=spy_idx) is False


def test_capitulation_gate() -> None:
    stock_bars, idx = _healthy_stock_bars(drop_pct=-6.0)
    spy_bars, spy_idx = _spy_bars(drop_pct=-2.0)

    assert (
        is_strategy_entry(
            stock_bars,
            idx,
            spy_bars=spy_bars,
            spy_idx=spy_idx,
            require_capitulation=True,
        )
        is False
    )

    volumes = [1000.0] * len(stock_bars)
    volumes[-1] = 2000.0
    stock_bars = _dated_bars_from_closes(
        [bar["c"] for bar in stock_bars],
        date(2024, 1, 1),
        volume=volumes,
    )

    assert (
        is_strategy_entry(
            stock_bars,
            idx,
            spy_bars=spy_bars,
            spy_idx=spy_idx,
            require_capitulation=True,
        )
        is True
    )


def test_short_entry_passes_when_overbought() -> None:
    stock_bars, idx = _weak_stock_bars(rally_pct=6.0)
    spy_bars, spy_idx = _spy_bars_rally(rally_pct=2.0)

    assert (
        is_strategy_entry(
            stock_bars,
            idx,
            spy_bars=spy_bars,
            spy_idx=spy_idx,
            side="short",
        )
        is True
    )


def test_short_entry_fails_when_market_down() -> None:
    stock_bars, idx = _weak_stock_bars(rally_pct=6.0)
    spy_bars, spy_idx = _spy_bars(drop_pct=-2.0)

    assert (
        is_strategy_entry(
            stock_bars,
            idx,
            spy_bars=spy_bars,
            spy_idx=spy_idx,
            side="short",
        )
        is False
    )


def test_long_default_unchanged_with_side_param() -> None:
    stock_bars, idx = _healthy_stock_bars(drop_pct=-6.0)
    spy_bars, spy_idx = _spy_bars(drop_pct=-2.0)

    assert is_strategy_entry(stock_bars, idx, spy_bars=spy_bars, spy_idx=spy_idx) is True
    assert (
        is_strategy_entry(
            stock_bars,
            idx,
            spy_bars=spy_bars,
            spy_idx=spy_idx,
            side="long",
        )
        is True
    )


def _momentum_stock_bars(
    *,
    mom_21d_pct: float = 8.0,
    volume_spike: float = 2.4,
    at_high: bool = True,
) -> tuple[list[DatedBar], int]:
    """Uptrend at/near 52w high with configurable 21d momentum and volume."""
    n = MIN_BARS_FOR_CONTEXT + 25
    closes = _uptrend_closes(n - 22, start=70.0, step=0.12)
    base_21d = closes[-1]
    target = base_21d * (1.0 + mom_21d_pct / 100.0)
    closes.append(base_21d)
    for i in range(1, 22):
        closes.append(base_21d + (target - base_21d) * i / 21)
    if not at_high:
        closes[-1] = base_21d * 0.85

    volumes = [1000.0] * n
    volumes[-1] = 1000.0 * volume_spike
    bars = _dated_bars_from_closes(closes, date(2024, 1, 1), volume=volumes)
    return bars, len(bars) - 1


def _spy_bars_uptrend() -> tuple[list[DatedBar], int]:
    n = MIN_BARS_FOR_CONTEXT + 25
    closes = _uptrend_closes(n, start=400.0, step=0.4)
    bars = _dated_bars_from_closes(closes, date(2024, 1, 1))
    return bars, len(bars) - 1


def _spy_bars_downtrend() -> tuple[list[DatedBar], int]:
    n = MIN_BARS_FOR_CONTEXT + 25
    closes = [500.0 - i * 0.5 for i in range(n)]
    bars = _dated_bars_from_closes(closes, date(2024, 1, 1))
    return bars, len(bars) - 1


def test_momentum_entry_passes_with_defaults() -> None:
    stock_bars, idx = _momentum_stock_bars()
    spy_bars, spy_idx = _spy_bars_uptrend()

    assert (
        is_momentum_entry(
            stock_bars,
            idx,
            spy_bars=spy_bars,
            spy_idx=spy_idx,
        )
        is True
    )


def test_momentum_entry_fails_far_from_high() -> None:
    stock_bars, idx = _momentum_stock_bars(at_high=False)
    spy_bars, spy_idx = _spy_bars_uptrend()

    assert (
        is_momentum_entry(
            stock_bars,
            idx,
            spy_bars=spy_bars,
            spy_idx=spy_idx,
        )
        is False
    )


def test_momentum_entry_fails_when_market_down() -> None:
    stock_bars, idx = _momentum_stock_bars()
    spy_bars, spy_idx = _spy_bars_downtrend()

    assert (
        is_momentum_entry(
            stock_bars,
            idx,
            spy_bars=spy_bars,
            spy_idx=spy_idx,
            require_market_uptrend=True,
        )
        is False
    )


def _rsi2_stock_bars(*, uptrend: bool = True) -> tuple[list[DatedBar], int]:
    """Uptrend (or downtrend) with a sharp 3-day drop pushing RSI(2) below 10."""
    base_len = MIN_BARS_FOR_CONTEXT + 3
    if uptrend:
        closes = _uptrend_closes(base_len - 3)
    else:
        closes = [220.0 - i * 0.45 for i in range(base_len - 3)]
    peak = closes[-1]
    closes = closes + [peak * 0.97, peak * 0.94, peak * 0.90]
    bars = _dated_bars_from_closes(closes, date(2024, 1, 1))
    return bars, len(bars) - 1


def test_rsi2_entry_passes_when_oversold_in_uptrend() -> None:
    stock_bars, idx = _rsi2_stock_bars(uptrend=True)
    spy_bars, spy_idx = _spy_bars()

    assert (
        is_rsi2_entry(
            stock_bars,
            idx,
            spy_bars=spy_bars,
            spy_idx=spy_idx,
        )
        is True
    )


def test_rsi2_entry_fails_below_sma200() -> None:
    stock_bars, idx = _rsi2_stock_bars(uptrend=False)
    spy_bars, spy_idx = _spy_bars()

    assert (
        is_rsi2_entry(
            stock_bars,
            idx,
            spy_bars=spy_bars,
            spy_idx=spy_idx,
        )
        is False
    )


def test_rsi2_entry_fails_when_rsi_high() -> None:
    base_len = MIN_BARS_FOR_CONTEXT + 1
    closes = _uptrend_closes(base_len)
    stock_bars = _dated_bars_from_closes(closes, date(2024, 1, 1))
    idx = len(stock_bars) - 1
    spy_bars, spy_idx = _spy_bars()

    assert (
        is_rsi2_entry(
            stock_bars,
            idx,
            spy_bars=spy_bars,
            spy_idx=spy_idx,
        )
        is False
    )


def _consec_down_stock_bars(
    *,
    down_days: int = 3,
    uptrend: bool = True,
) -> tuple[list[DatedBar], int]:
    """Series ending with down_days strictly-down closes."""
    base_len = MIN_BARS_FOR_CONTEXT + down_days - 1
    if uptrend:
        closes = _uptrend_closes(base_len)
    else:
        closes = [220.0 - i * 0.45 for i in range(base_len)]
    peak = closes[-1]
    for step in range(1, down_days):
        closes.append(peak * (1.0 - 0.02 * step))
    bars = _dated_bars_from_closes(closes, date(2024, 1, 1))
    return bars, len(bars) - 1


def test_consec_down_entry_passes_three_down_in_uptrend() -> None:
    stock_bars, idx = _consec_down_stock_bars(down_days=3, uptrend=True)
    spy_bars, spy_idx = _spy_bars()

    assert (
        is_consec_down_entry(
            stock_bars,
            idx,
            spy_bars=spy_bars,
            spy_idx=spy_idx,
            down_days=3,
        )
        is True
    )


def test_consec_down_entry_fails_with_only_two_down() -> None:
    stock_bars, idx = _consec_down_stock_bars(down_days=2, uptrend=True)
    spy_bars, spy_idx = _spy_bars()

    assert (
        is_consec_down_entry(
            stock_bars,
            idx,
            spy_bars=spy_bars,
            spy_idx=spy_idx,
            down_days=3,
        )
        is False
    )


def test_consec_down_entry_fails_below_sma200() -> None:
    stock_bars, idx = _consec_down_stock_bars(down_days=3, uptrend=False)
    spy_bars, spy_idx = _spy_bars()

    assert (
        is_consec_down_entry(
            stock_bars,
            idx,
            spy_bars=spy_bars,
            spy_idx=spy_idx,
            down_days=3,
        )
        is False
    )
