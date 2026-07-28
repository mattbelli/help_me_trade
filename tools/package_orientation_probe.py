"""Package orientation probe — the §3 o-band distribution vs a shuffled-pairing null.

Per FLOW_PACKAGE_ORIENTATION_DESIGN §3 + §3a: reassemble packages from
code-flagged prints using the REGISTERED pairing rule (same-underlying
single-linkage clusters at W=100us, leg = distinct instrument, leg size =
per-instrument size sum; tied-to-stock excluded), keep the first-pass
universe — exactly-2-leg equal-size clusters — and locate the net price
d = pA - pB inside the synthetic band [bidA-askB, askA-bidB]:

    o_diff = (d - (bidA - askB)) / ((askA - bidB) - (bidA - askB))

o_diff near 1 => aggressor bought A / sold B; near 0 => the reverse. Per §3b,
that band only carries information for SPREAD-type packages (net = the
difference); for same-direction combos (straddles: net = the SUM) the diff
coordinate is allocation noise, so the complement band is computed too:

    o_sum = (pA + pB - (bidA + bidB)) / ((askA + askB) - (bidA + bidB))

(both bands share one width, spreadA + spreadB). The probe's question is
whether (o_diff, o_sum) CONCENTRATES near band edges on real pairings far
more than on shuffled leg-pairings (same legs, B legs permuted within the
same underlying) — if leg-price allocation were arbitrary, it would not.
Registered pass/fail (§3b): real 2D edge share at c=0.10 >= 1.5x null.

Conventions, stated in advance:
  - A = the lower instrument_id of the pair (deterministic; edge
    concentration is symmetric under A/B swap, so the choice cannot
    manufacture a result).
  - leg price = size-weighted average across the leg's venue-split prints;
    leg BBO = the snapshot on the leg's FIRST print (tcbbo doctrine: one
    pre-trade snapshot per sweep).
  - pairs with any missing/one-sided BBO or a zero-width band abstain and
    are reported as coverage, never silently dropped.
  - output = distributions + coverage shares ONLY. No grading, no win%.

    python tools/package_orientation_probe.py --dates 2026-07-24,2026-07-17
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
UNDEF = np.iinfo(np.int64).max
DATA_DIR = ROOT / "data" / "databento"
NPZ_DIR = ROOT / "data" / "alpaca_conditions"

W_NS = 100_000          # registered pairing window (design doc §3a)
N_SHUFFLES = 3          # null draws per session, pooled
SEED = 20260728         # fixed for reproducibility

O_EDGES = np.array([-0.1, 0.0, 0.05, 0.15, 0.35, 0.65, 0.85, 0.95, 1.0, 1.1])
O_LABELS = ["<-.1", "-.1-0", "0-.05", ".05-.15", ".15-.35", ".35-.65",
            ".65-.85", ".85-.95", ".95-1", "1-1.1", ">1.1"]
EDGE_CUTS = [0.05, 0.10, 0.15]


def _o_hist(o: np.ndarray, w: np.ndarray) -> np.ndarray:
    out = np.zeros(len(O_LABELS))
    idx = np.searchsorted(O_EDGES, o, side="left")
    idx[o == 0.0] += 1  # exact bid-edge belongs in [0, .05), not (-.1, 0]
    np.add.at(out, idx, w)
    return out


def _fmt_hist(h: np.ndarray) -> str:
    tot = h.sum() or 1.0
    return "  ".join(f"{lab} {v / tot:5.1%}" for lab, v in zip(O_LABELS, h))


def _edge_share(o: np.ndarray, w: np.ndarray, cut: float) -> float:
    tot = w.sum() or 1.0
    return w[(o <= cut) | (o >= 1.0 - cut)].sum() / tot


def _load(session: date):
    import databento as dbn

    arr = dbn.DBNStore.from_file(
        DATA_DIR / "opra_tcbbo" / f"{session.isoformat()}.dbn.zst"
    ).to_ndarray()
    ts = arr["ts_event"].astype(np.int64)
    iid = arr["instrument_id"].astype(np.int64)
    price = arr["price"].astype(np.float64) / PX
    size = arr["size"].astype(np.int64)
    bid_raw = arr["bid_px_00"].astype(np.int64)
    ask_raw = arr["ask_px_00"].astype(np.int64)
    del arr
    bid = np.where((bid_raw > 0) & (bid_raw < UNDEF), bid_raw / PX, np.nan)
    ask = np.where((ask_raw > 0) & (ask_raw < UNDEF), ask_raw / PX, np.nan)

    defs = _load_definitions(session)
    if defs is None:
        raise SystemExit(f"{session}: no definitions file")
    d_ids, d_und, _uniq, d_right, _k, _dte = defs
    pos = np.clip(np.searchsorted(d_ids, iid), 0, len(d_ids) - 1)
    ok = d_ids[pos] == iid
    und = np.where(ok, d_und[pos], -1)
    right = np.where(ok, d_right[pos].astype(np.int8), -1)

    cents = np.round(price * 100).astype(np.int64)
    res = join_condition_codes(session, ts, iid, cents, size,
                               data_dir=DATA_DIR, npz_dir=NPZ_DIR)
    if res is None:
        raise SystemExit(f"{session}: no completed condition capture")
    cond, st = res
    return ts, iid, und, right, price, size, bid, ask, cond, st


def _clusters(ts: np.ndarray, und: np.ndarray, w_ns: int) -> np.ndarray:
    new = np.empty(len(ts), dtype=bool)
    new[0] = True
    new[1:] = (und[1:] != und[:-1]) | ((ts[1:] - ts[:-1]) > w_ns)
    return np.cumsum(new) - 1


def _null_perm(und_pair: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    """Permutation of pair indices that shuffles within each underlying
    (underlyings with a single pair map to themselves and are excluded
    by the caller via the returned mask)."""
    perm = np.arange(len(und_pair))
    order = np.argsort(und_pair, kind="stable")
    sorted_u = und_pair[order]
    starts = np.flatnonzero(np.r_[True, sorted_u[1:] != sorted_u[:-1]])
    ends = np.r_[starts[1:], len(sorted_u)]
    for s, e in zip(starts, ends):
        if e - s >= 2:
            seg = order[s:e]
            perm[seg] = seg[rng.permutation(e - s)]
    return perm


def run(session: date, pooled: dict) -> None:
    ts, iid, und, right, price, size, bid, ask, cond, st = _load(session)
    prem = price * size * 100.0
    resolved = und >= 0
    tot_prem = prem[resolved].sum() or 1.0
    is_ml = np.isin(cond, list(MULTILEG_CODES)) & resolved
    is_tied = np.isin(cond, list(TIED_CODES)) & resolved

    m = is_ml
    order = np.lexsort((ts[m], und[m]))
    m_ts = ts[m][order]
    m_iid = iid[m][order]
    m_und = und[m][order]
    m_right = right[m][order]
    m_price = price[m][order]
    m_size = size[m][order]
    m_prem = prem[m][order]
    m_bid = bid[m][order]
    m_ask = ask[m][order]
    ml_tot = m_prem.sum() or 1.0

    gid = _clusters(m_ts, m_und, W_NS)
    n_g = gid[-1] + 1 if len(gid) else 0

    # ---- legs: sort by (cluster, instrument, time); first row = min-ts print
    o2 = np.lexsort((m_ts, m_iid, gid))
    g2, i2 = gid[o2], m_iid[o2]
    first = np.empty(len(g2), dtype=bool)
    first[0] = True
    first[1:] = (g2[1:] != g2[:-1]) | (i2[1:] != i2[:-1])
    leg_id = np.cumsum(first) - 1
    leg_gid = g2[first]
    leg_sz = np.bincount(leg_id, weights=m_size[o2].astype(np.float64))
    leg_vwap = np.bincount(leg_id, weights=(m_price * m_size)[o2]) / leg_sz
    leg_prem = np.bincount(leg_id, weights=m_prem[o2])
    first_idx = np.flatnonzero(first)
    leg_bid = m_bid[o2][first_idx]
    leg_ask = m_ask[o2][first_idx]
    leg_right = m_right[o2][first_idx]
    leg_und = m_und[o2][first_idx]

    # ---- cluster census at the registered window (coverage report) ----
    legs_per = np.bincount(leg_gid, minlength=n_g)
    c_prem = np.bincount(gid, weights=m_prem, minlength=n_g)
    pair_g = np.flatnonzero(legs_per == 2)
    a_idx = np.flatnonzero(np.isin(leg_gid, pair_g))[0::2] \
        if len(pair_g) else np.empty(0, dtype=np.int64)
    b_idx = a_idx + 1
    eq = leg_sz[a_idx] == leg_sz[b_idx]
    A, B = a_idx[eq], b_idx[eq]
    pair_prem = leg_prem[A] + leg_prem[B]
    p_solo = c_prem[legs_per == 1].sum() / ml_tot
    p_eq = pair_prem.sum() / ml_tot
    p_uneq = (c_prem[pair_g].sum() - pair_prem.sum()) / ml_tot
    p_multi = c_prem[legs_per >= 3].sum() / ml_tot

    # ---- the band ----
    lo = leg_bid[A] - leg_ask[B]
    hi = leg_ask[A] - leg_bid[B]
    width = hi - lo
    valid = np.isfinite(lo) & np.isfinite(hi) & (width > 0)
    vA, vB = A[valid], B[valid]
    vw = width[valid]
    o = (leg_vwap[vA] - leg_vwap[vB] - lo[valid]) / vw
    o_s = (leg_vwap[vA] + leg_vwap[vB]
           - (leg_bid[vA] + leg_bid[vB])) / vw
    w = pair_prem[valid]
    v_und = leg_und[vA]

    print(f"\n=== {session}  join {st['join_rate_dbn']:.2%}  "
          f"prints {len(ts):,} ===")
    print(f"  premium ${tot_prem / 1e9:.1f}B   multileg {ml_tot / tot_prem:.1%}"
          f"   tied-to-stock {prem[is_tied].sum() / tot_prem:.1%} (excluded)")
    print(f"  buckets @100us: solo {p_solo:.1%}  pair=sz {p_eq:.1%}  "
          f"pair!sz {p_uneq:.1%}  3+leg {p_multi:.1%}   "
          f"(shares of multileg premium)")
    print(f"  clean pairs {len(A):,} = {p_eq:.1%} of multileg = "
          f"{pair_prem.sum() / tot_prem:.1%} of ALL premium; "
          f"valid band {valid.sum():,} pairs = "
          f"{w.sum() / (pair_prem.sum() or 1.0):.1%} of clean-pair premium "
          f"= {w.sum() / tot_prem:.1%} of ALL")

    # ---- shuffled-pairing null: same legs, B side permuted within underlying
    rng = np.random.default_rng(SEED + session.toordinal())
    null_o, null_os, null_w = [], [], []
    moved_share = 0.0
    for _ in range(N_SHUFFLES):
        perm = _null_perm(v_und, rng)
        moved = perm != np.arange(len(perm))
        moved_share = max(moved_share, moved.mean() if len(perm) else 0.0)
        nB = vB[perm]
        n_lo = leg_bid[vA] - leg_ask[nB]
        n_hi = leg_ask[vA] - leg_bid[nB]
        n_wd = n_hi - n_lo
        n_ok = moved & (n_wd > 0)
        null_o.append(((leg_vwap[vA] - leg_vwap[nB]) - n_lo)[n_ok] / n_wd[n_ok])
        null_os.append((leg_vwap[vA] + leg_vwap[nB]
                        - (leg_bid[vA] + leg_bid[nB]))[n_ok] / n_wd[n_ok])
        null_w.append((leg_prem[vA] + leg_prem[nB])[n_ok])
    null_o = np.concatenate(null_o)
    null_os = np.concatenate(null_os)
    null_w = np.concatenate(null_w)

    print("  o_diff distribution (premium-weighted):")
    print(f"    real: {_fmt_hist(_o_hist(o, w))}")
    print(f"    null: {_fmt_hist(_o_hist(null_o, null_w))}   "
          f"({N_SHUFFLES} shuffles, {moved_share:.0%} of pairs re-paired)")
    print("  o_sum distribution (premium-weighted):")
    print(f"    real: {_fmt_hist(_o_hist(o_s, w))}")
    print(f"    null: {_fmt_hist(_o_hist(null_os, null_w))}")

    # edge distance per coordinate; out-of-band (negative) counts as edge
    ed, es = np.minimum(o, 1 - o), np.minimum(o_s, 1 - o_s)
    n_ed = np.minimum(null_o, 1 - null_o)
    n_es = np.minimum(null_os, 1 - null_os)
    e2, n_e2 = np.minimum(ed, es), np.minimum(n_ed, n_es)
    cuts_real = [_edge_share(o, w, c) for c in EDGE_CUTS]
    cuts_null = [_edge_share(null_o, null_w, c) for c in EDGE_CUTS]
    print("  1D o_diff edge share (premium-weighted):")
    print("    " + "   ".join(
        f"c={c:.2f} real {r:5.1%} null {n:5.1%}"
        for c, r, n in zip(EDGE_CUTS, cuts_real, cuts_null)))
    w_tot, nw_tot = w.sum() or 1.0, null_w.sum() or 1.0
    e2_real = [w[e2 <= c].sum() / w_tot for c in EDGE_CUTS]
    e2_null = [null_w[n_e2 <= c].sum() / nw_tot for c in EDGE_CUTS]
    print("  2D edge share (min over both coords <= c, premium-weighted):")
    print("    " + "   ".join(
        f"c={c:.2f} real {r:5.1%} null {n:5.1%}"
        for c, r, n in zip(EDGE_CUTS, e2_real, e2_null)))

    # corner decomposition at c=0.10 (descriptive)
    corners = np.array([
        w[(ed <= .1) & (es > .1)].sum(), w[(es <= .1) & (ed > .1)].sum(),
        w[(ed <= .1) & (es <= .1)].sum(), w[(ed > .1) & (es > .1)].sum(),
        null_w[(n_ed <= .1) & (n_es > .1)].sum(),
        null_w[(n_es <= .1) & (n_ed > .1)].sum(),
        null_w[(n_ed <= .1) & (n_es <= .1)].sum(),
        null_w[(n_ed > .1) & (n_es > .1)].sum()])
    print(f"  corners @0.10  real: spread-edge {corners[0] / w_tot:5.1%}  "
          f"combo-edge {corners[1] / w_tot:5.1%}  both {corners[2] / w_tot:5.1%}  "
          f"neither {corners[3] / w_tot:5.1%}")
    print(f"                 null: spread-edge {corners[4] / nw_tot:5.1%}  "
          f"combo-edge {corners[5] / nw_tot:5.1%}  both {corners[6] / nw_tot:5.1%}  "
          f"neither {corners[7] / nw_tot:5.1%}")

    # ---- structure split (descriptive): call-call / put-put / mixed ----
    rA, rB = leg_right[vA], leg_right[vB]
    for lab, mask in (("call-call", (rA == 0) & (rB == 0)),
                      ("put-put", (rA == 1) & (rB == 1)),
                      ("mixed", rA != rB)):
        wm = w[mask]
        wm_tot = wm.sum() or 1.0
        sp = wm[(ed[mask] <= .1) & (es[mask] > .1)].sum() / wm_tot
        cb = wm[(es[mask] <= .1) & (ed[mask] > .1)].sum() / wm_tot
        print(f"    {lab:>9}: {wm.sum() / w_tot:5.1%} of valid-pair premium, "
              f"spread-edge@.10 {sp:5.1%}, combo-edge@.10 {cb:5.1%}")
        acc = pooled.setdefault(("split", lab), np.zeros(4))
        acc += np.array([wm.sum(),
                         wm[(ed[mask] <= .1) & (es[mask] > .1)].sum(),
                         wm[(es[mask] <= .1) & (ed[mask] > .1)].sum(),
                         w.sum()])

    pooled["h_real"] = pooled.get("h_real", 0) + _o_hist(o, w)
    pooled["h_null"] = pooled.get("h_null", 0) + _o_hist(null_o, null_w)
    pooled["h_real_s"] = pooled.get("h_real_s", 0) + _o_hist(o_s, w)
    pooled["h_null_s"] = pooled.get("h_null_s", 0) + _o_hist(null_os, null_w)
    for c, r_, n_, r2, n2 in zip(EDGE_CUTS, cuts_real, cuts_null,
                                 e2_real, e2_null):
        acc = pooled.setdefault(("edge", c), np.zeros(4))
        acc += np.array([r_ * w.sum(), w.sum(), n_ * null_w.sum(), null_w.sum()])
        acc = pooled.setdefault(("edge2", c), np.zeros(4))
        acc += np.array([r2 * w.sum(), w.sum(), n2 * null_w.sum(), null_w.sum()])
    pooled["corners"] = pooled.get("corners", 0) + corners
    pooled["corner_tots"] = pooled.get("corner_tots", 0) + \
        np.array([w.sum(), null_w.sum()])
    acc = pooled.setdefault("cov", np.zeros(3))
    acc += np.array([w.sum(), pair_prem.sum(), tot_prem])


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--dates", required=True,
                   help="comma-separated session dates (code-labeled, high coverage)")
    sessions = [date.fromisoformat(s) for s in p.parse_args().dates.split(",")]
    pooled: dict = {}
    for s in sessions:
        run(s, pooled)

    print(f"\n=== POOLED over {len(sessions)} sessions ===")
    v, pp, tot = pooled["cov"]
    print(f"  o-readable premium: ${v / 1e9:.1f}B = {v / tot:.1%} of ALL "
          f"(clean pairs {pp / tot:.1%}, of which valid band {v / (pp or 1):.1%})")
    print("  o_diff distribution (premium-weighted):")
    print(f"    real: {_fmt_hist(pooled['h_real'])}")
    print(f"    null: {_fmt_hist(pooled['h_null'])}")
    print("  o_sum distribution (premium-weighted):")
    print(f"    real: {_fmt_hist(pooled['h_real_s'])}")
    print(f"    null: {_fmt_hist(pooled['h_null_s'])}")
    print("  1D o_diff edge share, premium-weighted:")
    print("    " + "   ".join(
        f"c={c:.2f} real {pooled[('edge', c)][0] / pooled[('edge', c)][1]:5.1%} "
        f"null {pooled[('edge', c)][2] / pooled[('edge', c)][3]:5.1%}"
        for c in EDGE_CUTS))
    print("  2D edge share (min over both coords <= c), premium-weighted:")
    print("    " + "   ".join(
        f"c={c:.2f} real {pooled[('edge2', c)][0] / pooled[('edge2', c)][1]:5.1%} "
        f"null {pooled[('edge2', c)][2] / pooled[('edge2', c)][3]:5.1%}"
        for c in EDGE_CUTS))
    r2 = pooled[("edge2", 0.10)]
    ratio = (r2[0] / r2[1]) / ((r2[2] / r2[3]) or 1.0)
    print(f"  registered §3b criterion @0.10: real/null = {ratio:.2f}x "
          f"(pass >= 1.50x)")
    cw, cn = pooled["corner_tots"]
    cr = pooled["corners"]
    c_ratio = ((cr[0] + cr[1]) / cw) / (((cr[4] + cr[5]) / cn) or 1.0)
    print(f"  registered §3c criterion @0.10 (exactly-one-coord edge, "
          f"both-edge excluded): real/null = {c_ratio:.2f}x (pass >= 1.50x)")
    print(f"  corners @0.10  real: spread-edge {cr[0] / cw:5.1%}  "
          f"combo-edge {cr[1] / cw:5.1%}  both {cr[2] / cw:5.1%}  "
          f"neither {cr[3] / cw:5.1%}")
    print(f"                 null: spread-edge {cr[4] / cn:5.1%}  "
          f"combo-edge {cr[5] / cn:5.1%}  both {cr[6] / cn:5.1%}  "
          f"neither {cr[7] / cn:5.1%}")
    print("  splits (share of valid-pair premium):")
    for lab in ("call-call", "put-put", "mixed"):
        wsum, spsum, cbsum, wall = pooled[("split", lab)]
        print(f"    {lab:>9}: {wsum / (wall or 1):5.1%}  "
              f"spread-edge@.10 {spsum / (wsum or 1):5.1%}  "
              f"combo-edge@.10 {cbsum / (wsum or 1):5.1%}")
    print("\nReport-only: distribution + coverage per design doc §3/§3b. The "
          "registered pass/fail is the 2D criterion line above; any grading "
          "spec enters Addendum E (or its own prereg) first.")


if __name__ == "__main__":
    main()
