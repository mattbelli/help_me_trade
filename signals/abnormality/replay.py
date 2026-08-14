"""Replay harness: fire-rate tables at candidate thresholds over the 5-min store.

Reproducible: same store + same baselines + same thresholds → same counts.
Founder eyeballs the table + example-fire galleries before locking (Inc 2).
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np

from signals.abnormality.baselines import Baselines, load_baselines
from signals.abnormality.constants import KIND_PAIRED, N_BUCKETS, WINDOW_BARS
from signals.abnormality.engine import FireThresholds, PriorFire, evaluate_boundary
from signals.abnormality.store import list_sessions, load_5min, window_slice

CANDIDATES: tuple[FireThresholds, ...] = (
    FireThresholds(),  # draft: p99/p95, floors 2.0/3.0, top-N 10
    FireThresholds(range_p=99, vol_p=90, range_floor=2.0, vol_floor=2.0, top_n=10),
    FireThresholds(range_p=99, vol_p=95, range_floor=1.5, vol_floor=2.0, top_n=10),
    FireThresholds(range_p=99.5, vol_p=95, range_floor=2.0, vol_floor=3.0, top_n=5),
    FireThresholds(range_p=99, vol_p=95, range_floor=2.0, vol_floor=3.0, top_n=20),
)


def replay_session(
    grid_path: Path,
    baselines: Baselines,
    thresholds: FireThresholds,
) -> dict[str, Any]:
    """Run one session against frozen baselines. Returns counts + example fires."""
    g = load_5min(grid_path)
    idx = np.array([baselines.row(str(s)) for s in g.symbols], dtype=object)
    keep = np.array([i is not None for i in idx])
    if not keep.any():
        return {"session": g.session.isoformat(), "fires": 0, "paired": 0, "examples": []}
    rows = np.array([int(i) for i in idx[keep]], dtype=np.int32)
    high, low, vol = g.high[keep], g.low[keep], g.volume[keep]
    names = [str(s) for s, k in zip(g.symbols, keep) if k]
    prior: dict[int, PriorFire] = {}
    fires: list[dict[str, Any]] = []
    paired = 0
    for b in range(WINDOW_BARS, N_BUCKETS):
        if window_slice(b) is None:
            continue
        result = evaluate_boundary(
            high=high,
            low=low,
            volume=vol,
            bucket=b,
            median_rv=baselines.median_rv[rows, b],
            median_window_vol=baselines.median_window_vol[rows, b],
            median_cum_vol=baselines.median_cum_vol[rows, b],
            quintile=baselines.quintile[rows],
            base_state=baselines.base_state[rows],
            prior=prior,
            thresholds=thresholds,
        )
        for j, i in enumerate(result.fire_idx):
            i = int(i)
            kind = result.fire_kind[j]
            prior[i] = PriorFire(bucket=b, composite=float(result.composite[i]))
            if kind == KIND_PAIRED:
                paired += 1
            fires.append(
                {
                    "symbol": names[i],
                    "bucket": b,
                    "kind": kind,
                    "range_ratio": float(result.range_ratio[i]),
                    "vol_ratio": float(result.vol_ratio[i]),
                    "composite": float(result.composite[i]),
                    "range_pctile": float(result.range_pctile[i]),
                    "vol_pctile": float(result.vol_pctile[i]),
                    "base_state": int(baselines.base_state[rows[i]]),
                }
            )
    return {
        "session": g.session.isoformat(),
        "fires": len(fires),
        "paired": paired,
        "examples": fires[:25],
    }


def replay_store(
    bars_5min_dir: Path,
    baselines: Baselines,
    thresholds: FireThresholds,
    *,
    start: date | None = None,
    end: date | None = None,
) -> dict[str, Any]:
    sessions = [
        s for s in list_sessions(bars_5min_dir)
        if (start is None or s >= start) and (end is None or s <= end)
    ]
    per = []
    total = paired = 0
    for s in sessions:
        row = replay_session(bars_5min_dir / f"{s.isoformat()}.npz", baselines, thresholds)
        per.append({"session": row["session"], "fires": row["fires"], "paired": row["paired"]})
        total += int(row["fires"])
        paired += int(row["paired"])
        examples = row["examples"]
    n = len(sessions) or 1
    return {
        "thresholds": asdict(thresholds),
        "sessions": len(sessions),
        "fires": total,
        "paired": paired,
        "fires_per_day": round(total / n, 2),
        "paired_per_week": round(paired / max(n / 5, 1e-9), 2),
        "per_session": per,
        "examples": examples if sessions else [],
    }


def replay_candidates(
    bars_5min_dir: Path,
    *,
    baselines_dir: Path | None = None,
    start: date | None = None,
    end: date | None = None,
) -> list[dict[str, Any]]:
    bl = load_baselines(baselines_dir)
    if bl is None:
        raise SystemExit("no baselines artifact — run tools/abnormality_baselines.py first")
    return [
        replay_store(bars_5min_dir, bl, thr, start=start, end=end) for thr in CANDIDATES
    ]
