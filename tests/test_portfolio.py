"""Tests for portfolio equity-curve backtester."""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pytest

from research.bars import cache_bars
from research.portfolio import backtest_portfolio, buy_and_hold_benchmark
from signals.trade_context import DatedBar


def _single_trade(return_pct: float, *, holding_days: int = 5) -> list[tuple]:
    return [("2020-01-01", holding_days, return_pct)]


def test_single_trade_compounding() -> None:
    start = 100_000.0
    cost = 0.30
    result = backtest_portfolio(
        _single_trade(10.0),
        start_equity=start,
        max_concurrent=1,
        per_trade_fraction=1.0,
        max_gross_exposure=1.0,
        cost_pct=cost,
    )
    expected = start * (1.0 + (10.0 - cost) / 100.0)
    assert result["final_equity"] == pytest.approx(expected)
    assert result["n_taken"] == 1
    assert result["n_skipped"] == 0


def test_drawdown_peak_to_trough() -> None:
    start = 100_000.0
    cost = 0.0
    trades = [
        ("2020-01-01", 10, 10.0),
        ("2020-02-01", 10, -20.0),
    ]
    result = backtest_portfolio(
        trades,
        start_equity=start,
        max_concurrent=1,
        per_trade_fraction=1.0,
        max_gross_exposure=1.0,
        cost_pct=cost,
    )
    peak = start * 1.10
    trough = peak * 0.80
    expected_dd = (peak - trough) / peak
    assert result["final_equity"] == pytest.approx(trough)
    assert result["max_drawdown"] == pytest.approx(expected_dd, rel=1e-6)


def test_concurrency_cap() -> None:
    same_day = "2020-01-01"
    trades = [(same_day, 5, 1.0) for _ in range(15)]
    result = backtest_portfolio(
        trades,
        start_equity=100_000.0,
        max_concurrent=10,
        per_trade_fraction=0.05,
        max_gross_exposure=1.0,
    )
    assert result["n_taken"] == 10
    assert result["n_skipped"] == 5


def test_gross_exposure_cap() -> None:
    same_day = "2020-01-01"
    trades = [(same_day, 5, 1.0) for _ in range(5)]
    result = backtest_portfolio(
        trades,
        start_equity=100_000.0,
        max_concurrent=10,
        per_trade_fraction=0.5,
        max_gross_exposure=1.0,
    )
    assert result["n_taken"] == 2
    assert result["n_skipped"] == 3


def test_risk_sizing_notional() -> None:
    kwargs = dict(
        start_equity=100_000.0,
        max_concurrent=1,
        per_trade_fraction=0.05,
        sizing="risk",
        stop_pct=3.0,
        cost_pct=0.0,
    )
    target_frac = 0.05 / 0.03  # 1.667x equity
    below = backtest_portfolio(_single_trade(0.0), max_gross_exposure=target_frac - 0.01, **kwargs)
    at_target = backtest_portfolio(_single_trade(0.0), max_gross_exposure=target_frac + 0.01, **kwargs)
    assert below["n_taken"] == 0
    assert at_target["n_taken"] == 1


def test_cost_applied_on_zero_gross_trade() -> None:
    start = 100_000.0
    cost = 0.30
    result = backtest_portfolio(
        _single_trade(0.0),
        start_equity=start,
        max_concurrent=1,
        per_trade_fraction=1.0,
        max_gross_exposure=1.0,
        cost_pct=cost,
    )
    expected = start * (1.0 - cost / 100.0)
    assert result["final_equity"] == pytest.approx(expected)


def test_buy_and_hold_benchmark(tmp_path: Path) -> None:
    bars: list[DatedBar] = []
    start = date(2020, 1, 1)
    closes = [100.0, 102.0, 104.0, 103.0, 110.0]
    for i, close in enumerate(closes):
        d = (start + timedelta(days=i)).isoformat()
        bars.append({"d": d, "o": close, "h": close, "l": close, "c": close, "v": 1_000.0})

    db_path = tmp_path / "bars.db"
    cache_bars("SPY", bars, source="norgate", db_path=db_path)

    bench = buy_and_hold_benchmark(
        "SPY",
        bars[0]["d"],
        bars[-1]["d"],
        source="norgate",
        bars_db_path=db_path,
        start_equity=100_000.0,
    )
    assert bench["total_return_pct"] == pytest.approx(10.0)
    assert bench["final_equity"] == pytest.approx(110_000.0)


def test_sharpe_positive_and_flat() -> None:
    # Steady gains via sequential non-overlapping winners.
    trades = [(f"2020-{m:02d}-01", 20, 2.0) for m in range(1, 7)]
    positive = backtest_portfolio(
        trades,
        start_equity=100_000.0,
        max_concurrent=1,
        per_trade_fraction=1.0,
        max_gross_exposure=1.0,
        cost_pct=0.0,
    )
    assert positive["sharpe"] > 0

    flat = backtest_portfolio(
        _single_trade(0.0, holding_days=1),
        start_equity=100_000.0,
        max_concurrent=1,
        per_trade_fraction=0.0,
        max_gross_exposure=1.0,
        cost_pct=0.0,
    )
    assert flat["sharpe"] == 0.0
    assert flat["sortino"] == 0.0


def test_metric_keys_present() -> None:
    result = backtest_portfolio(_single_trade(5.0))
    expected_keys = {
        "start_equity",
        "final_equity",
        "total_return_pct",
        "years",
        "cagr",
        "max_drawdown",
        "sharpe",
        "sortino",
        "n_taken",
        "n_skipped",
        "pct_signals_taken",
        "avg_pct_invested",
        "daily_equity",
    }
    assert expected_keys <= set(result.keys())


def test_avg_pct_invested_gap_after_short_trade() -> None:
    """Regression: deployed must drop at calendar exit, not at the next entry."""
    entry = date(2020, 1, 1)
    gap_entry = entry + timedelta(days=200)
    holding = 5
    trades = [
        (entry.isoformat(), holding, 5.0),
        (gap_entry.isoformat(), holding, 5.0),
    ]
    result = backtest_portfolio(
        trades,
        start_equity=100_000.0,
        max_concurrent=1,
        per_trade_fraction=1.0,
        max_gross_exposure=1.0,
        cost_pct=0.0,
    )
    total_days = (gap_entry.toordinal() + holding) - entry.toordinal() + 1
    position_days = 2 * holding
    expected_avg = position_days / total_days
    assert result["avg_pct_invested"] == pytest.approx(expected_avg, rel=0.05)
    assert result["avg_pct_invested"] < 0.10


def test_daily_equity_matches_final_equity() -> None:
    trades = [
        ("2020-01-01", 10, 10.0),
        ("2020-02-01", 10, -5.0),
        ("2020-04-01", 15, 3.0),
    ]
    result = backtest_portfolio(
        trades,
        start_equity=100_000.0,
        max_concurrent=2,
        per_trade_fraction=0.5,
        max_gross_exposure=1.0,
        cost_pct=0.30,
    )
    assert result["daily_equity"]
    assert result["daily_equity"][-1][1] == pytest.approx(result["final_equity"])


def test_avg_pct_invested_sanity() -> None:
    isolated = backtest_portfolio(
        _single_trade(5.0, holding_days=5),
        start_equity=100_000.0,
        max_concurrent=1,
        per_trade_fraction=0.10,
        max_gross_exposure=1.0,
        cost_pct=0.0,
    )
    assert isolated["avg_pct_invested"] == pytest.approx(0.10 * 5 / 6, rel=0.01)

    overlapping = backtest_portfolio(
        [(f"2020-{m:02d}-01", 25, 1.0) for m in range(1, 13)],
        start_equity=100_000.0,
        max_concurrent=1,
        per_trade_fraction=1.0,
        max_gross_exposure=1.0,
        cost_pct=0.0,
    )
    assert overlapping["avg_pct_invested"] > 0.80
