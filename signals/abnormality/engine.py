"""Shared boundary evaluation — live detector and replay call the same function.

Parity test (design §8 Inc 4): fires from this function over a session's 5-min
store must match the live detector primed from that store.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from signals.abnormality.constants import (
    KIND_ABNORMALITY,
    KIND_PAIRED,
    N_BUCKETS,
    RANGE_FLOOR,
    RANGE_PCTL,
    REFIRE_COMPOSITE_LIFT,
    REFIRE_MINUTES,
    TOP_N_PER_BOUNDARY,
    VOL_FLOOR,
    VOL_PCTL,
    WINDOW_BARS,
)
from signals.abnormality.math import (
    cell_percentile,
    composite,
    parkinson_rv_rows,
    pctile_of,
    ratio,
    window_sum,
)
from signals.abnormality.store import window_slice


@dataclass
class FireThresholds:
    range_p: float = RANGE_PCTL
    vol_p: float = VOL_PCTL
    range_floor: float = RANGE_FLOOR
    vol_floor: float = VOL_FLOOR
    top_n: int = TOP_N_PER_BOUNDARY
    refire_minutes: int = REFIRE_MINUTES
    refire_lift: float = REFIRE_COMPOSITE_LIFT


@dataclass
class PriorFire:
    bucket: int
    composite: float


@dataclass
class BoundaryResult:
    range_ratio: np.ndarray
    vol_ratio: np.ndarray
    rvol_session: np.ndarray
    range_pctile: np.ndarray
    vol_pctile: np.ndarray
    composite: np.ndarray
    fire_idx: np.ndarray
    fire_kind: list[str] = field(default_factory=list)


def evaluate_boundary(
    *,
    high: np.ndarray,
    low: np.ndarray,
    volume: np.ndarray,
    bucket: int,
    median_rv: np.ndarray,
    median_window_vol: np.ndarray,
    median_cum_vol: np.ndarray,
    quintile: np.ndarray,
    base_state: np.ndarray | None = None,
    prior: dict[int, PriorFire] | None = None,
    thresholds: FireThresholds | None = None,
    n_buckets: int = N_BUCKETS,
) -> BoundaryResult:
    """Ratios + fires at one 5-minute boundary.

    Arrays are aligned on the universe (one row per symbol). `prior` maps
    row index → last fire this session. `base_state` is 0/1/2 (none/low/high)
    or None to skip pairing.
    """
    thr = thresholds or FireThresholds()
    n = high.shape[0]
    empty = np.full(n, np.nan)
    win = window_slice(bucket, WINDOW_BARS)
    if win is None or bucket >= n_buckets:
        return BoundaryResult(empty, empty, empty, empty, empty, empty, np.array([], np.int32))

    rv = parkinson_rv_rows(high[:, win], low[:, win])
    wvol = window_sum(volume[:, win])
    cum = window_sum(volume[:, :bucket])  # session cumulative through prior bars
    range_ratio = ratio(rv, median_rv)
    vol_ratio = ratio(wvol, median_window_vol)
    rvol_session = ratio(cum, median_cum_vol)
    comp = composite(range_ratio, vol_ratio)

    range_pctile = np.full(n, np.nan)
    vol_pctile = np.full(n, np.nan)
    p99 = np.full(n, np.nan)
    p95 = np.full(n, np.nan)
    q = np.asarray(quintile, np.int8)
    for qi in range(5):
        mask = q == qi
        if not mask.any():
            continue
        cell_r = range_ratio[mask]
        cell_v = vol_ratio[mask]
        p99[mask] = cell_percentile(cell_r, thr.range_p)
        p95[mask] = cell_percentile(cell_v, thr.vol_p)
        for i in np.flatnonzero(mask):
            range_pctile[i] = pctile_of(float(range_ratio[i]), cell_r)
            vol_pctile[i] = pctile_of(float(vol_ratio[i]), cell_v)

    eligible = (
        np.isfinite(range_ratio)
        & np.isfinite(vol_ratio)
        & np.isfinite(p99)
        & np.isfinite(p95)
        & (range_ratio >= p99)
        & (vol_ratio >= p95)
        & (range_ratio >= thr.range_floor)
        & (vol_ratio >= thr.vol_floor)
    )
    cand = np.flatnonzero(eligible)
    if prior:
        keep = []
        for i in cand:
            prev = prior.get(int(i))
            if prev is None:
                keep.append(i)
                continue
            dt = (bucket - prev.bucket) * 5
            lifted = np.isfinite(comp[i]) and float(comp[i]) >= prev.composite * thr.refire_lift
            if dt >= thr.refire_minutes and lifted:
                keep.append(i)
        cand = np.asarray(keep, np.int32)
    if cand.size == 0:
        return BoundaryResult(
            range_ratio, vol_ratio, rvol_session, range_pctile, vol_pctile, comp, cand
        )

    order = np.argsort(-np.where(np.isfinite(comp[cand]), comp[cand], -np.inf))
    picked = cand[order[: thr.top_n]]
    kinds: list[str] = []
    for i in picked:
        paired = False
        if base_state is not None:
            st = int(base_state[int(i)])
            paired = st != 0
        kinds.append(KIND_PAIRED if paired else KIND_ABNORMALITY)
    return BoundaryResult(
        range_ratio, vol_ratio, rvol_session, range_pctile, vol_pctile, comp, picked, kinds
    )
