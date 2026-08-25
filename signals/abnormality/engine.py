"""Shared boundary evaluation — live detector and replay call the same function.

Parity test (design §8 Inc 4): fires from this function over a session's 5-min
store must match the live detector primed from that store.

Attention Phase 1 (2026-08-25): firing is the registered 1.14f/g rule — range
ratio >= p90 within the (boundary x $-vol quintile) peer cell, single
component, cell occupancy >= 25, universe = the options-eligible pool (the
``pool`` mask; quintiles are assigned within it). vol/rvol stay computed as
displayed facts, never gates. First fire per name-day is the study object;
later fires still emit, flagged ``repeat``.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from signals.abnormality.constants import (
    CELL_MIN_OCCUPANCY,
    KIND_ABNORMALITY,
    KIND_PAIRED,
    N_BUCKETS,
    RANGE_PCTL,
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
    min_occupancy: int = CELL_MIN_OCCUPANCY


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
    fire_repeat: list[bool] = field(default_factory=list)


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
    pool: np.ndarray | None = None,
    base_state: np.ndarray | None = None,
    prior: dict[int, PriorFire] | None = None,
    thresholds: FireThresholds | None = None,
    n_buckets: int = N_BUCKETS,
    fires_allowed: bool = True,
) -> BoundaryResult:
    """Ratios + fires at one 5-minute boundary.

    Arrays are aligned on the universe (one row per symbol). ``pool`` marks the
    options-eligible names — peer cells, percentiles and fires exist only
    inside it (None = no pool, nothing fires; state is still computed).
    ``prior`` maps row index -> last fire this session; a row with any prior
    fire still fires but flagged ``repeat``. ``fires_allowed=False`` computes
    state only (a 5-min boundary off the half-hour study grid).
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
    p_range = np.full(n, np.nan)
    occupied = np.zeros(n, dtype=bool)
    in_pool = (
        np.asarray(pool, dtype=bool)
        if pool is not None
        else np.zeros(n, dtype=bool)
    )
    q = np.asarray(quintile, np.int8)
    for qi in range(5):
        mask = (q == qi) & in_pool
        if not mask.any():
            continue
        cell_r = range_ratio[mask]
        cell_v = vol_ratio[mask]
        n_occ = int(np.isfinite(cell_r).sum())
        if n_occ >= thr.min_occupancy:
            occupied[mask] = True
            p_range[mask] = cell_percentile(cell_r, thr.range_p)
        for i in np.flatnonzero(mask):
            range_pctile[i] = pctile_of(float(range_ratio[i]), cell_r)
            vol_pctile[i] = pctile_of(float(vol_ratio[i]), cell_v)

    if not fires_allowed:
        return BoundaryResult(
            range_ratio, vol_ratio, rvol_session, range_pctile, vol_pctile, comp,
            np.array([], np.int32),
        )

    eligible = (
        in_pool
        & occupied
        & np.isfinite(range_ratio)
        & np.isfinite(p_range)
        & (range_ratio >= p_range)
    )
    picked = np.flatnonzero(eligible).astype(np.int32)
    if picked.size == 0:
        return BoundaryResult(
            range_ratio, vol_ratio, rvol_session, range_pctile, vol_pctile, comp, picked
        )

    kinds: list[str] = []
    repeats: list[bool] = []
    for i in picked:
        paired = False
        if base_state is not None:
            st = int(base_state[int(i)])
            paired = st != 0
        kinds.append(KIND_PAIRED if paired else KIND_ABNORMALITY)
        repeats.append(prior is not None and int(i) in prior)
    return BoundaryResult(
        range_ratio, vol_ratio, rvol_session, range_pctile, vol_pctile, comp,
        picked, kinds, repeats,
    )
