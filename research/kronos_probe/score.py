"""Scoring: paired log-loss / Brier with a block-bootstrap CI over session dates.

Probabilities are Laplace-smoothed ((count+1)/(n+3)) before scoring. A Monte Carlo
arm can assign exactly zero to an outcome that then happens, which would make
log-loss infinite; smoothing is applied identically to both arms so the paired
comparison stays fair.
"""

from __future__ import annotations

import numpy as np

from research.kronos_probe.barriers import N_OUTCOMES

N_RESAMPLES = 2000

# The CI resamples whole session dates, so with only a handful of distinct dates it
# collapses to a point estimate — a 30-point smoke run over ONE session produced
# ci_low == ci_high == mean and still read "PASS". Below this many dates the probe
# reports INSUFFICIENT DATA instead of a verdict it cannot support.
MIN_SESSION_DATES = 20


def smooth(counts: np.ndarray) -> np.ndarray:
    return (counts + 1.0) / (counts.sum() + N_OUTCOMES)


def log_loss(probs: np.ndarray, actual: int) -> float:
    return float(-np.log(probs[actual]))


def brier(probs: np.ndarray, actual: int) -> float:
    onehot = np.zeros(N_OUTCOMES)
    onehot[actual] = 1.0
    return float(np.sum((probs - onehot) ** 2))


def paired_ci(
    diffs: np.ndarray, dates: np.ndarray, *, seed: int = 20260724
) -> tuple[float, float, float]:
    """Mean of ``diffs`` with a 95% CI, resampling whole session dates.

    Evaluation points inside one session are correlated, so the resampling unit is
    the date, not the point.
    """
    rng = np.random.default_rng(seed)
    unique = np.unique(dates)
    by_date = [diffs[dates == d] for d in unique]
    means = np.empty(N_RESAMPLES)
    for k in range(N_RESAMPLES):
        pick = rng.integers(0, len(by_date), size=len(by_date))
        means[k] = np.concatenate([by_date[p] for p in pick]).mean()
    lo, hi = np.percentile(means, [2.5, 97.5])
    return float(diffs.mean()), float(lo), float(hi)


def summarize(records: list[dict]) -> dict:
    """Score every evaluation point and apply the pre-registered decision rule."""
    if not records:
        return {"n_points": 0, "verdict": "NO DATA"}

    dates = np.array([r["session_date"] for r in records])
    actual = np.array([r["actual"] for r in records], dtype=int)

    out: dict = {"n_points": len(records)}
    losses: dict[str, np.ndarray] = {}
    for arm in ("kronos", "bootstrap"):
        probs = np.array([smooth(np.asarray(r[f"{arm}_counts"])) for r in records])
        losses[arm] = np.array(
            [log_loss(p, a) for p, a in zip(probs, actual, strict=True)]
        )
        briers = np.array([brier(p, a) for p, a in zip(probs, actual, strict=True)])
        out[arm] = {
            "log_loss": float(losses[arm].mean()),
            "brier": float(briers.mean()),
            "mean_p_up": float(probs[:, 0].mean()),
            "mean_p_down": float(probs[:, 1].mean()),
            "mean_p_timeout": float(probs[:, 2].mean()),
        }

    diffs = losses["kronos"] - losses["bootstrap"]
    mean, lo, hi = paired_ci(diffs, dates)
    out["difference"] = {
        "mean_log_loss_delta": mean,
        "ci_low": lo,
        "ci_high": hi,
        "note": "negative favours Kronos",
    }
    out["actual_rates"] = {
        "up": float((actual == 0).mean()),
        "down": float((actual == 1).mean()),
        "timeout": float((actual == 2).mean()),
    }
    n_dates = int(np.unique(dates).size)
    out["n_session_dates"] = n_dates
    if n_dates < MIN_SESSION_DATES:
        out["verdict"] = "INSUFFICIENT DATA"
        out["verdict_note"] = (
            f"{n_dates} session dates < {MIN_SESSION_DATES}; the paired CI cannot "
            "be trusted at this sample size. Not a result."
        )
    else:
        out["verdict"] = "PASS" if mean < 0 and hi < 0 else "FAIL"
    return out
