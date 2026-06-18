"""Pure bar-by-bar backtest replay for declarative rule conditions."""

from __future__ import annotations

from typing import Any

from ai.rules.engine import evaluate_condition
from signals.features import build_indicator_facts
from signals.indicators import Bar

BENCHMARK_KEY = "BENCH"


def backtest_condition(
    condition: dict[str, Any],
    bars: list[Bar],
    symbol: str,
    *,
    benchmark_bars: list[Bar] | None = None,
    min_bars_between: int = 1,
) -> list[dict[str, Any]]:
    """Replay `condition` bar-by-bar; return fire events (false→true transitions)."""
    fires: list[dict[str, Any]] = []
    prev_matched = False
    last_fire_index = -min_bars_between

    for i in range(len(bars)):
        symbol_bars: dict[str, list[Bar]] = {symbol: bars[: i + 1]}
        if benchmark_bars is not None:
            symbol_bars[BENCHMARK_KEY] = benchmark_bars[: i + 1]

        facts = build_indicator_facts(
            symbol_bars,
            benchmark=BENCHMARK_KEY if benchmark_bars is not None else None,
        )
        matched = evaluate_condition(condition, facts)

        if matched and not prev_matched:
            if i - last_fire_index >= min_bars_between:
                fires.append({"index": i, "price": bars[i]["c"]})
                last_fire_index = i

        prev_matched = matched

    return fires
