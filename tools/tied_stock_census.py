#!/usr/bin/env python3
"""Tied-to-stock feasibility census — ON DISK ONLY, no equity fetches.

Counts, per session: TIED-code option prints; same-underlying 100us clusters
among them; 1-option-leg vs 2-option-leg(equal size) cluster counts + premium;
equity-class share; band validity + spread-edge share for the 2-leg subset;
code letter breakdown (k n o p q r s). Decides whether the o sign validation
can ride the tied-stock ground truth.
"""
import sys
from datetime import date
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tools.condition_labels import TIED_CODES  # noqa: E402
from tools.package_orientation_probe import _clusters, _load  # noqa: E402

W_NS = 100_000
SESSIONS = ["2025-09-16", "2026-01-07", "2026-04-16"]

for s in SESSIONS:
    session = date.fromisoformat(s)
    ts, iid, und, right, price, size, bid, ask, cond, st = _load(session)
    prem = price * size * 100.0
    resolved = und >= 0
    tied = np.isin(cond, list(TIED_CODES)) & resolved
    n_tied = int(tied.sum())
    from collections import Counter
    codes = Counter(cond[tied].tolist())

    order = np.lexsort((ts[tied], und[tied]))
    t_ts, t_iid, t_und = ts[tied][order], iid[tied][order], und[tied][order]
    t_price, t_size = price[tied][order], size[tied][order]
    t_prem = prem[tied][order]
    t_bid, t_ask = bid[tied][order], ask[tied][order]
    if not len(t_ts):
        print(f"{s}: no tied prints")
        continue
    gid = _clusters(t_ts, t_und, W_NS)
    n_g = int(gid[-1]) + 1

    o2 = np.lexsort((t_ts, t_iid, gid))
    g2, i2 = gid[o2], t_iid[o2]
    first = np.empty(len(g2), dtype=bool)
    first[0] = True
    first[1:] = (g2[1:] != g2[:-1]) | (i2[1:] != i2[:-1])
    leg_id = np.cumsum(first) - 1
    leg_gid = g2[first]
    leg_sz = np.bincount(leg_id, weights=t_size[o2].astype(np.float64))
    leg_vwap = np.bincount(leg_id, weights=(t_price * t_size)[o2]) / leg_sz
    fidx = np.flatnonzero(first)
    leg_bid, leg_ask = t_bid[o2][fidx], t_ask[o2][fidx]

    legs_per = np.bincount(leg_gid, minlength=n_g)
    c_prem = np.bincount(gid, weights=t_prem, minlength=n_g)
    n1 = int((legs_per == 1).sum())
    p1 = c_prem[legs_per == 1].sum()
    pair_g = np.flatnonzero(legs_per == 2)
    a_idx = np.flatnonzero(np.isin(leg_gid, pair_g))[0::2] \
        if len(pair_g) else np.empty(0, dtype=np.int64)
    b_idx = a_idx + 1
    eq = leg_sz[a_idx] == leg_sz[b_idx]
    A, B = a_idx[eq], b_idx[eq]
    p2 = (c_prem[pair_g]).sum()
    n3 = int((legs_per >= 3).sum())

    # band validity + in-band spread-edge for equal-size 2-leg
    lo = leg_bid[A] - leg_ask[B]
    hi = leg_ask[A] - leg_bid[B]
    width = hi - lo
    valid = np.isfinite(lo) & np.isfinite(hi) & (width > 0)
    o = np.full(len(A), np.nan)
    with np.errstate(invalid="ignore", divide="ignore"):
        o[valid] = ((leg_vwap[A] - leg_vwap[B] - lo) / width)[valid]
    inband = valid & (o >= 0) & (o <= 1)
    edge = inband & (np.minimum(o, 1 - o) <= 0.10)

    tot_prem = prem[resolved].sum()
    print(f"{s}: tied prints {n_tied:,} ({t_prem.sum() / tot_prem:.2%} of premium) "
          f"codes {dict(codes)}")
    print(f"   clusters {n_g:,}: 1-leg {n1:,} (${p1 / 1e6:.1f}M)  "
          f"2-leg {len(pair_g):,} (eq-size {len(A):,}, ${p2 / 1e6:.1f}M)  3+ {n3:,}")
    print(f"   2-leg eq: band-valid {int(valid.sum()):,}  in-band {int(inband.sum()):,}  "
          f"edge@0.10 {int(edge.sum()):,}")
