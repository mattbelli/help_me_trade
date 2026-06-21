"""Tests for overlap-aware block-bootstrap significance testing."""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np

from research.significance import (
    aggregate_daily,
    moving_block_bootstrap_mean,
    significance_report,
)


def test_aggregate_daily_groups_and_sorts() -> None:
    dates = ["2024-01-03", "2024-01-01", "2024-01-01", "2024-01-03"]
    returns = [2.0, 1.0, 3.0, 4.0]

    sorted_dates, daily_means = aggregate_daily(dates, returns)

    assert sorted_dates == ["2024-01-01", "2024-01-03"]
    np.testing.assert_allclose(daily_means, [2.0, 3.0])


def test_strong_positive_signal_is_significant() -> None:
    rng = np.random.default_rng(42)
    start = date(2020, 1, 1)
    dates = [(start + timedelta(days=i)).isoformat() for i in range(120)]
    returns = rng.normal(1.0, 0.05, size=120).tolist()

    report = significance_report(dates, returns, block_size=10, n_boot=5000, seed=42)

    assert report["block_ci"][0] is not None
    assert report["block_ci"][0] > 0
    assert report["p_le_zero"] is not None
    assert report["p_le_zero"] < 0.05
    assert report["significant"] is True


def test_zero_mean_noise_not_significant() -> None:
    rng = np.random.default_rng(7)
    start = date(2019, 6, 1)
    dates = [(start + timedelta(days=i)).isoformat() for i in range(120)]
    raw = rng.normal(0.0, 1.0, size=120)
    returns = (raw - raw.mean()).tolist()

    report = significance_report(dates, returns, block_size=10, n_boot=5000, seed=42)

    assert report["block_ci"][0] is not None
    assert report["block_ci"][1] is not None
    assert report["block_ci"][0] < 0 < report["block_ci"][1]
    assert report["p_le_zero"] is not None
    assert 0.35 < report["p_le_zero"] < 0.65
    assert report["significant"] is False


def test_bootstrap_is_deterministic_for_fixed_seed() -> None:
    rng = np.random.default_rng(99)
    series = rng.normal(0.5, 0.2, size=80)

    first = moving_block_bootstrap_mean(series, block_size=10, n_boot=2000, seed=123)
    second = moving_block_bootstrap_mean(series, block_size=10, n_boot=2000, seed=123)

    assert first["ci_lo"] == second["ci_lo"]
    assert first["ci_hi"] == second["ci_hi"]
    assert first["p_le_zero"] == second["p_le_zero"]


def test_larger_block_size_yields_wider_ci_for_autocorrelated_series() -> None:
    n = 120
    series = np.zeros(n)
    rng = np.random.default_rng(123)
    for i in range(1, n):
        series[i] = 0.85 * series[i - 1] + rng.normal(0.0, 0.4)

    narrow = moving_block_bootstrap_mean(series, block_size=1, n_boot=5000, seed=42)
    wide = moving_block_bootstrap_mean(series, block_size=20, n_boot=5000, seed=42)

    width_narrow = narrow["ci_hi"] - narrow["ci_lo"]
    width_wide = wide["ci_hi"] - wide["ci_lo"]
    assert width_wide > width_narrow


def test_naive_iid_ci_narrower_than_block_ci_on_clustered_trades() -> None:
    rng = np.random.default_rng(55)
    dates: list[str] = []
    returns: list[float] = []
    start = date(2018, 1, 1)
    for i in range(60):
        day = (start + timedelta(days=i)).isoformat()
        day_return = 0.5 + float(rng.normal(0.0, 0.08))
        for _ in range(25):
            dates.append(day)
            returns.append(day_return)

    report = significance_report(dates, returns, block_size=10, n_boot=5000, seed=42)

    naive_width = report["naive_iid_ci"][1] - report["naive_iid_ci"][0]
    block_width = report["block_ci"][1] - report["block_ci"][0]
    assert naive_width < block_width


def test_empty_input_does_not_crash() -> None:
    report = significance_report([], [])

    assert report["significant"] is False
    assert report["per_trade_mean"] is None
    assert report["block_ci"] == (None, None)


def test_single_date_input_does_not_crash() -> None:
    report = significance_report(["2024-01-01"], [0.5])

    assert report["significant"] is False
    assert report["n_trades"] == 1
    assert report["n_dates"] == 1
    assert report["block_ci"] == (None, None)


def test_single_point_bootstrap_does_not_crash() -> None:
    boot = moving_block_bootstrap_mean(np.array([1.2]), block_size=10, n_boot=100, seed=1)

    assert boot["observed_mean"] == 1.2
    assert boot["p_le_zero"] == 0.0
