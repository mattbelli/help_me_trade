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


def qlike(sigma: np.ndarray, realized_return: np.ndarray) -> np.ndarray:
    """QLIKE loss: log(sigma^2) + r^2 / sigma^2, lower is better.

    Standard for volatility forecasts because it stays robust when the realized
    proxy is noisy — a single squared return is an unbiased but very noisy estimate
    of the horizon's variance, and MSE over-punishes forecasts on the few points
    where that noise is large.
    """
    variance = sigma**2
    return np.log(variance) + realized_return**2 / variance


def summarize_volatility(records: list[dict]) -> dict:
    """Score Kronos's implied sigma against EWMA and GARCH(1,1) on the same points.

    Separate question from the path test, with its own pre-registered decision rule.
    A win here does NOT substitute for the path verdict, or vice versa — they are
    reported independently so neither can launder the other's null result.
    """
    usable = [
        r
        for r in records
        if r.get("kronos_sigma", 0) > 0 and r.get("ewma_sigma", 0) > 0
    ]
    if not usable:
        return {"n_points": 0, "verdict": "NO DATA"}

    dates = np.array([r["session_date"] for r in usable])
    realized = np.array([r["realized_return"] for r in usable])
    losses = {
        arm: qlike(np.array([r[f"{arm}_sigma"] for r in usable]), realized)
        for arm in ("kronos", "ewma")
    }

    out: dict = {"n_points": len(usable)}
    for arm, loss in losses.items():
        out[arm] = {"qlike": float(loss.mean())}

    # GARCH can fail to converge on individual points; score it on its own subset
    # and, to keep the comparison paired, against Kronos restricted to that subset.
    garch_mask = np.array([r.get("garch_sigma") is not None for r in usable])
    if garch_mask.sum() >= MIN_SESSION_DATES:
        garch_sigma = np.array(
            [r["garch_sigma"] for r in usable if r.get("garch_sigma") is not None]
        )
        garch_loss = qlike(garch_sigma, realized[garch_mask])
        out["garch"] = {
            "qlike": float(garch_loss.mean()),
            "n_points": int(garch_mask.sum()),
            "fit_failure_rate": float(1 - garch_mask.mean()),
        }
        mean, lo, hi = paired_ci(
            losses["kronos"][garch_mask] - garch_loss, dates[garch_mask]
        )
        out["kronos_vs_garch"] = {"mean_qlike_delta": mean, "ci_low": lo, "ci_high": hi}

    mean, lo, hi = paired_ci(losses["kronos"] - losses["ewma"], dates)
    out["kronos_vs_ewma"] = {"mean_qlike_delta": mean, "ci_low": lo, "ci_high": hi}

    n_dates = int(np.unique(dates).size)
    out["n_session_dates"] = n_dates
    if n_dates < MIN_SESSION_DATES:
        out["verdict"] = "INSUFFICIENT DATA"
    else:
        beats = [
            out[k]["mean_qlike_delta"] < 0 and out[k]["ci_high"] < 0
            for k in ("kronos_vs_ewma", "kronos_vs_garch")
            if k in out
        ]
        out["verdict"] = "PASS" if beats and all(beats) else "FAIL"
    return out


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
