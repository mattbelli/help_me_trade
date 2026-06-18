"""Build flat indicator facts from per-symbol bar history for the rules engine."""

from __future__ import annotations

from typing import Any

from signals.indicators import (
    Bar,
    atr,
    closes,
    cross_above,
    cross_below,
    distance_from_high,
    ema,
    highs,
    lows,
    pct_change,
    rel_strength,
    rsi,
    sma,
    volume_vs_avg,
    volumes,
)


def _ma_cross(close_series: list[float], n: int) -> tuple[bool, bool]:
    """Detect price/SMA cross on the latest bar using a 2-point tail."""
    sma_prev = sma(close_series[:-1], n)
    sma_curr = sma(close_series, n)
    if sma_prev is None or sma_curr is None:
        return False, False
    price2 = close_series[-2:]
    sma2 = [sma_prev, sma_curr]
    return cross_above(price2, sma2), cross_below(price2, sma2)


def _add_if_present(facts: dict[str, Any], key: str, value: Any) -> None:
    if value is not None:
        facts[key] = value


def build_indicator_facts(
    symbol_bars: dict[str, list[Bar]],
    *,
    benchmark: str | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    benchmark_closes = (
        closes(symbol_bars[benchmark]) if benchmark and benchmark in symbol_bars else None
    )

    for symbol, bars in symbol_bars.items():
        if not bars:
            continue

        c = closes(bars)
        h = highs(bars)
        low_vals = lows(bars)
        v = volumes(bars)

        _add_if_present(result, f"{symbol}_price", c[-1])
        _add_if_present(result, f"{symbol}_pct_change_1d", pct_change(c, 1))
        _add_if_present(result, f"{symbol}_pct_change_5d", pct_change(c, 5))
        _add_if_present(result, f"{symbol}_pct_change_21d", pct_change(c, 21))
        _add_if_present(result, f"{symbol}_sma_50", sma(c, 50))
        _add_if_present(result, f"{symbol}_sma_200", sma(c, 200))
        _add_if_present(result, f"{symbol}_ema_20", ema(c, 20))
        _add_if_present(result, f"{symbol}_rsi_14", rsi(c, 14))
        _add_if_present(result, f"{symbol}_atr_14", atr(h, low_vals, c, 14))
        _add_if_present(result, f"{symbol}_dist_from_high_252", distance_from_high(c, 252))
        _add_if_present(result, f"{symbol}_vol_vs_avg_20", volume_vs_avg(v, 20))

        cross_above_ma, cross_below_ma = _ma_cross(c, 200)
        result[f"{symbol}_cross_above_sma_200"] = cross_above_ma
        result[f"{symbol}_cross_below_sma_200"] = cross_below_ma

        if benchmark and benchmark_closes is not None and symbol != benchmark:
            _add_if_present(
                result,
                f"{symbol}_rel_strength_21",
                rel_strength(c, benchmark_closes, 21),
            )

    return result
