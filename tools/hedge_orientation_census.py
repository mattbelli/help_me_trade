#!/usr/bin/env python3
"""Phase 3.4 stage-0 funnel census — ON DISK ONLY, run BEFORE the spec freezes.

Question it answers: if 3.4 (spread orientation from the hedge footprint's
DIRECTION) ran over the code-provenance training sessions, how many 2-leg
package events survive the 3.2-style filters, what do their predicted net
hedges |H_pred| look like, and how big is the intersection of
  (a) o-band ORIENTATION-CONFIDENT pairs (§3c spread-edge corner at c=0.10) and
  (b) detectability-proxy D >= 3?
That intersection is the validation cell the 3.4 agreement test lives in; its
size decides the registered floors and whether the phase is worth running.

Consumes NO equity microstructure and NO outcomes: tcbbo prints + definitions +
condition codes + daily closes/ADV already in the corpus DB. The detectability
number here is a PROXY (ADV pro-rata over 29.1s of a 6.5h session); the real
stage-2 gate uses time-of-day median minute volume, which runs 1-40x pro-rata —
the proxy therefore OVERSTATES detectability early in the day and the census
labels it as such everywhere.

Pairing = the registered §3a rule verbatim (multileg codes only, tied-to-stock
excluded, same-underlying single-linkage clusters at W=100us, leg = distinct
instrument, first-pass universe = exactly-2-leg equal-size clusters).

KNOWN GAPS (measured by the 2026-08-03 two-Opus review of the 3.4 draft this
census informed; kept as-run because the round closed BLOCKED — fix these
before any number below is reused): no isolation filter (the dominant 3.2
filter — 98% of candidates die there; applying the draft's rule on 2025-09-16
cut the intersection 6 -> 4), no news fence, no per-underlying cap, no
half-day exclusion; out-of-band o_diff counted as edge (18% of the confident
cell); D_proxy mislabeled "overstates" (pro-rata overstates at open/close,
UNDERSTATES midday); funnel row `both_legs_defined` is a no-op; `band_valid`
sits inside the equity->premium chain; pair premium is GROSS both legs;
`swap` block is dead code (the [0::2] stride already enforces A = lower id).

    .venv/bin/python tools/hedge_orientation_census.py            # ~12 spread sessions
    .venv/bin/python tools/hedge_orientation_census.py --every 5  # denser sample
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from db.flow_corpus import DEFAULT_DB_PATH, bars_before_date  # noqa: E402
from tools.condition_labels import JOIN_FLOOR, MULTILEG_CODES  # noqa: E402
from tools.flow_corpus_rollup import (  # noqa: E402
    PX,
    TCBBO_DIR,
    fence_and_classify,
    multileg_flags,
)
from tools.hedge_footprint import (  # noqa: E402
    ADV_MIN,
    DTE_MIN,
    PREMIUM_MIN,
    PRICE_MIN,
    UNDEF_SIZE,
    adv_before_date,
    et_second_of_day,
    fit_delta,
    in_release_fence,
    in_trading_window,
    load_option_defs,
    session_midnight_ns,
    time_to_expiry_years,
)
from tools.root_classification_build import load as load_classification  # noqa: E402

W_NS = 100_000                    # registered §3a pairing window
EDGE_C = 0.10                     # registered §3c corner cut
RTH_S = 6.5 * 3600.0
EXPECTED_WINDOW_S = 29.1
D_PROXY_MIN = 3.0

SELECT_DIR = ROOT / "data" / "research" / "hedge_footprint" / "equity" / "select"

FUNNEL_KEYS = (
    "clusters", "pairs_equal_size", "both_legs_defined", "equity_class",
    "band_valid", "premium_25k", "dte_ge_1", "time_window", "release_fence",
    "prior_close", "price_min", "adv_min", "quote_good_both", "iv_fit_both",
    "final",
)

NETD_BINS = [0.0, 0.05, 0.10, 0.20, 0.30, 0.50, 0.70, 1.00, 2.00]
DPROXY_BINS = [0.0, 1.0, 3.0, 10.0, float("inf")]


def qualifying_sessions() -> list[date]:
    """The 3.2 code-provenance training sessions — read from its select ledgers
    (join_rate_dbn recorded per session; ledger presence == session qualified)."""
    out = []
    for p in sorted(SELECT_DIR.glob("*.json")):
        if p.name.endswith("_candidates.json"):
            continue
        led = json.loads(p.read_text())
        if led.get("join_rate_dbn", 0.0) >= JOIN_FLOOR:
            out.append(date.fromisoformat(p.stem))
    return out


def census_session(session: date, rc, pooled: dict) -> None:
    import databento as dbn

    defs = load_option_defs(session)
    tpath = TCBBO_DIR / f"{session.isoformat()}.dbn.zst"
    if defs is None or not tpath.exists():
        print(f"{session}: missing defs/tcbbo — skipped")
        return
    arr = dbn.DBNStore.from_file(tpath).to_ndarray()
    arr = arr[arr["size"] != UNDEF_SIZE]
    pid = arr["instrument_id"].astype(np.int64)
    idx = np.clip(np.searchsorted(defs.ids, pid), 0, len(defs.ids) - 1)
    hit = defs.ids[idx] == pid
    arr, idx = arr[hit], idx[hit]

    fx = fence_and_classify(arr)
    ts = arr["ts_event"].astype(np.int64)
    iid = arr["instrument_id"].astype(np.int64)
    size = arr["size"].astype(np.int64)
    _, cond, src, jr, _ = multileg_flags(session, ts, iid, fx.price, size)
    if src != "codes" or jr is None or jr < JOIN_FLOOR:
        print(f"{session}: not code-provenance ({src} {jr}) — skipped")
        return

    bid = arr["bid_px_00"].astype(np.int64)
    ask = arr["ask_px_00"].astype(np.int64)
    bidf = np.where((bid > 0) & (bid < np.iinfo(np.int64).max), bid / PX, np.nan)
    askf = np.where((ask > 0) & (ask < np.iinfo(np.int64).max), ask / PX, np.nan)

    ml = np.isin(cond, list(MULTILEG_CODES))
    und = defs.und_code[idx]
    order = np.lexsort((ts[ml], und[ml]))

    def col(a):
        return a[ml][order]

    px_usd = fx.price / PX          # Fenced.price is RAW fixed-point, not dollars
    m_ts, m_iid, m_und = col(ts), col(iid), col(und)
    m_price, m_size = col(px_usd), col(size)
    m_prem = col(fx.premium)
    m_bid, m_ask, m_good = col(bidf), col(askf), col(fx.good_q)
    m_def = col(idx)

    new = np.empty(len(m_ts), dtype=bool)
    if not len(m_ts):
        print(f"{session}: no multileg prints")
        return
    new[0] = True
    new[1:] = (m_und[1:] != m_und[:-1]) | ((m_ts[1:] - m_ts[:-1]) > W_NS)
    gid = np.cumsum(new) - 1
    n_g = int(gid[-1]) + 1

    # legs: (cluster, instrument), first row per leg = min-ts print
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
    fidx = np.flatnonzero(first)
    leg_bid, leg_ask = m_bid[o2][fidx], m_ask[o2][fidx]
    leg_good = m_good[o2][fidx]
    leg_ts = m_ts[o2][fidx]
    leg_def = m_def[o2][fidx]

    fun = dict.fromkeys(FUNNEL_KEYS, 0)
    fun["clusters"] = n_g
    legs_per = np.bincount(leg_gid, minlength=n_g)
    pair_g = np.flatnonzero(legs_per == 2)
    a_idx = np.flatnonzero(np.isin(leg_gid, pair_g))[0::2] \
        if len(pair_g) else np.empty(0, dtype=np.int64)
    b_idx = a_idx + 1
    eq = leg_sz[a_idx] == leg_sz[b_idx]
    A, B = a_idx[eq], b_idx[eq]
    fun["pairs_equal_size"] = len(A)
    if not len(A):
        _accumulate(pooled, fun, np.empty(0), np.empty(0), np.empty(0, dtype=bool),
                    np.empty(0, dtype="U16"), 0.0)
        return

    # A = lower instrument_id (probe convention; deterministic)
    swap = leg_def[A] > leg_def[B]  # def idx orders identically to iid within a session
    A2 = np.where(swap, B, A)
    B2 = np.where(swap, A, B)
    A, B = A2, B2

    ds = session.isoformat()
    roots = [str(s) for s in defs.roots]
    cls = np.array([rc.classify(r, ds) for r in roots])
    ident = np.array([rc.resolve(r, ds) for r in roots])
    keep = np.ones(len(A), dtype=bool)
    fun["both_legs_defined"] = int(keep.sum())     # defs join is upstream

    rootA = defs.und_code[leg_def[A]]
    keep &= cls[rootA] == "equity"
    fun["equity_class"] = int(keep.sum())

    lo = leg_bid[A] - leg_ask[B]
    hi = leg_ask[A] - leg_bid[B]
    width = hi - lo
    band_ok = np.isfinite(lo) & np.isfinite(hi) & (width > 0)
    keep &= band_ok
    fun["band_valid"] = int(keep.sum())

    with np.errstate(invalid="ignore", divide="ignore"):
        o_diff = (leg_vwap[A] - leg_vwap[B] - lo) / width
        o_sum = (leg_vwap[A] + leg_vwap[B] - (leg_bid[A] + leg_bid[B])) / width
    ed = np.minimum(o_diff, 1.0 - o_diff)
    es = np.minimum(o_sum, 1.0 - o_sum)
    spread_edge = (ed <= EDGE_C) & (es > EDGE_C)      # §3c orientation-confident

    pair_prem = leg_prem[A] + leg_prem[B]
    keep &= pair_prem >= PREMIUM_MIN
    fun["premium_25k"] = int(keep.sum())

    epoch_days = (session - date(1970, 1, 1)).days
    dteA = defs.exp_days[leg_def[A]] - epoch_days
    dteB = defs.exp_days[leg_def[B]] - epoch_days
    keep &= (dteA >= DTE_MIN) & (dteB >= DTE_MIN)
    fun["dte_ge_1"] = int(keep.sum())

    t0 = np.minimum(leg_ts[A], leg_ts[B])
    sec = et_second_of_day(t0, session_midnight_ns(session))
    keep &= in_trading_window(sec)
    fun["time_window"] = int(keep.sum())
    keep &= ~in_release_fence(sec)
    fun["release_fence"] = int(keep.sum())

    closes = bars_before_date(ds, db_path=DEFAULT_DB_PATH)
    adv_map = adv_before_date(session, db_path=DEFAULT_DB_PATH)
    sym = ident[rootA]
    prior_close = np.array([closes.get(str(s), (np.nan, ""))[0] for s in sym])
    adv = np.array([adv_map.get(str(s), np.nan) for s in sym])
    keep &= np.isfinite(prior_close)
    fun["prior_close"] = int(keep.sum())
    keep &= prior_close >= PRICE_MIN
    fun["price_min"] = int(keep.sum())
    keep &= np.isfinite(adv) & (adv >= ADV_MIN)
    fun["adv_min"] = int(keep.sum())

    keep &= leg_good[A] & leg_good[B]
    fun["quote_good_both"] = int(keep.sum())

    # per-leg IV/delta at prior-close spot (Δ_select analog; census only)
    rows = np.flatnonzero(keep)
    net_d = np.full(len(A), np.nan)
    for i in rows:
        rec = []
        for leg in (A[i], B[i]):
            d = leg_def[leg]
            t_years = time_to_expiry_years(int(leg_ts[leg]), int(defs.exp_days[d]))
            if t_years <= 0.0:
                break
            mid = (leg_bid[leg] + leg_ask[leg]) / 2.0
            fit = fit_delta(float(mid), float(prior_close[i]),
                            float(defs.strike[d]), t_years, bool(defs.is_put[d]))
            if not fit.ok:
                break
            rec.append(fit.delta)
        if len(rec) == 2:
            net_d[i] = rec[0] - rec[1]      # hypothesis o=1: buy A / sell B
    keep &= np.isfinite(net_d)
    fun["iv_fit_both"] = int(keep.sum())
    fun["final"] = int(keep.sum())

    n_pair = leg_sz[A]
    h_pred = net_d * n_pair * 100.0
    d_proxy = np.abs(h_pred) / (adv * EXPECTED_WINDOW_S / RTH_S)

    _accumulate(pooled, fun, np.abs(net_d[keep]), d_proxy[keep],
                spread_edge[keep], sym[keep], float(pair_prem[keep].sum()))
    n_conf = int((spread_edge[keep]).sum())
    n_det = int((d_proxy[keep] >= D_PROXY_MIN).sum())
    n_both = int((spread_edge[keep] & (d_proxy[keep] >= D_PROXY_MIN)).sum())
    print(f"{session}: clusters {n_g:,} -> eq-pairs {fun['pairs_equal_size']:,} "
          f"-> final {fun['final']:,}  o-confident {n_conf}  "
          f"D*>=3 {n_det}  BOTH {n_both}")


def _accumulate(pooled, fun, netd, dproxy, conf, syms, prem):
    f = pooled.setdefault("funnel", dict.fromkeys(FUNNEL_KEYS, 0))
    for k in FUNNEL_KEYS:
        f[k] += fun[k]
    pooled.setdefault("netd", []).append(netd)
    pooled.setdefault("dproxy", []).append(dproxy)
    pooled.setdefault("conf", []).append(conf)
    pooled.setdefault("syms", []).append(syms)
    pooled["prem"] = pooled.get("prem", 0.0) + prem
    pooled["sessions"] = pooled.get("sessions", 0) + 1


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--every", type=int, default=9,
                    help="sample every Nth qualifying session")
    args = ap.parse_args()

    rc = load_classification(ROOT / "signals" / "options_flow" / "root_classification.json")
    sessions = qualifying_sessions()
    sample = sessions[:: args.every]
    print(f"{len(sessions)} code-provenance training sessions; "
          f"census on {len(sample)} (every {args.every}th)\n")

    pooled: dict = {}
    for s in sample:
        census_session(s, rc, pooled)

    if not pooled.get("sessions"):
        print("no sessions ran")
        return
    fun = pooled["funnel"]
    netd = np.concatenate(pooled["netd"])
    dproxy = np.concatenate(pooled["dproxy"])
    conf = np.concatenate(pooled["conf"])
    syms = np.concatenate(pooled["syms"])
    n_s = pooled["sessions"]
    scale = len(sessions) / n_s

    print(f"\n=== POOLED over {n_s} sessions ===")
    print("funnel:", "  ".join(f"{k} {fun[k]:,}" for k in FUNNEL_KEYS))
    print(f"final pair premium ${pooled['prem'] / 1e6:.1f}M")
    hist, _ = np.histogram(netd, bins=NETD_BINS)
    print("|netΔ| per contract:",
          "  ".join(f"{NETD_BINS[i]:.2f}-{NETD_BINS[i + 1]:.2f}: {h}"
                    for i, h in enumerate(hist)))
    dh, _ = np.histogram(dproxy, bins=DPROXY_BINS)
    print("D_proxy (ADV pro-rata — OVERSTATES real detectability):",
          "  ".join(f"{DPROXY_BINS[i]:g}-{DPROXY_BINS[i + 1]:g}: {h}"
                    for i, h in enumerate(dh)))
    det = dproxy >= D_PROXY_MIN
    both = conf & det
    print(f"o-confident (spread-edge@{EDGE_C}): {int(conf.sum())}   "
          f"D_proxy>=3: {int(det.sum())}   INTERSECTION: {int(both.sum())}  "
          f"({len(np.unique(syms[both]))} distinct underlyings)")
    print(f"projection x{scale:.1f} to all {len(sessions)} sessions: "
          f"final ~{fun['final'] * scale:,.0f}, o-confident ~{conf.sum() * scale:,.0f}, "
          f"intersection ~{both.sum() * scale:,.0f}")
    print("\nCensus only — informs the 3.4 spec's floors. No equity fetches, "
          "no outcomes, no verdict authority.")


if __name__ == "__main__":
    main()
