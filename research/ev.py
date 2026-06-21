"""Expected-value lookup table with Wilson confidence intervals."""

from __future__ import annotations

import math

import numpy as np


def wilson_ci(successes: int, n: int, *, z: float = 1.96) -> tuple[float, float]:
    """Wilson score 95% interval for a binomial proportion."""
    if n == 0:
        return (0.0, 1.0)
    p = successes / n
    z2 = z * z
    denom = 1.0 + z2 / n
    center = (p + z2 / (2.0 * n)) / denom
    margin = z * math.sqrt((p * (1.0 - p) / n) + (z2 / (4.0 * n * n))) / denom
    return (max(0.0, center - margin), min(1.0, center + margin))


def build_ev_table(
    probs: np.ndarray,
    y: np.ndarray,
    returns: np.ndarray,
    *,
    bins: list[float] | None = None,
    min_bucket: int = 30,
    winsorize_pct: float = 0.01,
) -> list[dict]:
    """Bucket calibrated probabilities into an honest EV / win-rate table."""
    if bins is None:
        bins = [i / 10 for i in range(11)]

    if len(returns) > 0 and winsorize_pct > 0.0:
        lo_clip = float(np.quantile(returns, winsorize_pct))
        hi_clip = float(np.quantile(returns, 1.0 - winsorize_pct))
    else:
        lo_clip = hi_clip = None

    table: list[dict] = []
    for i in range(len(bins) - 1):
        lo, hi = bins[i], bins[i + 1]
        if i < len(bins) - 2:
            mask = (probs >= lo) & (probs < hi)
        else:
            mask = (probs >= lo) & (probs <= hi)

        idx = np.where(mask)[0]
        n = int(len(idx))
        sufficient = n >= min_bucket

        if n == 0:
            table.append(
                {
                    "bin_lo": lo,
                    "bin_hi": hi,
                    "n": 0,
                    "calibrated_winrate": None,
                    "empirical_winrate": None,
                    "wilson_ci_lo": None,
                    "wilson_ci_hi": None,
                    "avg_win_return": None,
                    "avg_loss_return": None,
                    "ev": None,
                    "sufficient": False,
                    "flag": "insufficient_data",
                }
            )
            continue

        bucket_y = y[idx]
        bucket_probs = probs[idx]
        bucket_returns = returns[idx]
        if lo_clip is not None and hi_clip is not None:
            bucket_returns = np.clip(bucket_returns, lo_clip, hi_clip)
        wins = bucket_y == 1
        losses = bucket_y == 0

        successes = int(np.sum(wins))
        empirical = float(np.mean(bucket_y))
        ci_lo, ci_hi = wilson_ci(successes, n)

        avg_win = float(np.mean(bucket_returns[wins])) if successes > 0 else 0.0
        loss_count = int(np.sum(losses))
        avg_loss = float(np.mean(bucket_returns[losses])) if loss_count > 0 else 0.0
        ev = empirical * avg_win + (1.0 - empirical) * avg_loss

        table.append(
            {
                "bin_lo": lo,
                "bin_hi": hi,
                "n": n,
                "calibrated_winrate": float(np.mean(bucket_probs)),
                "empirical_winrate": empirical,
                "wilson_ci_lo": ci_lo,
                "wilson_ci_hi": ci_hi,
                "avg_win_return": avg_win,
                "avg_loss_return": avg_loss,
                "ev": ev,
                "sufficient": sufficient,
                "flag": None if sufficient else "insufficient_data",
            }
        )

    return table
