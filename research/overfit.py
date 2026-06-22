"""Overfitting diagnostics: Deflated Sharpe Ratio and PBO via CSCV."""

from __future__ import annotations

import itertools
import math

import numpy as np
from scipy.stats import kurtosis, norm, skew

_EULER_MASCHERONI = 0.5772156649


def probabilistic_sharpe_ratio(
    sr: float,
    sr_benchmark: float,
    n: int,
    skewness: float,
    kurt: float,
) -> float:
    """PSR = Phi( (sr - sr_benchmark) * sqrt(n - 1) / sqrt(1 - skew*sr + ((kurt - 1)/4)*sr**2) ).

    sr and sr_benchmark are per-observation Sharpes; kurt is non-excess kurtosis; Phi = std normal CDF.
    """
    if n < 2:
        return float("nan")

    numerator = (sr - sr_benchmark) * math.sqrt(n - 1)
    denominator = math.sqrt(1.0 - skewness * sr + ((kurt - 1.0) / 4.0) * sr**2)
    if denominator <= 0:
        return float("nan")

    z = numerator / denominator
    return float(norm.cdf(z))


def expected_max_sharpe(n_trials: int, sr_variance: float) -> float:
    """E[max SR] under the null across N independent trials (per-observation units)."""
    if n_trials < 1:
        raise ValueError("n_trials must be >= 1")
    if sr_variance < 0:
        raise ValueError("sr_variance must be >= 0")

    n = float(n_trials)
    g = _EULER_MASCHERONI
    z1 = norm.ppf(1.0 - 1.0 / n)
    z2 = norm.ppf(1.0 - 1.0 / (n * math.e))
    return float(math.sqrt(sr_variance) * ((1.0 - g) * z1 + g * z2))


def _per_obs_sharpe(returns: np.ndarray) -> float:
    std = float(np.std(returns, ddof=1))
    if std == 0.0:
        return 0.0
    return float(np.mean(returns) / std)


def deflated_sharpe_ratio(
    returns: np.ndarray,
    *,
    n_trials: int,
    sr_variance: float | None = None,
) -> dict:
    """Deflated Sharpe Ratio for a selected strategy's per-period returns."""
    if sr_variance is None:
        raise ValueError("sr_variance is required")

    series = np.asarray(returns, dtype=float).ravel()
    n = len(series)
    if n < 2:
        raise ValueError("returns must contain at least 2 observations")

    sr = _per_obs_sharpe(series)
    series_skew = float(skew(series, bias=False))
    series_kurt = float(kurtosis(series, fisher=False, bias=False))
    sr_star = expected_max_sharpe(n_trials, sr_variance)
    dsr = probabilistic_sharpe_ratio(sr, sr_star, n, series_skew, series_kurt)

    return {
        "sr_per_obs": sr,
        "sr_annual": sr * math.sqrt(252.0),
        "skew": series_skew,
        "kurt": series_kurt,
        "n": n,
        "sr_star": sr_star,
        "dsr": dsr,
    }


def _strategy_sharpe(block: np.ndarray) -> np.ndarray:
    """Per-strategy Sharpe over rows in block, shape (T, M) -> (M,)."""
    means = block.mean(axis=0)
    stds = block.std(axis=0, ddof=1)
    with np.errstate(divide="ignore", invalid="ignore"):
        sharpes = np.where(stds > 0, means / stds, 0.0)
    return sharpes


def _oos_rank(is_best: int, oos_sharpes: np.ndarray) -> float:
    """Relative OOS rank r in (0, 1): rank/(M+1), rank 1 = worst, M = best."""
    m = len(oos_sharpes)
    order = np.argsort(oos_sharpes, kind="mergesort")
    ranks = np.empty(m, dtype=float)
    ranks[order] = np.arange(1, m + 1, dtype=float)

    # Break ties by averaging ranks (standard competition ranking).
    for value in np.unique(oos_sharpes):
        tied = np.where(oos_sharpes == value)[0]
        if len(tied) > 1:
            avg_rank = float(np.mean(ranks[tied]))
            ranks[tied] = avg_rank

    return float(ranks[is_best] / (m + 1))


def pbo_cscv(
    returns_matrix: np.ndarray,
    *,
    n_splits: int = 16,
    metric: str = "sharpe",
) -> dict:
    """Probability of Backtest Overfitting via Combinatorially Symmetric Cross-Validation."""
    if metric != "sharpe":
        raise ValueError(f"unsupported metric: {metric}")

    matrix = np.asarray(returns_matrix, dtype=float)
    if matrix.ndim != 2:
        raise ValueError("returns_matrix must be 2D (T observations, M strategies)")

    t_obs, m_strategies = matrix.shape
    if m_strategies < 2:
        raise ValueError("returns_matrix must have at least 2 strategies (columns)")
    if n_splits < 2 or n_splits % 2 != 0:
        raise ValueError("n_splits must be an even integer >= 2")

    usable_rows = (t_obs // n_splits) * n_splits
    if usable_rows == 0:
        raise ValueError("not enough rows to form n_splits equal blocks")

    block_size = usable_rows // n_splits
    if block_size < 2:
        raise ValueError("each block must contain at least 2 observations for Sharpe")

    trimmed = matrix[:usable_rows]
    blocks = trimmed.reshape(n_splits, block_size, m_strategies)
    half = n_splits // 2

    lambdas: list[float] = []
    for is_indices in itertools.combinations(range(n_splits), half):
        is_mask = np.zeros(n_splits, dtype=bool)
        is_mask[list(is_indices)] = True

        is_rows = blocks[is_mask].reshape(-1, m_strategies)
        oos_rows = blocks[~is_mask].reshape(-1, m_strategies)

        is_sharpes = _strategy_sharpe(is_rows)
        oos_sharpes = _strategy_sharpe(oos_rows)
        is_best = int(np.argmax(is_sharpes))

        rank = _oos_rank(is_best, oos_sharpes)
        if rank <= 0.0 or rank >= 1.0:
            lambdas.append(0.0 if rank <= 0.5 else float("inf"))
        else:
            lambdas.append(float(math.log(rank / (1.0 - rank))))

    n_combinations = len(lambdas)
    pbo = float(np.mean(np.array(lambdas) <= 0.0))
    median_logit = float(np.median(lambdas))

    return {
        "pbo": pbo,
        "n_combinations": n_combinations,
        "median_logit": median_logit,
        "lambdas": lambdas,
    }
