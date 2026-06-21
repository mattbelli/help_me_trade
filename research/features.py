"""Point-in-time feature vectors for labeled dataset rows."""

from __future__ import annotations

from datetime import date

from signals.features import build_indicator_facts
from signals.indicators import closes, sma
from signals.trade_context import (
    MIN_BARS_FOR_CONTEXT,
    DatedBar,
    _compute_context_row,
    _find_bar_index,
)

_INTERNAL_SYMBOL = "S"
_BENCHMARK_SYMBOL = "BENCH"


def _dist_from_sma_pct(close: float, ma: float | None) -> float | None:
    if ma is None or ma == 0:
        return None
    return (close / ma - 1) * 100


def compute_features(
    bars: list[DatedBar],
    idx: int,
    *,
    benchmark_bars: list[DatedBar] | None = None,
) -> dict | None:
    """Build a feature dict using only bars[:idx+1] (no look-ahead)."""
    if idx < 0 or idx >= len(bars):
        return None

    prefix = bars[: idx + 1]
    if len(prefix) < MIN_BARS_FOR_CONTEXT:
        return None

    entry_date = bars[idx]["d"]
    event = {
        "symbol": _INTERNAL_SYMBOL,
        "side": "buy",
        "trade_date": entry_date,
    }
    ctx = _compute_context_row(event, bars)
    if not ctx.get("context_available"):
        return None

    symbol_bars: dict[str, list[DatedBar]] = {_INTERNAL_SYMBOL: prefix}
    benchmark_key: str | None = None
    if benchmark_bars:
        trade_date = date.fromisoformat(entry_date)
        bench_idx = _find_bar_index(benchmark_bars, trade_date)
        if bench_idx is not None and bench_idx + 1 >= MIN_BARS_FOR_CONTEXT:
            symbol_bars[_BENCHMARK_SYMBOL] = benchmark_bars[: bench_idx + 1]
            benchmark_key = _BENCHMARK_SYMBOL

    ind_facts = build_indicator_facts(
        symbol_bars,
        benchmark=benchmark_key,
    )

    c = closes(prefix)
    close = c[-1]
    sma50 = sma(c, 50)
    sma200 = sma(c, 200)

    features: dict[str, float | str | None] = {
        "prior_return_1d": ctx["prior_return_1d"],
        "prior_return_3d": ctx["prior_return_3d"],
        "prior_return_5d": ctx["prior_return_5d"],
        "vs_sma50": ctx["vs_sma50"],
        "vs_sma200": ctx["vs_sma200"],
        "dist_from_52w_high_pct": ctx["dist_from_52w_high_pct"],
        "gap_prior": ctx["gap_prior"],
        "entry_context": ctx["entry_context"],
        "rsi_14": ind_facts.get(f"{_INTERNAL_SYMBOL}_rsi_14"),
        "atr_14": ind_facts.get(f"{_INTERNAL_SYMBOL}_atr_14"),
        "dist_from_sma50_pct": _dist_from_sma_pct(close, sma50),
        "dist_from_sma200_pct": _dist_from_sma_pct(close, sma200),
        "dist_from_high_252": ind_facts.get(f"{_INTERNAL_SYMBOL}_dist_from_high_252"),
        "pct_change_1d": ind_facts.get(f"{_INTERNAL_SYMBOL}_pct_change_1d"),
        "pct_change_5d": ind_facts.get(f"{_INTERNAL_SYMBOL}_pct_change_5d"),
        "pct_change_21d": ind_facts.get(f"{_INTERNAL_SYMBOL}_pct_change_21d"),
        "vol_vs_avg_20": ind_facts.get(f"{_INTERNAL_SYMBOL}_vol_vs_avg_20"),
    }

    if benchmark_key:
        features["rel_strength_21"] = ind_facts.get(f"{_INTERNAL_SYMBOL}_rel_strength_21")

    return {k: v for k, v in features.items() if v is not None}
