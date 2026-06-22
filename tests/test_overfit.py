"""Tests for Deflated Sharpe Ratio and PBO via CSCV."""

from __future__ import annotations

import numpy as np
from scipy.stats import kurtosis, skew

from research.overfit import (
    deflated_sharpe_ratio,
    expected_max_sharpe,
    pbo_cscv,
    probabilistic_sharpe_ratio,
)


def test_psr_positive_series_near_one() -> None:
    rng = np.random.default_rng(42)
    returns = rng.normal(0.01, 0.005, size=500)
    sr = float(np.mean(returns) / np.std(returns, ddof=1))
    series_skew = float(np.mean(((returns - returns.mean()) / returns.std(ddof=1)) ** 3))
    series_kurt = float(np.mean(((returns - returns.mean()) / returns.std(ddof=1)) ** 4))

    psr = probabilistic_sharpe_ratio(sr, 0.0, len(returns), series_skew, series_kurt)

    assert psr > 0.99


def test_psr_zero_mean_series_near_half() -> None:
    rng = np.random.default_rng(7)
    returns = rng.normal(0.0, 0.01, size=500)
    returns = returns - returns.mean()
    sr = float(np.mean(returns) / np.std(returns, ddof=1))
    series_skew = float(skew(returns, bias=False))
    series_kurt = float(kurtosis(returns, fisher=False, bias=False))

    psr = probabilistic_sharpe_ratio(sr, 0.0, len(returns), series_skew, series_kurt)

    assert 0.45 < psr < 0.55


def test_dsr_decreases_with_more_trials() -> None:
    rng = np.random.default_rng(123)
    returns = rng.normal(0.002, 0.01, size=252)
    sr_variance = 0.01

    low_trials = deflated_sharpe_ratio(returns, n_trials=1, sr_variance=sr_variance)
    high_trials = deflated_sharpe_ratio(returns, n_trials=1000, sr_variance=sr_variance)

    assert high_trials["sr_star"] > low_trials["sr_star"]
    assert high_trials["dsr"] < low_trials["dsr"]


def test_dsr_marginal_sharpe_fails_with_many_trials() -> None:
    rng = np.random.default_rng(99)
    returns = rng.normal(0.0005, 0.01, size=252)

    result = deflated_sharpe_ratio(returns, n_trials=500, sr_variance=0.02)

    assert result["dsr"] < 0.95


def test_expected_max_sharpe_increases_with_n_trials() -> None:
    sr_variance = 0.05
    low = expected_max_sharpe(5, sr_variance)
    high = expected_max_sharpe(500, sr_variance)

    assert high > low


def test_pbo_pure_noise_is_high() -> None:
    rng = np.random.default_rng(42)
    t_obs, m_strategies = 800, 20
    matrix = rng.normal(0.0, 0.01, size=(t_obs, m_strategies))

    result = pbo_cscv(matrix, n_splits=16)

    assert result["n_combinations"] == 12870
    assert result["pbo"] > 0.4


def test_pbo_real_edge_is_low() -> None:
    rng = np.random.default_rng(2024)
    t_obs, m_strategies = 800, 20
    matrix = rng.normal(0.0, 0.01, size=(t_obs, m_strategies))
    matrix[:, 0] += 0.003

    result = pbo_cscv(matrix, n_splits=16)

    assert result["pbo"] < 0.2


def test_pbo_requires_multiple_strategies() -> None:
    matrix = np.random.default_rng(1).normal(0.0, 0.01, size=(100, 1))

    try:
        pbo_cscv(matrix, n_splits=4)
    except ValueError as exc:
        assert "at least 2 strategies" in str(exc)
    else:
        raise AssertionError("expected ValueError for single-strategy matrix")
