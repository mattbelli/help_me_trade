"""Load labeled setups from SQLite and build a featurized matrix for training."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from db.base import connect
from research.dataset import DEFAULT_LABELED_DB_PATH

NUMERIC_FEATURES = [
    "rsi_14",
    "atr_14",
    "dist_from_sma50_pct",
    "dist_from_sma200_pct",
    "dist_from_52w_high_pct",
    "prior_return_1d",
    "prior_return_3d",
    "prior_return_5d",
    "pct_change_1d",
    "pct_change_5d",
    "pct_change_21d",
    "vol_vs_avg_20",
    "gap_prior",
    "dist_from_high_252",
    "rel_strength_21",
]

ENTRY_CONTEXT_CATEGORIES = [
    "pullback",
    "dip_below_trend",
    "breakout",
    "momentum",
    "neutral",
]

CATEGORICAL_BELOW_FEATURES = ("vs_sma50_below", "vs_sma200_below")


def _categorical_feature_names() -> list[str]:
    names = [f"entry_context_{cat}" for cat in ENTRY_CONTEXT_CATEGORIES]
    names.extend(CATEGORICAL_BELOW_FEATURES)
    return names


def feature_names() -> list[str]:
    """Ordered feature column names (numerics then one-hot categoricals)."""
    return list(NUMERIC_FEATURES) + _categorical_feature_names()


def _encode_row(raw: dict, medians: dict[str, float]) -> list[float]:
    row: list[float] = []
    for name in NUMERIC_FEATURES:
        val = raw.get(name)
        if val is None:
            val = medians[name]
        row.append(float(val))

    entry_ctx = raw.get("entry_context", "neutral")
    for cat in ENTRY_CONTEXT_CATEGORIES:
        row.append(1.0 if entry_ctx == cat else 0.0)

    row.append(1.0 if raw.get("vs_sma50") == "below" else 0.0)
    row.append(1.0 if raw.get("vs_sma200") == "below" else 0.0)
    return row


def _standardize_matrix(
    matrix: np.ndarray,
    numeric_count: int,
    means: np.ndarray,
    stds: np.ndarray,
) -> np.ndarray:
    out = matrix.copy()
    out[:, :numeric_count] = (out[:, :numeric_count] - means) / stds
    return out


def fit_preprocessing(raw_matrix: np.ndarray) -> dict:
    """Fit imputation medians and z-score stats on raw rows (NaN = missing).

    When cross-validating, fit this on the training-fold rows ONLY and apply
    to both fold halves (see research.train.purged_oof_scores) — fitting on
    the full sample leaks test-fold distribution into the transform.
    """
    numeric_count = len(NUMERIC_FEATURES)
    medians: dict[str, float] = {}
    imputed = raw_matrix[:, :numeric_count].copy()
    for j, name in enumerate(NUMERIC_FEATURES):
        col = imputed[:, j]
        finite = col[np.isfinite(col)]
        median = float(np.median(finite)) if len(finite) else 0.0
        medians[name] = median
        col[~np.isfinite(col)] = median

    means = imputed.mean(axis=0)
    stds = imputed.std(axis=0)
    stds = np.where(stds == 0, 1.0, stds)

    return {
        "numeric_features": list(NUMERIC_FEATURES),
        "medians": medians,
        "means": {n: float(means[i]) for i, n in enumerate(NUMERIC_FEATURES)},
        "stds": {n: float(stds[i]) for i, n in enumerate(NUMERIC_FEATURES)},
        "entry_context_categories": list(ENTRY_CONTEXT_CATEGORIES),
    }


def apply_preprocessing(raw_matrix: np.ndarray, preprocessing: dict) -> np.ndarray:
    """Impute NaNs and standardize raw rows using previously fit stats."""
    numeric_count = len(NUMERIC_FEATURES)
    out = raw_matrix.copy()
    medians = preprocessing["medians"]
    for j, name in enumerate(NUMERIC_FEATURES):
        col = out[:, j]
        col[~np.isfinite(col)] = medians[name]
    means = np.array([preprocessing["means"][n] for n in NUMERIC_FEATURES])
    stds = np.array([preprocessing["stds"][n] for n in NUMERIC_FEATURES])
    return _standardize_matrix(out, numeric_count, means, stds)


def load_labeled_dataset(
    db_path: Path = DEFAULT_LABELED_DB_PATH,
    *,
    min_rows: int = 1,
) -> dict:
    """Load labeled_setups, featurize, and sort by entry_date.

    Returns both X_raw (NaN = missing; refit preprocessing per CV fold on this)
    and X standardized with FULL-SAMPLE stats — the latter is only for the final
    deployed model / live-scorer contract, never for cross-validation.
    """
    conn = connect(db_path)
    try:
        rows = conn.execute(
            """
            SELECT entry_date, features_json, label, return_pct, holding_days
            FROM labeled_setups
            ORDER BY entry_date ASC
            """
        ).fetchall()
    finally:
        conn.close()

    if len(rows) < min_rows:
        msg = f"Need at least {min_rows} labeled rows, found {len(rows)}"
        raise ValueError(msg)

    parsed = [json.loads(row["features_json"]) for row in rows]
    raw_matrix = np.array(
        [_encode_row(raw, {n: np.nan for n in NUMERIC_FEATURES}) for raw in parsed],
        dtype=float,
    )

    preprocessing = fit_preprocessing(raw_matrix)
    x = apply_preprocessing(raw_matrix, preprocessing)
    y = np.array([int(row["label"]) for row in rows], dtype=int)
    returns = np.array([float(row["return_pct"]) for row in rows], dtype=float)
    entry_dates = np.array([row["entry_date"] for row in rows], dtype=str)
    holding_days = np.array([int(row["holding_days"]) for row in rows], dtype=int)

    names = feature_names()
    return {
        "X": x,
        "X_raw": raw_matrix,
        "y": y,
        "returns": returns,
        "entry_dates": entry_dates,
        "holding_days": holding_days,
        "feature_names": names,
        "preprocessing": preprocessing,
    }


def featurize_dict(raw: dict, preprocessing: dict) -> np.ndarray:
    """Build a standardized feature row from a raw feature dict (live-scorer contract)."""
    medians = preprocessing["medians"]
    row = np.array([_encode_row(raw, medians)], dtype=float)
    numeric_count = len(NUMERIC_FEATURES)
    means = np.array([preprocessing["means"][n] for n in NUMERIC_FEATURES])
    stds = np.array([preprocessing["stds"][n] for n in NUMERIC_FEATURES])
    return _standardize_matrix(row, numeric_count, means, stds)[0]


def compute_raw_score(feature_dict: dict, model_artifact: dict) -> float:
    """Raw win-probability from stored logistic coefficients (pre-Platt)."""
    x = featurize_dict(feature_dict, model_artifact["preprocessing"])
    coef = np.array(
        [model_artifact["coefficients"][name] for name in model_artifact["feature_names"]],
        dtype=float,
    )
    logit = float(np.dot(x, coef) + model_artifact["intercept"])
    return float(1.0 / (1.0 + np.exp(-logit)))
