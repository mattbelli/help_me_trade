"""Time-aware cross-validation indices with purging and embargo."""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np


def _parse_iso(value: str) -> date:
    return date.fromisoformat(value)


def _label_window_end(entry_date: str, holding_days: int) -> date:
    return _parse_iso(entry_date) + timedelta(days=holding_days)


def _windows_overlap(
    start_a: date,
    end_a: date,
    start_b: date,
    end_b: date,
) -> bool:
    return start_a <= end_b and end_a >= start_b


def purged_kfold_indices(
    entry_dates: np.ndarray,
    holding_days: np.ndarray,
    *,
    n_splits: int = 5,
    embargo_frac: float = 0.01,
) -> list[tuple[np.ndarray, np.ndarray]]:
    """Return (train_idx, test_idx) pairs for purged, embargoed K-fold CV.

    Samples must already be sorted by entry_date ascending.
    """
    n = len(entry_dates)
    if n_splits < 2:
        msg = "n_splits must be at least 2"
        raise ValueError(msg)
    if n < n_splits:
        msg = f"Need at least {n_splits} samples for {n_splits} folds, got {n}"
        raise ValueError(msg)

    embargo_size = max(1, int(round(embargo_frac * n)))
    fold_sizes = [n // n_splits] * n_splits
    for i in range(n % n_splits):
        fold_sizes[i] += 1

    folds: list[tuple[np.ndarray, np.ndarray]] = []
    start = 0
    for size in fold_sizes:
        test_idx = np.arange(start, start + size, dtype=int)
        start += size

        test_starts = [_parse_iso(entry_dates[i]) for i in test_idx]
        test_ends = [_label_window_end(entry_dates[i], int(holding_days[i])) for i in test_idx]
        test_min = min(test_starts)
        test_max = max(test_ends)

        train_candidates = np.setdiff1d(np.arange(n, dtype=int), test_idx, assume_unique=True)
        purged_train: list[int] = []
        for idx in train_candidates:
            sample_start = _parse_iso(entry_dates[idx])
            sample_end = _label_window_end(entry_dates[idx], int(holding_days[idx]))
            if _windows_overlap(sample_start, sample_end, test_min, test_max):
                continue
            purged_train.append(int(idx))

        if len(test_idx) == 0:
            continue
        embargo_start_pos = int(test_idx[-1]) + 1
        embargo_end_pos = min(n, embargo_start_pos + embargo_size)
        embargo_indices = set(range(embargo_start_pos, embargo_end_pos))

        final_train = np.array(
            [idx for idx in purged_train if idx not in embargo_indices],
            dtype=int,
        )
        folds.append((final_train, test_idx))

    return folds
