"""Parkinson RV, coverage, ratios, cell percentiles — shared by live and replay.

The RV formula is the 1.14g `rv_park` (per-bin averaged, zero-range present-and-
zero, ≥80% bins present else absent). Ratios are own-normalized so time scaling
cancels. Peer percentiles are within the 1.14d cell (bucket × $-vol quintile).
"""

from __future__ import annotations

import warnings

import numpy as np

from signals.abnormality.constants import LN2_4, N_QUINTILES, RV_COVERAGE


def parkinson_rv(high: np.ndarray, low: np.ndarray, *, coverage: float = RV_COVERAGE) -> float:
    """Scalar Parkinson RV over a window. NaN when coverage fails.

    sqrt( sum_k ln(h_k/l_k)^2 / (4 ln2 * n_present) ).
    A bin with h == l is PRESENT and contributes 0.
    """
    rows = parkinson_rv_rows(
        np.asarray(high, np.float64)[None, :],
        np.asarray(low, np.float64)[None, :],
        coverage=coverage,
    )
    return float(rows[0])


def parkinson_rv_rows(
    high: np.ndarray, low: np.ndarray, *, coverage: float = RV_COVERAGE
) -> np.ndarray:
    """Row-wise Parkinson RV. `high`/`low` are [n_names, n_bins]. NaN where NA."""
    high = np.asarray(high, np.float64)
    low = np.asarray(low, np.float64)
    if high.size == 0:
        return np.full(high.shape[:1], np.nan)
    with np.errstate(invalid="ignore"):
        ok = np.isfinite(high) & np.isfinite(low) & (low > 0) & (high >= low)
    n_present = ok.sum(axis=1)
    with np.errstate(invalid="ignore", divide="ignore"):
        r = np.log(np.where(ok, high, 1.0) / np.where(ok, low, 1.0))
    ssq = np.where(ok, r * r, 0.0).sum(axis=1)
    n_bins = high.shape[1]
    with np.errstate(invalid="ignore", divide="ignore"):
        val = np.sqrt(ssq / (LN2_4 * n_present))
    return np.where(n_present >= coverage * n_bins, val, np.nan)


def window_sum(values: np.ndarray, *, coverage: float = RV_COVERAGE) -> np.ndarray:
    """Sum of finite bins; NaN when coverage fails. `values` is [n, bins] or [bins]."""
    x = np.asarray(values, np.float64)
    if x.ndim == 1:
        return window_sum(x[None, :], coverage=coverage)[0]
    ok = np.isfinite(x) & (x >= 0)
    n_present = ok.sum(axis=1)
    total = np.where(ok, x, 0.0).sum(axis=1)
    return np.where(n_present >= coverage * x.shape[1], total, np.nan)


def ratio(num: np.ndarray, den: np.ndarray) -> np.ndarray:
    """num/den with NaN when either side is non-finite or den <= 0. Never fabricated."""
    n = np.asarray(num, np.float64)
    d = np.asarray(den, np.float64)
    with np.errstate(invalid="ignore", divide="ignore"):
        out = n / d
    return np.where(np.isfinite(n) & np.isfinite(d) & (d > 0), out, np.nan)


def composite(range_ratio: np.ndarray, vol_ratio: np.ndarray) -> np.ndarray:
    """Ranking score for top-N: product of the two ratios. NaN if either is."""
    r = np.asarray(range_ratio, np.float64)
    v = np.asarray(vol_ratio, np.float64)
    out = r * v
    return np.where(np.isfinite(r) & np.isfinite(v), out, np.nan)


def assign_quintiles(dvol: np.ndarray) -> np.ndarray:
    """Equal-count 0–4 among finite positive $-vol; -1 if absent."""
    x = np.asarray(dvol, np.float64)
    out = np.full(x.shape, -1, dtype=np.int8)
    ok = np.isfinite(x) & (x > 0)
    if int(ok.sum()) < N_QUINTILES:
        return out
    v = x[ok]
    cuts = np.percentile(v, [20, 40, 60, 80])
    out[ok] = np.digitize(v, cuts, right=True).astype(np.int8)
    return out


def nanmedian(a: np.ndarray, axis: int) -> np.ndarray:
    """np.nanmedian without the all-NaN RuntimeWarning (warmup cells are empty)."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        return np.nanmedian(a, axis=axis)


def cell_percentile(values: np.ndarray, q: float) -> float:
    """Percentile `q` (0–100) of finite values. NaN if the cell is empty."""
    v = np.asarray(values, np.float64)
    v = v[np.isfinite(v)]
    if v.size == 0:
        return float("nan")
    return float(np.percentile(v, q))


def pctile_of(value: float, cell: np.ndarray) -> float:
    """Percent of finite cell values strictly below `value`, 0–100. NaN if absent."""
    if not np.isfinite(value):
        return float("nan")
    v = np.asarray(cell, np.float64)
    v = v[np.isfinite(v)]
    if v.size == 0:
        return float("nan")
    return float((v < value).mean() * 100.0)
