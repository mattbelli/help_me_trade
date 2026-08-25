"""Grade fired events from the 5-min store — the correct estimand for a direction-less signal.

One row per FIRST fire (repeats are the pseudo-replicates 1.14f excluded):
forward max |cumulative 5-min log return| on CLOSES over 2h and to close — the
registered study estimand, comparable to the 1.3x/0.94x published numbers —
divided by the baseline forward norm (excess ratio), plus realized direction.
Nightly, never live-polled. Any displayed rate is Wilson-bounded.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import numpy as np

from research.ev import wilson_ci
from signals.abnormality.baselines import Baselines
from signals.abnormality.constants import FORWARD_2H_BARS, N_BUCKETS
from signals.abnormality.store import load_5min


def grade_event(
    *,
    close: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    bucket: int,
    fwd_norm: float,
) -> dict[str, Any]:
    """Grade one fire from a single-name 5-min row. Arrays are length N_BUCKETS."""
    if bucket < 1 or bucket >= N_BUCKETS:
        return {"ok": False, "reason": "bad_bucket"}
    entry = float(close[bucket - 1])
    if not np.isfinite(entry) or entry <= 0:
        return {"ok": False, "reason": "no_entry"}
    end_2h = min(bucket + FORWARD_2H_BARS, N_BUCKETS)
    # Registered estimand: max |cumulative 5-min log return| on CLOSES from the
    # boundary — never the high/low excursion (that measured a different, wider
    # quantity than the norms it is divided by).
    c2 = close[bucket:end_2h]
    if c2.size == 0 or not np.isfinite(c2).any():
        return {"ok": False, "reason": "no_horizon"}
    with np.errstate(invalid="ignore", divide="ignore"):
        move_2h = float(np.nanmax(np.abs(np.log(c2 / entry))))
    close_2h = float(close[end_2h - 1]) if np.isfinite(close[end_2h - 1]) else float("nan")
    c_to_c = close[bucket:]
    with np.errstate(invalid="ignore", divide="ignore"):
        move_close = (
            float(np.nanmax(np.abs(np.log(c_to_c / entry))))
            if np.isfinite(c_to_c).any()
            else float("nan")
        )
    last = _last(close)
    direction = 0
    if np.isfinite(close_2h):
        direction = 1 if close_2h > entry else (-1 if close_2h < entry else 0)
    excess = (move_2h / fwd_norm) if np.isfinite(fwd_norm) and fwd_norm > 0 else float("nan")
    return {
        "ok": True,
        "entry": entry,
        "move_2h": move_2h,
        "move_close": move_close,
        "excess_2h": excess if np.isfinite(excess) else None,
        "close_2h": close_2h if np.isfinite(close_2h) else None,
        "close_session": last,
        "direction": direction,
        "horizon_complete": end_2h - bucket == FORWARD_2H_BARS,
    }


def sweep_session(
    session: date,
    events: list[dict[str, Any]],
    bars_5min_dir: Path,
    baselines: Baselines,
) -> list[dict[str, Any]]:
    path = bars_5min_dir / f"{session.isoformat()}.npz"
    if not path.exists():
        return []
    g = load_5min(path)
    by_sym = {str(s): i for i, s in enumerate(g.symbols)}
    out: list[dict[str, Any]] = []
    for ev in events:
        sym = str(ev["symbol"])
        j = by_sym.get(sym)
        if j is None:
            continue
        row = baselines.row(sym)
        bucket = int(ev["bucket"])
        fwd_norm = (
            float(baselines.median_fwd_2h[row, bucket])
            if row is not None
            else float("nan")
        )
        graded = grade_event(
            close=g.close[j], high=g.high[j], low=g.low[j],
            bucket=bucket, fwd_norm=fwd_norm,
        )
        if not graded.get("ok"):
            continue
        out.append({**ev, **graded, "session": session.isoformat()})
    return out


def aggregate(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Footer stats. Direction rate is Wilson-bounded; never a naked percent."""
    n = len(rows)
    if n == 0:
        return {"n": 0, "median_excess": None, "direction": None}
    excess = [r["excess_2h"] for r in rows if r.get("excess_2h") is not None]
    up = sum(1 for r in rows if r.get("direction") == 1)
    down = sum(1 for r in rows if r.get("direction") == -1)
    signed = up + down
    lo, hi = wilson_ci(up, signed) if signed else (0.0, 1.0)
    return {
        "n": n,
        "median_excess": round(float(np.median(excess)), 3) if excess else None,
        "direction": {
            "up": up,
            "down": down,
            "flat": n - signed,
            "up_rate": round(up / signed, 3) if signed else None,
            "up_wilson_95": [round(lo, 3), round(hi, 3)] if signed else None,
        },
    }


def _last(close: np.ndarray) -> float | None:
    ok = np.isfinite(close)
    if not ok.any():
        return None
    return float(close[np.flatnonzero(ok)[-1]])
