"""Unit tests for research.strategy_features — market context and entry predicate."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from research.strategy_features import compute_market_context, is_strategy_entry
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
