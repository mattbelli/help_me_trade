"""Grade EVERY pool name-day with the study estimand (design §4.2 nightly).

The attention aggregate — "does 2 angles > 1 angle > 0?" — needs outcomes on
the whole pool, lit or not, so the comparison is a plain query. Each name-day
gets ONE row anchored at:
  - its first abnormality fire bucket (``anchor='abn_first'``), else
  - the bucket of its first rare flow print (``anchor='flow_first'``), else
  - the session's median lit anchor bucket (``anchor='session_median'``;
    fallback bucket 24 = 11:30 ET mid-grid on a no-lit session).
The excess is the registered 1.14f/g estimand — forward-2h max |cum 5-min log
return| on closes ÷ the name's OWN boundary-matched norm from the artifact
built ≤ t−1 — so the anchor's time-of-day effect is divided out by
construction; the anchor is still recorded on every row for matched-boundary
cuts later. ``move_close`` is stored raw (no close-horizon norm exists in the
artifact — an ``excess_close`` would be a fabricated unit).
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np

from signals.abnormality.baselines import Baselines
from signals.abnormality.constants import N_BUCKETS
from signals.abnormality.outcomes import grade_event
from signals.abnormality.store import load_5min

logger = logging.getLogger(__name__)

_ET = ZoneInfo("America/New_York")
FALLBACK_BUCKET = 24  # 11:30 ET — mid-grid


def _ts_bucket(ts_utc_iso: str) -> int | None:
    """UTC ISO stamp → 5-min session bucket, clamped into gradable range."""
    try:
        dt = datetime.fromisoformat(ts_utc_iso).astimezone(_ET)
    except (ValueError, TypeError):
        return None
    minutes = dt.hour * 60 + dt.minute - (9 * 60 + 30)
    return min(max(minutes // 5, 1), N_BUCKETS - 1)


def grade_session(
    session: date,
    daily_rows: list[dict[str, Any]],
    bars_5min_dir: Path,
    baselines: Baselines,
) -> list[dict[str, Any]]:
    path = bars_5min_dir / f"{session.isoformat()}.npz"
    if not path.exists():
        logger.warning("attention outcomes: no 5-min store for %s", session)
        return []
    g = load_5min(path)
    by_sym = {str(s): i for i, s in enumerate(g.symbols)}
    lit = {str(r["symbol"]): r for r in daily_rows}

    # Anchor per lit row; the session median of those anchors the non-lit peers.
    anchors: dict[str, tuple[int, str]] = {}
    for sym, r in lit.items():
        if r.get("abn_lit") and r.get("abn_bucket") is not None:
            anchors[sym] = (int(r["abn_bucket"]), "abn_first")
        elif r.get("flow_first_ts"):
            b = _ts_bucket(str(r["flow_first_ts"]))
            if b is not None:
                anchors[sym] = (b, "flow_first")
    lit_buckets = [b for b, _ in anchors.values()]
    median_bucket = int(np.median(lit_buckets)) if lit_buckets else FALLBACK_BUCKET

    out: list[dict[str, Any]] = []
    pool_syms = {str(s) for s, in_pool in zip(baselines.symbols, baselines.pool) if in_pool}
    for sym in sorted(pool_syms):
        j = by_sym.get(sym)
        row = baselines.row(sym)
        if j is None or row is None:
            continue
        r = lit.get(sym)
        bucket, anchor = anchors.get(sym, (median_bucket, "session_median"))
        fwd_norm = float(baselines.median_fwd_2h[row, bucket])
        graded = grade_event(
            close=g.close[j], high=g.high[j], low=g.low[j],
            bucket=bucket, fwd_norm=fwd_norm,
        )
        if not graded.get("ok"):
            continue
        quintile = r.get("quintile") if r else int(baselines.quintile[row])
        out.append(
            {
                "symbol": sym,
                "session": session.isoformat(),
                "angles": int(r["angles"]) if r else 0,
                "quintile": quintile if quintile is not None and int(quintile) >= 0 else None,
                "bucket": bucket,
                "anchor": anchor,
                "first_lit_ts": r.get("first_lit_ts") if r else None,
                "entry": graded["entry"],
                "move_2h": graded["move_2h"],
                "move_close": graded["move_close"],
                "excess_2h": graded["excess_2h"],
                "direction": graded["direction"],
                "horizon_complete": graded["horizon_complete"],
            }
        )
    return out


def sweep_attention_outcomes(
    session: date,
    *,
    bars_5min_dir: Path,
    baselines: Baselines,
    db_path: Path | None = None,
) -> int:
    from db.attention import insert_outcomes, list_rows

    daily = list_rows(session.isoformat(), min_quintile=0, min_angles=1, limit=500, db_path=db_path)
    rows = grade_session(session, daily, bars_5min_dir, baselines)
    return insert_outcomes(rows, db_path=db_path)
