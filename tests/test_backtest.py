"""Unit tests for evaluate_condition and signals.backtest."""

from __future__ import annotations

import pytest

from ai.rules.engine import evaluate_condition
from signals.backtest import backtest_condition
from signals.features import build_indicator_facts
from signals.indicators import Bar


def _bars_from_closes(
    close_series: list[float],
    *,
    volume: float = 1000.0,
) -> list[Bar]:
    return [
        {"o": c, "h": c + 1.0, "l": c - 1.0, "c": c, "v": volume}
        for c in close_series
    ]


# --- Part A: public condition evaluator ---


def test_evaluate_condition_all_and_operators() -> None:
    facts = {"score": 42, "label": "buy"}
    condition = {
        "all": [
            {"fact": "score", "operator": "greaterThanInclusive", "value": 40},
            {"fact": "label", "operator": "equal", "value": "buy"},
        ]
    }
    assert evaluate_condition(condition, facts) is True
    assert evaluate_condition(condition, {**facts, "score": 39}) is False


def test_evaluate_condition_any_and_not() -> None:
    facts = {"flag_a": False, "flag_b": True}
    assert (
        evaluate_condition(
            {"any": [{"fact": "flag_a", "operator": "equal", "value": True}]},
            facts,
        )
        is False
    )
    assert (
        evaluate_condition(
            {"any": [{"fact": "flag_b", "operator": "equal", "value": True}]},
            facts,
        )
        is True
    )
    assert (
        evaluate_condition(
            {"not": {"fact": "flag_a", "operator": "equal", "value": True}},
            facts,
        )
        is True
    )


def test_evaluate_condition_missing_fact_is_falsey() -> None:
    condition = {"fact": "missing_metric", "operator": "lessThanInclusive", "value": -5}
    assert evaluate_condition(condition, {}) is False
    assert (
        evaluate_condition(
            {"fact": "missing_metric", "operator": "equal", "value": True},
            {},
        )
        is False
    )


# --- Part B: backtest runner ---


def test_backtest_pct_change_fires_on_entering_bars_only() -> None:
    symbol = "NVDA"
    closes = [100.0, 100.0, 94.0, 88.36, 100.0, 93.0]
    bars = _bars_from_closes(closes)
    condition = {
        "fact": f"{symbol}_pct_change_1d",
        "operator": "lessThanInclusive",
        "value": -5,
    }

    fires = backtest_condition(condition, bars, symbol)

    assert [event["index"] for event in fires] == [2, 5]
    assert fires[0]["price"] == pytest.approx(94.0)
    assert fires[1]["price"] == pytest.approx(93.0)


def test_backtest_sma200_cross_below_fires_once() -> None:
    symbol = "NVDA"
    closes = [100.0] * 200 + [105.0, 95.0]
    bars = _bars_from_closes(closes)
    condition = {
        "fact": f"{symbol}_cross_below_sma_200",
        "operator": "equal",
        "value": True,
    }

    fires = backtest_condition(condition, bars, symbol)

    assert len(fires) == 1
    assert fires[0]["index"] == 201
    assert fires[0]["price"] == pytest.approx(95.0)


def test_backtest_min_bars_between_suppresses_near_refires() -> None:
    symbol = "NVDA"
    closes = [100.0, 100.0, 94.0, 100.0, 94.0]
    bars = _bars_from_closes(closes)
    condition = {
        "fact": f"{symbol}_pct_change_1d",
        "operator": "lessThanInclusive",
        "value": -5,
    }

    fires_default = backtest_condition(condition, bars, symbol, min_bars_between=1)
    fires_spaced = backtest_condition(condition, bars, symbol, min_bars_between=3)

    assert [event["index"] for event in fires_default] == [2, 4]
    assert [event["index"] for event in fires_spaced] == [2]


def test_backtest_all_composition_requires_both_conditions() -> None:
    symbol = "NVDA"
    closes = [100.0] * 200 + [105.0, 95.0]
    bars = _bars_from_closes(closes)
    condition = {
        "all": [
            {
                "fact": f"{symbol}_pct_change_1d",
                "operator": "lessThanInclusive",
                "value": -5,
            },
            {
                "fact": f"{symbol}_cross_below_sma_200",
                "operator": "equal",
                "value": True,
            },
        ]
    }

    fires = backtest_condition(condition, bars, symbol)

    assert len(fires) == 1
    assert fires[0]["index"] == 201


def test_backtest_rel_strength_with_benchmark_bars() -> None:
    symbol = "NVDA"
    asset_closes = [100.0 + (10.0 * i / 21) for i in range(22)]
    bench_closes = [100.0 + (5.0 * i / 21) for i in range(22)]
    bars = _bars_from_closes(asset_closes)
    benchmark_bars = _bars_from_closes(bench_closes)
    condition = {
        "fact": f"{symbol}_rel_strength_21",
        "operator": "greaterThanInclusive",
        "value": 5,
    }

    fires = backtest_condition(
        condition,
        bars,
        symbol,
        benchmark_bars=benchmark_bars,
    )

    assert len(fires) == 1
    assert fires[0]["index"] == 21
    facts_at_fire = build_indicator_facts(
        {symbol: bars[:22], "BENCH": benchmark_bars[:22]},
        benchmark="BENCH",
    )
    assert facts_at_fire[f"{symbol}_rel_strength_21"] == pytest.approx(5.0)
