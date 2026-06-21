"""Score model fitting, out-of-fold predictions, and Platt calibration."""

from __future__ import annotations

import numpy as np
from sklearn.linear_model import LogisticRegression


def fit_score_model(X: np.ndarray, y: np.ndarray) -> LogisticRegression:
    """Fit a transparent logistic regression score model."""
    model = LogisticRegression(
        C=1.0,
        class_weight=None,
        max_iter=1000,
        random_state=42,
    )
    model.fit(X, y)
    return model


def out_of_fold_scores(
    X: np.ndarray,
    y: np.ndarray,
    folds: list[tuple[np.ndarray, np.ndarray]],
) -> np.ndarray:
    """Per-sample OOF raw scores — each sample scored by a model not trained on it."""
    n = len(y)
    oof = np.full(n, np.nan, dtype=float)
    for train_idx, test_idx in folds:
        model = fit_score_model(X[train_idx], y[train_idx])
        oof[test_idx] = model.predict_proba(X[test_idx])[:, 1]
    if np.any(np.isnan(oof)):
        msg = "OOF scores contain NaN — folds did not cover all samples"
        raise ValueError(msg)
    return oof


def fit_platt(oof_scores: np.ndarray, y: np.ndarray) -> dict:
    """Fit a 1-D Platt sigmoid on OOF scores."""
    calibrator = LogisticRegression(C=1e10, max_iter=1000)
    calibrator.fit(oof_scores.reshape(-1, 1), y)
    return {
        "A": float(calibrator.coef_[0, 0]),
        "B": float(calibrator.intercept_[0]),
    }


def calibrate(scores: np.ndarray, platt: dict) -> np.ndarray:
    """Apply Platt scaling to raw scores."""
    logits = platt["A"] * scores + platt["B"]
    return 1.0 / (1.0 + np.exp(-logits))


def calibration_metrics(
    probs: np.ndarray,
    y: np.ndarray,
    *,
    n_bins: int = 10,
) -> dict:
    """Brier score, base rate, and reliability table."""
    base_rate = float(np.mean(y))
    brier = float(np.mean((probs - y) ** 2))

    bin_edges = np.linspace(0.0, 1.0, n_bins + 1)
    reliability: list[dict] = []
    for i in range(n_bins):
        lo, hi = bin_edges[i], bin_edges[i + 1]
        if i < n_bins - 1:
            mask = (probs >= lo) & (probs < hi)
        else:
            mask = (probs >= lo) & (probs <= hi)
        count = int(np.sum(mask))
        if count == 0:
            reliability.append(
                {
                    "bin_lo": float(lo),
                    "bin_hi": float(hi),
                    "mean_predicted": None,
                    "empirical_rate": None,
                    "n": 0,
                }
            )
            continue
        bin_probs = probs[mask]
        bin_y = y[mask]
        reliability.append(
            {
                "bin_lo": float(lo),
                "bin_hi": float(hi),
                "mean_predicted": float(np.mean(bin_probs)),
                "empirical_rate": float(np.mean(bin_y)),
                "n": count,
            }
        )

    return {
        "base_rate": base_rate,
        "brier": brier,
        "reliability": reliability,
    }
