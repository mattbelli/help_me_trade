"""Tests for purged CV, Platt calibration, EV table, and artifact export."""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pytest

from db.base import connect
from research.calibration import (
    calibrate,
    calibration_metrics,
    fit_platt,
    fit_score_model,
    out_of_fold_scores,
)
from research.dataset_io import compute_raw_score, load_labeled_dataset
from research.ev import build_ev_table, wilson_ci
from research.train import train_and_export
from research.validation import purged_kfold_indices


def _make_dated_samples(
    n: int,
    *,
    start: date = date(2024, 1, 1),
    holding: int = 5,
    gap_days: int = 1,
) -> tuple[np.ndarray, np.ndarray]:
    dates = []
    d = start
    for _ in range(n):
        dates.append(d.isoformat())
        d += timedelta(days=gap_days)
    return np.array(dates, dtype=str), np.full(n, holding, dtype=int)


def _label_window_overlaps_test(
    idx: int,
    entry_dates: np.ndarray,
    holding_days: np.ndarray,
    test_min: date,
    test_max: date,
) -> bool:
    sample_start = date.fromisoformat(entry_dates[idx])
    sample_end = sample_start + timedelta(days=int(holding_days[idx]))
    return sample_start <= test_max and sample_end >= test_min


# --- PurgedKFold ---


def test_purged_kfold_excludes_overlapping_train_samples() -> None:
    entry_dates, holding_days = _make_dated_samples(20, holding=10, gap_days=3)
    folds = purged_kfold_indices(entry_dates, holding_days, n_splits=4, embargo_frac=0.05)

    assert len(folds) == 4
    all_test: set[int] = set()
    for train_idx, test_idx in folds:
        assert len(set(train_idx) & set(test_idx)) == 0
        all_test.update(test_idx.tolist())

        test_starts = [date.fromisoformat(entry_dates[i]) for i in test_idx]
        test_ends = [
            date.fromisoformat(entry_dates[i]) + timedelta(days=int(holding_days[i]))
            for i in test_idx
        ]
        test_min, test_max = min(test_starts), max(test_ends)

        for idx in train_idx:
            assert not _label_window_overlaps_test(
                idx, entry_dates, holding_days, test_min, test_max
            )

        embargo_size = max(1, int(round(0.05 * len(entry_dates))))
        embargo_start = int(test_idx[-1]) + 1
        embargo_end = min(len(entry_dates), embargo_start + embargo_size)
        for idx in train_idx:
            assert idx not in set(range(embargo_start, embargo_end))

    assert all_test == set(range(len(entry_dates)))


def test_purged_kfold_no_train_test_overlap_per_fold() -> None:
    entry_dates, holding_days = _make_dated_samples(15, holding=3, gap_days=2)
    folds = purged_kfold_indices(entry_dates, holding_days, n_splits=3)
    for train_idx, test_idx in folds:
        overlap = set(train_idx.tolist()) & set(test_idx.tolist())
        assert overlap == set()


# --- Calibration ---


def _separable_xy(n: int = 120, seed: int = 7) -> tuple[np.ndarray, np.ndarray]:
    rng = np.random.default_rng(seed)
    x0 = rng.normal(-1.5, 1.0, n // 2)
    x1 = rng.normal(1.5, 1.0, n - n // 2)
    x = np.concatenate([x0, x1]).reshape(-1, 1)
    y = np.array([0] * (n // 2) + [1] * (n - n // 2), dtype=int)
    perm = rng.permutation(n)
    return x[perm], y[perm]


def test_out_of_fold_scores_in_unit_interval() -> None:
    x, y = _separable_xy()
    entry_dates, holding_days = _make_dated_samples(len(y), gap_days=1)
    folds = purged_kfold_indices(entry_dates, holding_days, n_splits=5)
    oof = out_of_fold_scores(x, y, folds)
    assert oof.shape == y.shape
    assert np.all(oof >= 0.0)
    assert np.all(oof <= 1.0)


def test_platt_calibration_in_unit_interval() -> None:
    x, y = _separable_xy()
    entry_dates, holding_days = _make_dated_samples(len(y), gap_days=1)
    folds = purged_kfold_indices(entry_dates, holding_days, n_splits=5)
    oof = out_of_fold_scores(x, y, folds)
    platt = fit_platt(oof, y)
    probs = calibrate(oof, platt)
    assert np.all(probs >= 0.0)
    assert np.all(probs <= 1.0)


def test_brier_beats_base_rate_baseline_on_separable_data() -> None:
    x, y = _separable_xy(n=160)
    entry_dates, holding_days = _make_dated_samples(len(y), gap_days=1)
    folds = purged_kfold_indices(entry_dates, holding_days, n_splits=5)
    oof = out_of_fold_scores(x, y, folds)
    platt = fit_platt(oof, y)
    probs = calibrate(oof, platt)
    metrics = calibration_metrics(probs, y)
    base_rate = float(np.mean(y))
    baseline_brier = base_rate * (1.0 - base_rate) ** 2 + (1.0 - base_rate) * base_rate**2
    assert np.isfinite(metrics["brier"])
    assert metrics["brier"] < baseline_brier


# --- EV table ---


def test_wilson_ci_contains_empirical_rate() -> None:
    lo, hi = wilson_ci(7, 10)
    assert 0.0 <= lo <= 0.7 <= hi <= 1.0


def test_build_ev_table_bucket_counts_and_ev() -> None:
    probs = np.array([0.05, 0.15, 0.25, 0.55, 0.58, 0.85, 0.95])
    y = np.array([0, 0, 1, 0, 1, 1, 1])
    returns = np.array([-2.0, -3.0, 5.0, -1.0, 4.0, 6.0, 8.0])

    table = build_ev_table(probs, y, returns, min_bucket=2)
    populated = [row for row in table if row["n"] > 0]
    assert sum(row["n"] for row in populated) == len(probs)

    mid = next(row for row in table if row["bin_lo"] == 0.5)
    assert mid["n"] == 2
    assert mid["empirical_winrate"] == 0.5
    assert mid["wilson_ci_lo"] <= mid["empirical_winrate"] <= mid["wilson_ci_hi"]
    assert mid["avg_win_return"] == 4.0
    assert mid["avg_loss_return"] == -1.0
    expected_ev = 0.5 * 4.0 + 0.5 * (-1.0)
    assert mid["ev"] == pytest.approx(expected_ev)
    assert mid["sufficient"] is True
    assert mid["flag"] is None

    sparse = next(row for row in table if row["bin_lo"] == 0.0)
    assert sparse["n"] == 1
    assert sparse["sufficient"] is False
    assert sparse["flag"] == "insufficient_data"


def test_ev_table_sufficient_flag_flips_at_min_bucket() -> None:
    probs = np.array([0.15, 0.15, 0.15])
    y = np.array([0, 1, 0])
    returns = np.array([-1.0, 2.0, -1.0])

    small = build_ev_table(probs, y, returns, min_bucket=3)
    bucket = next(row for row in small if row["bin_lo"] == 0.1)
    assert bucket["n"] == 3
    assert bucket["sufficient"] is True

    large = build_ev_table(probs, y, returns, min_bucket=4)
    bucket = next(row for row in large if row["bin_lo"] == 0.1)
    assert bucket["sufficient"] is False
    assert bucket["flag"] == "insufficient_data"


# --- Artifacts round-trip ---


def _sample_features(label_hint: int, idx: int) -> dict:
    return {
        "rsi_14": 45.0 + idx,
        "atr_14": 2.0 + idx * 0.01,
        "dist_from_sma50_pct": -1.0 + idx * 0.1,
        "dist_from_sma200_pct": 2.0,
        "dist_from_52w_high_pct": -5.0,
        "prior_return_1d": 0.5,
        "prior_return_3d": 1.0,
        "prior_return_5d": 1.5,
        "pct_change_1d": 0.3,
        "pct_change_5d": 1.2,
        "pct_change_21d": 3.0,
        "vol_vs_avg_20": 1.1,
        "gap_prior": 0.0,
        "dist_from_high_252": -4.0,
        "rel_strength_21": 0.5 + idx * 0.02,
        "entry_context": "pullback" if idx % 2 == 0 else "momentum",
        "vs_sma50": "above" if label_hint else "below",
        "vs_sma200": "above",
    }


def _write_synthetic_labeled_db(db_path: Path, n: int = 80) -> None:
    conn = connect(db_path)
    try:
        conn.execute(
            """
            CREATE TABLE labeled_setups (
                id INTEGER PRIMARY KEY,
                symbol TEXT NOT NULL,
                entry_date TEXT NOT NULL,
                features_json TEXT NOT NULL,
                label INTEGER NOT NULL,
                outcome TEXT NOT NULL,
                return_pct REAL NOT NULL,
                holding_days INTEGER NOT NULL,
                max_holding INTEGER NOT NULL,
                upper_atr_mult REAL NOT NULL,
                lower_atr_mult REAL NOT NULL,
                source TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        start = date(2023, 1, 3)
        for i in range(n):
            label = i % 2
            features = _sample_features(label, i)
            entry = start + timedelta(days=i * 4)
            conn.execute(
                """
                INSERT INTO labeled_setups (
                    symbol, entry_date, features_json, label, outcome,
                    return_pct, holding_days, max_holding,
                    upper_atr_mult, lower_atr_mult, source, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "TEST",
                    entry.isoformat(),
                    json.dumps(features),
                    label,
                    "win" if label else "loss",
                    4.0 if label else -2.0,
                    5,
                    10,
                    2.0,
                    2.0,
                    "test",
                    "2024-01-01T00:00:00+00:00",
                ),
            )
        conn.commit()
    finally:
        conn.close()


def test_train_and_export_writes_artifacts_and_raw_score_roundtrip(tmp_path: Path) -> None:
    labeled_db = tmp_path / "labeled.db"
    artifact_dir = tmp_path / "artifacts"
    _write_synthetic_labeled_db(labeled_db, n=80)

    summary = train_and_export(
        labeled_db=labeled_db,
        artifact_dir=artifact_dir,
        created_at="2024-06-01T12:00:00+00:00",
        min_bucket=5,
    )
    assert summary["n_samples"] == 80

    for name in ("model.json", "calibrator.json", "ev_table.json", "meta.json"):
        assert (artifact_dir / name).is_file()

    model = json.loads((artifact_dir / "model.json").read_text())
    meta = json.loads((artifact_dir / "meta.json").read_text())
    assert meta["created_at"] == "2024-06-01T12:00:00+00:00"
    assert "honesty" in meta
    assert meta["honesty"]["low_confidence"] is True

    sample = _sample_features(0, 0)
    roundtrip = compute_raw_score(sample, model)

    data = load_labeled_dataset(labeled_db)
    x_row = data["X"][0]
    coef = np.array([model["coefficients"][n] for n in model["feature_names"]])
    logit = float(np.dot(x_row, coef) + model["intercept"])
    expected = float(1.0 / (1.0 + np.exp(-logit)))
    assert roundtrip == pytest.approx(expected)

    full_model = fit_score_model(data["X"], data["y"])
    sklearn_prob = float(full_model.predict_proba(x_row.reshape(1, -1))[0, 1])
    assert roundtrip == pytest.approx(sklearn_prob)
