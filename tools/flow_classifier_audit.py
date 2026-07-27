"""Audit the corpus direction classifier against real prints, and test whether a
deeper schema could resolve sweeps per-leg.

Reproduces tools/flow_corpus_rollup.py's classify block EXACTLY (same good_q, same
midpoint/strict rules, same u) so the numbers here are the numbers the corpus stores,
not a re-derivation that might differ.

A  classification rates    verify the claimed ~89%, and split out precisely who is
                           unclassified: unquoted, locked, crossed, non-positive, or
                           printed exactly at the midpoint.

B  u contamination         rollup line 158 does nan_to_num(u, nan=0.0). u=0.0 is the
                           value meaning "printed at the midpoint", so every bad-quote
                           print is folded into the at-mid bucket. u_prem is a
                           premium-weighted SUM, so this dilutes any mean taken against
                           total premium toward "no direction". Measure the share.

C  u tail                  u is unbounded. Prints outside the quote can carry large |u|
                           and dominate a premium-weighted sum. Measure how much of
                           |u_prem| comes from |u| > 2.

D  sweep resolution        Would cmbp-1 buy per-leg pairing? Only if the legs of a
                           same-ts_event group are actually disseminated at distinct
                           times. If ts_recv is identical too, the exchange reported
                           them as one batch and no intervening quote exists to find --
                           the single snapshot is OPRA's granularity, not a Databento
                           product gap.
"""

from __future__ import annotations

import argparse

import databento as db
import numpy as np

UNDEF = 9223372036854775807
PX = 1e9
CHUNK = 1_000_000


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("path")
    p.add_argument("--defs", required=True,
                   help="same-session definition file; the join is applied exactly as "
                        "rollup_session does, so these are the numbers the corpus stores")
    p.add_argument("--chunk", type=int, default=CHUNK)
    args = p.parse_args()

    # _load_definitions keeps ONLY calls and puts. Everything else -- notably
    # instrument_class 'K' (index) -- never reaches d_ids, so its prints are dropped
    # by the join below rather than classified. Auditing pre-join measures records the
    # rollup never sees.
    kept = []
    for a in db.DBNStore.from_file(args.defs).to_ndarray(count=500_000):
        c = a["instrument_class"]
        kept.append(a["instrument_id"][(c == b"C") | (c == b"P")])
    d_ids = np.unique(np.concatenate(kept)).astype(np.int64)

    st = db.DBNStore.from_file(args.path)
    n_raw = 0
    n_dropped = 0

    n = 0
    n_goodq = 0
    unquoted = nonpos = locked = crossed = 0
    buy = sell = at_mid = 0
    s_buy = s_sell = s_none = 0
    prem_all = prem_goodq = prem_bad = 0.0
    uprem_total = 0.0
    uprem_tail = 0.0
    n_tail = 0
    umin, umax = 0.0, 0.0

    grp_n = 0
    grp_same_recv = 0
    grp_multi_pub = 0
    grp_recv_spread_ns: list[int] = []
    tail = None

    for arr in st.to_ndarray(count=args.chunk):
        n_raw += len(arr)
        pid = arr["instrument_id"].astype(np.int64)
        j = np.searchsorted(d_ids, pid)
        np.clip(j, 0, len(d_ids) - 1, out=j)
        hit = d_ids[j] == pid
        n_dropped += int((~hit).sum())
        arr = arr[hit]
        if not len(arr):
            continue

        n += len(arr)
        price = arr["price"].astype(np.float64)
        size = arr["size"].astype(np.float64)
        bid = arr["bid_px_00"].astype(np.float64)
        ask = arr["ask_px_00"].astype(np.float64)

        # --- identical to rollup lines 151-166 ---
        good_q = (arr["bid_px_00"] != UNDEF) & (arr["ask_px_00"] != UNDEF) \
            & (bid > 0) & (ask > 0) & (ask > bid)
        mid = np.where(good_q, (bid + ask) / 2.0, np.nan)
        half = np.where(good_q, (ask - bid) / 2.0, np.nan)
        with np.errstate(invalid="ignore", divide="ignore"):
            u = (price - mid) / half
        u = np.nan_to_num(u, nan=0.0, posinf=0.0, neginf=0.0)
        premium = price / PX * size * 100.0
        # ----------------------------------------

        n_goodq += int(good_q.sum())
        undef = (arr["bid_px_00"] == UNDEF) | (arr["ask_px_00"] == UNDEF)
        unquoted += int(undef.sum())
        rest = ~undef
        nonpos += int((rest & ((bid <= 0) | (ask <= 0))).sum())
        locked += int((rest & (bid > 0) & (ask > 0) & (ask == bid)).sum())
        crossed += int((rest & (bid > 0) & (ask > 0) & (ask < bid)).sum())

        buy += int((good_q & (price > mid)).sum())
        sell += int((good_q & (price < mid)).sum())
        at_mid += int((good_q & (price == mid)).sum())
        s_buy += int((good_q & (price >= ask)).sum())
        s_sell += int((good_q & (price <= bid)).sum())
        s_none += int((good_q & (price < ask) & (price > bid)).sum())

        prem_all += float(premium.sum())
        prem_goodq += float(premium[good_q].sum())
        prem_bad += float(premium[~good_q].sum())

        up = u * premium
        uprem_total += float(up.sum())
        big = np.abs(u) > 2.0
        uprem_tail += float(up[big].sum())
        n_tail += int(big.sum())
        if len(u):
            umin = min(umin, float(u.min()))
            umax = max(umax, float(u.max()))

        # --- D: sweep groups ---
        sub = arr[["instrument_id", "ts_event", "ts_recv", "publisher_id"]]
        if tail is not None:
            sub = np.concatenate([tail, sub])
        ki, kt = sub["instrument_id"], sub["ts_event"]
        newg = np.empty(len(sub), dtype=bool)
        newg[0] = True
        newg[1:] = (ki[1:] != ki[:-1]) | (kt[1:] != kt[:-1])
        starts = np.flatnonzero(newg)
        bounds = list(starts) + [len(sub)]
        for s, e in zip(bounds[:-2], bounds[1:-1]):
            if e - s < 2:
                continue
            grp_n += 1
            rv = sub["ts_recv"][s:e].astype(np.int64)
            if (rv == rv[0]).all():
                grp_same_recv += 1
            else:
                grp_recv_spread_ns.append(int(rv.max() - rv.min()))
            if len(np.unique(sub["publisher_id"][s:e])) > 1:
                grp_multi_pub += 1
        tail = sub[bounds[-2]:].copy()

    def pc(x, d):
        return f"{100.0*x/d:6.2f}%" if d else "   n/a"

    print(f"file   {args.path}")
    print(f"raw prints          {n_raw:,}")
    print(f"dropped by join     {n_dropped:,}  {pc(n_dropped, n_raw)}  (non-option instruments)")
    print(f"classified over     {n:,}\n")

    print("A  classification (rollup's own good_q / midpoint / strict rules)")
    print(f"  good_q (classifiable)  {n_goodq:>12,}  {pc(n_goodq, n)}")
    print(f"    -> buy  (px > mid)   {buy:>12,}  {pc(buy, n)} of all")
    print(f"    -> sell (px < mid)   {sell:>12,}  {pc(sell, n)} of all")
    print(f"    -> SIDE CLASSIFIED   {buy+sell:>12,}  {pc(buy+sell, n)} of all   <-- vs claimed ~89%")
    print(f"    -> at mid, unsigned  {at_mid:>12,}  {pc(at_mid, n)}")
    print("  excluded from good_q:")
    print(f"    unquoted (UNDEF)     {unquoted:>12,}  {pc(unquoted, n)}")
    print(f"    non-positive px      {nonpos:>12,}  {pc(nonpos, n)}")
    print(f"    locked (ask==bid)    {locked:>12,}  {pc(locked, n)}")
    print(f"    crossed (ask<bid)    {crossed:>12,}  {pc(crossed, n)}")
    print("  strict (touch) rule:")
    print(f"    buy  px>=ask         {s_buy:>12,}  {pc(s_buy, n)}")
    print(f"    sell px<=bid         {s_sell:>12,}  {pc(s_sell, n)}")
    print(f"    inside, unsigned     {s_none:>12,}  {pc(s_none, n)}")
    print()

    print("B  u contamination from nan_to_num(u, nan=0.0)  [u=0.0 == 'printed at mid']")
    print(f"  premium, all prints    {prem_all:>18,.0f}")
    print(f"  premium, good_q        {prem_goodq:>18,.0f}  {pc(prem_goodq, prem_all)}")
    print(f"  premium, BAD quote     {prem_bad:>18,.0f}  {pc(prem_bad, prem_all)}"
          "   <-- forced to u=0, dilutes any mean taken vs total premium")
    print()

    print("C  u tail (u is unbounded; u_prem is a premium-weighted SUM)")
    print(f"  u range                [{umin:.2f}, {umax:.2f}]")
    print(f"  prints with |u| > 2    {n_tail:>12,}  {pc(n_tail, n)}")
    print(f"  sum(u*prem) total      {uprem_total:>18,.0f}")
    print(f"  sum(u*prem) |u|>2      {uprem_tail:>18,.0f}"
          f"   {pc(abs(uprem_tail), abs(uprem_total)) if uprem_total else 'n/a'} of |total|")
    print()

    print("D  could cmbp-1 pair sweeps per-leg?  (groups = same instrument_id + ts_event)")
    print(f"  multi-print groups     {grp_n:>12,}")
    print(f"  ts_recv IDENTICAL too  {grp_same_recv:>12,}  {pc(grp_same_recv, grp_n)}"
          "   <-- one batch; no intervening quote exists to buy")
    d = len(grp_recv_spread_ns)
    print(f"  ts_recv DIFFERS        {d:>12,}  {pc(d, grp_n)}")
    if d:
        a = np.array(grp_recv_spread_ns, dtype=np.int64)
        print(f"    spread ns  p50={np.percentile(a,50):,.0f}  p90={np.percentile(a,90):,.0f}"
              f"  max={a.max():,}")
    print(f"  multi-publisher groups {grp_multi_pub:>12,}  {pc(grp_multi_pub, grp_n)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
