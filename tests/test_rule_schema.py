"""Tests for signals.rule_schema — validated rule conditions that compile to engine dicts."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from ai.rules.engine import evaluate_condition
from signals.features import build_indicator_facts
from signals.indicators import Bar
from signals.rule_schema import (
    Action,
    Group,
    Leaf,
    Operator,
    RuleSpec,
    Universe,
    fact_key,
)


def _bars_from_closes(
    close_series: list[float],
    *,
    volume: float = 1000.0,
) -> list[Bar]:
    return [
        {"o": c, "h": c + 1.0, "l": c - 1.0, "c": c, "v": volume}
        for c in close_series
    ]


def test_symbol_bool_metric_compiles_with_uppercased_symbol() -> None:
    leaf = Leaf(
        metric="cross_below_sma_200",
        symbol="nvda",
        operator=Operator.equal,
        value=True,
    )
    assert leaf.compile() == {
        "fact": "NVDA_cross_below_sma_200",
        "operator": "equal",
        "value": True,
    }


def test_numeric_symbol_metric_compiles() -> None:
    leaf = Leaf(
        metric="pct_change_1d",
        symbol="NVDA",
        operator=Operator.lessThanInclusive,
        value=-5,
    )
    assert leaf.compile() == {
        "fact": "NVDA_pct_change_1d",
        "operator": "lessThanInclusive",
        "value": -5,
    }


def test_portfolio_metric_compiles_without_symbol() -> None:
    leaf = Leaf(
        metric="portfolio_value",
        operator=Operator.greaterThan,
        value=100_000,
    )
    assert leaf.compile() == {
        "fact": "portfolio_value",
        "operator": "greaterThan",
        "value": 100_000,
    }


def test_portfolio_metric_rejects_symbol() -> None:
    with pytest.raises(ValidationError, match="symbol must not be set"):
        Leaf(
            metric="portfolio_value",
            symbol="NVDA",
            operator=Operator.greaterThan,
            value=1,
        )


def test_position_metric_compiles() -> None:
    leaf = Leaf(
        metric="position_weight_pct",
        symbol="nvda",
        operator=Operator.greaterThanInclusive,
        value=10.0,
    )
    assert leaf.compile() == {
        "fact": "position_NVDA_weight_pct",
        "operator": "greaterThanInclusive",
        "value": 10.0,
    }
    assert fact_key("position_weight_pct", "NVDA") == "position_NVDA_weight_pct"


def test_unknown_metric_errors() -> None:
    with pytest.raises(ValidationError, match="unknown metric 'fake_metric'"):
        Leaf(metric="fake_metric", symbol="NVDA", operator=Operator.equal, value=1)


def test_symbol_metric_missing_symbol_errors() -> None:
    with pytest.raises(ValidationError, match="symbol is required"):
        Leaf(metric="price", operator=Operator.equal, value=100)


def test_bool_metric_rejects_numeric_value() -> None:
    with pytest.raises(ValidationError, match="expects a bool value"):
        Leaf(
            metric="cross_below_sma_200",
            symbol="NVDA",
            operator=Operator.equal,
            value=1,
        )


def test_bool_metric_rejects_greater_than_operator() -> None:
    with pytest.raises(ValidationError, match="only supports operators"):
        Leaf(
            metric="has_positions",
            operator=Operator.greaterThan,
            value=True,
        )


def test_group_all_compiles() -> None:
    leaf1 = Leaf(
        metric="pct_change_1d",
        symbol="NVDA",
        operator=Operator.lessThanInclusive,
        value=-5,
    )
    leaf2 = Leaf(
        metric="cross_below_sma_200",
        symbol="NVDA",
        operator=Operator.equal,
        value=True,
    )
    group = Group(all=[leaf1, leaf2])
    compiled = group.compile()
    assert compiled == {
        "all": [
            {
                "fact": "NVDA_pct_change_1d",
                "operator": "lessThanInclusive",
                "value": -5,
            },
            {
                "fact": "NVDA_cross_below_sma_200",
                "operator": "equal",
                "value": True,
            },
        ]
    }


def test_group_nested_not_compiles() -> None:
    inner = Leaf(
        metric="cross_below_sma_200",
        symbol="NVDA",
        operator=Operator.equal,
        value=True,
    )
    group = Group(not_=inner)
    assert group.compile() == {
        "not": {
            "fact": "NVDA_cross_below_sma_200",
            "operator": "equal",
            "value": True,
        }
    }


def test_rule_spec_to_engine_rule_shape() -> None:
    spec = RuleSpec(
        name="SMA cross alert",
        condition=Leaf(
            metric="cross_below_sma_200",
            symbol="NVDA",
            operator=Operator.equal,
            value=True,
        ),
        action=Action(message="NVDA crossed below SMA 200"),
        enabled=False,
        source="ai",
    )
    engine_rule = spec.to_engine_rule()
    assert engine_rule == {
        "name": "SMA cross alert",
        "enabled": False,
        "conditions": {
            "fact": "NVDA_cross_below_sma_200",
            "operator": "equal",
            "value": True,
        },
        "action": {
            "type": "alert",
            "trigger_on": "transition",
            "cooldown_seconds": 0,
            "message": "NVDA crossed below SMA 200",
        },
    }


def test_universe_requires_value_for_watchlist_and_symbol() -> None:
    with pytest.raises(ValidationError, match="value is required"):
        Universe(kind="watchlist")
    with pytest.raises(ValidationError, match="value is required"):
        Universe(kind="symbol")
    Universe(kind="positions")
    Universe(kind="all")
    Universe(kind="watchlist", value="tech")
    Universe(kind="symbol", value="NVDA")


def test_integration_cross_below_evaluates_true_against_real_facts() -> None:
    close_series = [100.0] * 200 + [105.0, 95.0]
    facts = build_indicator_facts({"NVDA": _bars_from_closes(close_series)})
    spec = RuleSpec(
        name="Cross below SMA 200",
        condition=Leaf(
            metric="cross_below_sma_200",
            symbol="nvda",
            operator=Operator.equal,
            value=True,
        ),
    )
    assert evaluate_condition(spec.condition.compile(), facts) is True


def test_integration_non_matching_cross_evaluates_false() -> None:
    close_series = [100.0] * 200 + [105.0, 95.0]
    facts = build_indicator_facts({"NVDA": _bars_from_closes(close_series)})
    spec = RuleSpec(
        name="No cross",
        condition=Leaf(
            metric="cross_below_sma_200",
            symbol="NVDA",
            operator=Operator.equal,
            value=False,
        ),
    )
    assert evaluate_condition(spec.condition.compile(), facts) is False
