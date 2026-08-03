#!/usr/bin/env python3
"""COUNT-weighted §3c corner enrichment on the five fresh OOS sessions (3.4 round 1).

Reviewer-A B7: the registered §3c pass (3.33x) is PREMIUM-weighted; 3.4's cell
is EVENT-counted. This re-reads the same five never-seen sessions with weight=1
per valid pair and reports the same corner ratios. Read-only, on-disk, no
outcomes. Reuses package_orientation_probe's own loaders verbatim.
"""
import sys
from datetime import date
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tools.package_orientation_probe import (  # noqa: E402
    N_SHUFFLES,
    SEED,
    W_NS,
    _clusters,
    _load,
    _null_perm,
)

SESSIONS = ["2025-08-22", "2025-10-31", "2026-01-07", "2026-04-16", "2026-06-29"]
C = 0.10

tot = np.zeros(8)  # real: spread, combo, both, neither; null: same
for s in SESSIONS:
    session = date.fromisoformat(s)
    ts, iid, und, right, price, size, bid, ask, cond, st = _load(session)
    from tools.condition_labels import MULTILEG_CODES  # noqa: E402
    resolved = und >= 0
    m = np.isin(cond, list(MULTILEG_CODES)) & resolved
    order = np.lexsort((ts[m], und[m]))
    m_ts, m_iid, m_und = ts[m][order], iid[m][order], und[m][order]
    m_price, m_size = price[m][order], size[m][order]
    m_bid, m_ask = bid[m][order], ask[m][order]
    if not len(m_ts):
        continue
    gid = _clusters(m_ts, m_und, W_NS)

    o2 = np.lexsort((m_ts, m_iid, gid))
    g2, i2 = gid[o2], m_iid[o2]
    first = np.empty(len(g2), dtype=bool)
    first[0] = True
    first[1:] = (g2[1:] != g2[:-1]) | (i2[1:] != i2[:-1])
    leg_id = np.cumsum(first) - 1
    leg_gid = g2[first]
    leg_sz = np.bincount(leg_id, weights=m_size[o2].astype(np.float64))
    leg_vwap = np.bincount(leg_id, weights=(m_price * m_size)[o2]) / leg_sz
    fidx = np.flatnonzero(first)
    leg_bid, leg_ask = m_bid[o2][fidx], m_ask[o2][fidx]
    leg_und = m_und[o2][fidx]

    n_g = int(leg_gid[-1]) + 1
    legs_per = np.bincount(leg_gid, minlength=n_g)
    pair_g = np.flatnonzero(legs_per == 2)
    a_idx = np.flatnonzero(np.isin(leg_gid, pair_g))[0::2] \
        if len(pair_g) else np.empty(0, dtype=np.int64)
    b_idx = a_idx + 1
    eq = leg_sz[a_idx] == leg_sz[b_idx]
    A, B = a_idx[eq], b_idx[eq]

    lo = leg_bid[A] - leg_ask[B]
    hi = leg_ask[A] - leg_bid[B]
    width = hi - lo
    valid = np.isfinite(lo) & np.isfinite(hi) & (width > 0)
    vA, vB = A[valid], B[valid]
    vw = width[valid]
    o = (leg_vwap[vA] - leg_vwap[vB] - lo[valid]) / vw
    o_s = (leg_vwap[vA] + leg_vwap[vB] - (leg_bid[vA] + leg_bid[vB])) / vw
    ed, es = np.minimum(o, 1 - o), np.minimum(o_s, 1 - o_s)
    v_und = leg_und[vA]

    rng = np.random.default_rng(SEED + session.toordinal())
    n_sp = n_cb = n_bo = n_ne = 0
    for _ in range(N_SHUFFLES):
        perm = _null_perm(v_und, rng)
        moved = perm != np.arange(len(perm))
        nB = vB[perm]
        n_lo = leg_bid[vA] - leg_ask[nB]
        n_hi = leg_ask[vA] - leg_bid[nB]
        n_wd = n_hi - n_lo
        ok = moved & (n_wd > 0)
        no = ((leg_vwap[vA] - leg_vwap[nB]) - n_lo)[ok] / n_wd[ok]
        nos = (leg_vwap[vA] + leg_vwap[nB] - (leg_bid[vA] + leg_bid[nB]))[ok] / n_wd[ok]
        ned, nes = np.minimum(no, 1 - no), np.minimum(nos, 1 - nos)
        n_sp += int(((ned <= C) & (nes > C)).sum())
        n_cb += int(((nes <= C) & (ned > C)).sum())
        n_bo += int(((ned <= C) & (nes <= C)).sum())
        n_ne += int(((ned > C) & (nes > C)).sum())

    r_sp = int(((ed <= C) & (es > C)).sum())
    r_cb = int(((es <= C) & (ed > C)).sum())
    r_bo = int(((ed <= C) & (es <= C)).sum())
    r_ne = int(((ed > C) & (es > C)).sum())
    tot += np.array([r_sp, r_cb, r_bo, r_ne, n_sp, n_cb, n_bo, n_ne],
                    dtype=np.float64)
    rt = r_sp + r_cb + r_bo + r_ne or 1
    nt = n_sp + n_cb + n_bo + n_ne or 1
    print(f"{s}: pairs {rt:,}  COUNT-weighted spread-edge real {r_sp / rt:.2%} "
          f"null {n_sp / nt:.2%}  one-coord real {(r_sp + r_cb) / rt:.2%} "
          f"null {(n_sp + n_cb) / nt:.2%}")

rt = tot[:4].sum() or 1
nt = tot[4:].sum() or 1
sp_ratio = (tot[0] / rt) / ((tot[4] / nt) or 1)
oc_ratio = ((tot[0] + tot[1]) / rt) / (((tot[4] + tot[5]) / nt) or 1)
print(f"\nPOOLED 5 fresh sessions, COUNT-weighted @c={C}:")
print(f"  spread-edge-only: real {tot[0] / rt:.2%}  null {tot[4] / nt:.2%}  "
      f"ratio {sp_ratio:.2f}x")
print(f"  exactly-one-coord (registered form): real {(tot[0] + tot[1]) / rt:.2%}  "
      f"null {(tot[4] + tot[5]) / nt:.2%}  ratio {oc_ratio:.2f}x")
