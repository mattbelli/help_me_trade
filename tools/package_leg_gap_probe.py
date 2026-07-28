"""Package leg-gap probe — the pairing prerequisite for the o-band probe (design doc §3).

The Alpaca condition codes say WHICH prints are package legs; OPRA publishes no
package id, so pairing legs into groups is ours to define — and 1.9 measured
that legs frequently do NOT share one nanosecond (heuristic recall ~51%), so
ns-collision cannot be the grouping rule. This probe measures, on code-labeled
high-coverage sessions:

  1. the adjacent time-gap distribution among multileg-code prints of the same
     underlying (split same- vs different-instrument), against the unflagged
     background — where does the pairing signal live;
  2. the clean-pair curve: for a ladder of candidate windows W, single-linkage
     time-clusters of multileg prints per underlying, bucketed by number of
     DISTINCT instruments in the cluster (a leg can be venue-split into many
     prints — tcbbo doctrine): solo leg / 2-leg equal-size / 2-leg unequal /
     3+ legs. Premium share of each, as a function of W.

Report-only. The registered pairing rule gets written into
FLOW_PACKAGE_ORIENTATION_DESIGN as an addendum AFTER this curve is seen —
report the whole curve, never pick the window first.

    python tools/package_leg_gap_probe.py --dates 2026-07-24,2026-07-17
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tools.condition_labels import (  # noqa: E402
    MULTILEG_CODES,
    TIED_CODES,
    join_condition_codes,
)
from tools.flow_corpus_rollup import _load_definitions  # noqa: E402

PX = 1e9
DATA_DIR = ROOT / "data" / "databento"
NPZ_DIR = ROOT / "data" / "alpaca_conditions"

# Candidate single-linkage windows, ns. 0 = the old ns-collision assumption.
W_LADDER = [0, 1_000, 100_000, 1_000_000, 10_000_000, 100_000_000, 1_000_000_000]
W_LABELS = ["0ns", "1us", "100us", "1ms", "10ms", "100ms", "1s"]

GAP_EDGES = [0, 1_000, 10_000, 100_000, 1_000_000, 10_000_000, 100_000_000,
             1_000_000_000]
GAP_LABELS = ["=0", "<=1us", "<=10us", "<=100us", "<=1ms", "<=10ms", "<=100ms",
              "<=1s", ">1s"]


def _gap_hist(gaps: np.ndarray, w: np.ndarray) -> np.ndarray:
    """Premium-weighted histogram over GAP_EDGES buckets (=0 is its own)."""
    out = np.zeros(len(GAP_LABELS))
    zero = gaps == 0
    out[0] = w[zero].sum()
    idx = np.searchsorted(np.asarray(GAP_EDGES[1:]), gaps[~zero], side="left")
    np.add.at(out, idx + 1, w[~zero])
    return out


def _fmt_hist(h: np.ndarray) -> str:
    tot = h.sum() or 1.0
    return "  ".join(f"{lab} {v / tot:5.1%}" for lab, v in zip(GAP_LABELS, h))


def _load(session: date):
    import databento as dbn

    arr = dbn.DBNStore.from_file(
        DATA_DIR / "opra_tcbbo" / f"{session.isoformat()}.dbn.zst"
    ).to_ndarray()
    ts = arr["ts_event"].astype(np.int64)
    iid = arr["instrument_id"].astype(np.int64)
    price = arr["price"].astype(np.float64) / PX
    size = arr["size"].astype(np.int64)
    del arr

    defs = _load_definitions(session)
    if defs is None:
        raise SystemExit(f"{session}: no definitions file")
    d_ids, d_und, _uniq, _r, _k, _dte = defs
    pos = np.clip(np.searchsorted(d_ids, iid), 0, len(d_ids) - 1)
    ok = d_ids[pos] == iid
    und = np.where(ok, d_und[pos], -1)

    cents = np.round(price * 100).astype(np.int64)
    res = join_condition_codes(session, ts, iid, cents, size,
                               data_dir=DATA_DIR, npz_dir=NPZ_DIR)
    if res is None:
        raise SystemExit(f"{session}: no completed condition capture")
    cond, st = res
    return ts, iid, und, price, size, cond, st


def _clusters(ts: np.ndarray, und: np.ndarray, w_ns: int) -> np.ndarray:
    """Single-linkage group ids over (already und-then-ts sorted) rows."""
    new = np.empty(len(ts), dtype=bool)
    new[0] = True
    new[1:] = (und[1:] != und[:-1]) | ((ts[1:] - ts[:-1]) > w_ns)
    return np.cumsum(new) - 1


def run(session: date, pooled: dict) -> None:
    ts, iid, und, price, size, cond, st = _load(session)
    prem = price * size * 100.0
    resolved = und >= 0
    is_ml = np.isin(cond, list(MULTILEG_CODES)) & resolved
    is_tied = np.isin(cond, list(TIED_CODES)) & resolved
    tot_prem = prem[resolved].sum() or 1.0

    print(f"\n=== {session}  join {st['join_rate_dbn']:.2%}  "
          f"prints {len(ts):,} ===")
    print(f"  premium ${tot_prem / 1e9:.1f}B   "
          f"multileg {prem[is_ml].sum() / tot_prem:.1%}   "
          f"tied-to-stock {prem[is_tied].sum() / tot_prem:.1%} "
          f"(stock leg off-tape, excluded from pairing)")

    # ---- sort the multileg slice once: (underlying, time) ----
    m_ts, m_iid, m_und, m_prem, m_size = (
        a[is_ml] for a in (ts, iid, und, prem, size))
    order = np.lexsort((m_ts, m_und))
    m_ts, m_iid, m_und, m_prem, m_size = (
        a[order] for a in (m_ts, m_iid, m_und, m_prem, m_size))

    # ---- 1. adjacent-gap distribution (premium-weighted, same underlying) ----
    same_und = m_und[1:] == m_und[:-1]
    gaps = (m_ts[1:] - m_ts[:-1])[same_und]
    diff_iid = (m_iid[1:] != m_iid[:-1])[same_und]
    w_pair = np.minimum(m_prem[1:], m_prem[:-1])[same_und]
    h_diff = _gap_hist(gaps[diff_iid], w_pair[diff_iid])
    h_same = _gap_hist(gaps[~diff_iid], w_pair[~diff_iid])
    print("  adjacent-gap dist, multileg prints (premium-weighted):")
    print(f"    diff-instrument: {_fmt_hist(h_diff)}")
    print(f"    same-instrument: {_fmt_hist(h_same)}")
    pooled["h_diff"] = pooled.get("h_diff", 0) + h_diff
    pooled["h_same"] = pooled.get("h_same", 0) + h_same

    # unflagged background on the same underlyings (sampled by construction:
    # every unflagged resolved print, adjacent gaps within underlying)
    bg = resolved & ~is_ml & ~is_tied
    b_ts, b_und, b_prem = ts[bg], und[bg], prem[bg]
    border = np.lexsort((b_ts, b_und))
    b_ts, b_und, b_prem = b_ts[border], b_und[border], b_prem[border]
    b_same = b_und[1:] == b_und[:-1]
    h_bg = _gap_hist((b_ts[1:] - b_ts[:-1])[b_same],
                     np.minimum(b_prem[1:], b_prem[:-1])[b_same])
    print(f"    unflagged bkgd:  {_fmt_hist(h_bg)}")
    pooled["h_bg"] = pooled.get("h_bg", 0) + h_bg

    # ---- 2. clean-pair curve over the window ladder ----
    print("  cluster census by window (share of multileg premium; "
          "legs = DISTINCT instruments per cluster):")
    print(f"    {'W':>6}  {'solo':>6}  {'2-leg=sz':>8}  {'2-leg!sz':>8}  "
          f"{'3+leg':>6}  {'clusters':>9}  {'2=sz same-ns':>12}")
    ml_tot = m_prem.sum() or 1.0
    for w_ns, w_lab in zip(W_LADDER, W_LABELS):
        gid = _clusters(m_ts, m_und, w_ns)
        n_g = gid[-1] + 1 if len(gid) else 0
        c_prem = np.bincount(gid, weights=m_prem, minlength=n_g)

        # distinct instruments per cluster + per-(cluster, instrument) size sums
        o2 = np.lexsort((m_iid, gid))
        g2, i2 = gid[o2], m_iid[o2]
        first = np.empty(len(g2), dtype=bool)
        first[0] = True
        first[1:] = (g2[1:] != g2[:-1]) | (i2[1:] != i2[:-1])
        legs_per = np.bincount(g2[first], minlength=n_g)
        leg_id = np.cumsum(first) - 1
        leg_sz = np.bincount(leg_id, weights=m_size[o2].astype(np.float64))
        leg_ts_min = np.full(len(leg_sz), np.iinfo(np.int64).max, dtype=np.int64)
        np.minimum.at(leg_ts_min, leg_id, m_ts[o2])

        solo = legs_per == 1
        pair = legs_per == 2
        multi = legs_per >= 3
        # the two legs of each pair cluster sit adjacent in the leg arrays
        leg_gid = g2[first]
        pair_leg0 = np.flatnonzero(np.isin(leg_gid, np.flatnonzero(pair)))[0::2] \
            if pair.any() else np.empty(0, dtype=np.int64)
        eq = np.zeros(n_g, dtype=bool)
        ns0 = np.zeros(n_g, dtype=bool)
        if len(pair_leg0):
            eq_pairs = leg_sz[pair_leg0] == leg_sz[pair_leg0 + 1]
            ns_pairs = leg_ts_min[pair_leg0] == leg_ts_min[pair_leg0 + 1]
            eq[leg_gid[pair_leg0[eq_pairs]]] = True
            ns0[leg_gid[pair_leg0[ns_pairs & eq_pairs]]] = True
        p_solo = c_prem[solo].sum() / ml_tot
        p_eq = c_prem[pair & eq].sum() / ml_tot
        p_uneq = c_prem[pair & ~eq].sum() / ml_tot
        p_multi = c_prem[multi].sum() / ml_tot
        eq_prem = c_prem[pair & eq].sum() or 1.0
        p_ns0 = c_prem[ns0].sum() / eq_prem
        print(f"    {w_lab:>6}  {p_solo:6.1%}  {p_eq:8.1%}  {p_uneq:8.1%}  "
              f"{p_multi:6.1%}  {n_g:9,}  {p_ns0:12.1%}")
        key = ("curve", w_lab)
        acc = pooled.setdefault(key, np.zeros(5))
        acc += np.array([c_prem[solo].sum(), c_prem[pair & eq].sum(),
                         c_prem[pair & ~eq].sum(), c_prem[multi].sum(), ml_tot])


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--dates", required=True,
                   help="comma-separated session dates (code-labeled, high coverage)")
    sessions = [date.fromisoformat(s) for s in p.parse_args().dates.split(",")]
    pooled: dict = {}
    for s in sessions:
        run(s, pooled)

    print(f"\n=== POOLED over {len(sessions)} sessions ===")
    print("  adjacent-gap dist (premium-weighted):")
    print(f"    diff-instrument: {_fmt_hist(pooled['h_diff'])}")
    print(f"    same-instrument: {_fmt_hist(pooled['h_same'])}")
    print(f"    unflagged bkgd:  {_fmt_hist(pooled['h_bg'])}")
    print("  clean-pair curve (share of multileg premium):")
    print(f"    {'W':>6}  {'solo':>6}  {'2-leg=sz':>8}  {'2-leg!sz':>8}  {'3+leg':>6}")
    for w_lab in W_LABELS:
        solo, eq, uneq, multi, tot = pooled[("curve", w_lab)]
        print(f"    {w_lab:>6}  {solo / tot:6.1%}  {eq / tot:8.1%}  "
              f"{uneq / tot:8.1%}  {multi / tot:6.1%}")
    print("\nNext (per design doc §3): register the pairing window as an addendum "
          "to FLOW_PACKAGE_ORIENTATION_DESIGN before the o probe runs.")


if __name__ == "__main__":
    main()
