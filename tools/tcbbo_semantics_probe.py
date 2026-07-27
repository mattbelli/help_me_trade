"""Is the BBO attached to a tcbbo print the book state BEFORE or AFTER the trade?

The corpus's whole direction signal is NBBO-at-print, so this is load-bearing: if the
quote is post-trade, every aggressor classification is reading a book that already
absorbed the print, and "at the ask" stops meaning "buyer lifted the offer".

Read-only. Chunked, so peak RSS stays flat regardless of session size.

Three tests, none of which relies on the vendor's prose:

  T1 touch mass       Pre-trade BBO puts hard mass at price == ask (buyer lifts the
                      displayed offer) and price == bid. A post-trade book has already
                      moved, so exact equality would be rare and prints would sit
                      outside the quote far more often.

  T2 full consumption price == ask AND size >= ask_sz. Under a post-trade BBO this is
                      near-contradictory: if the print consumed the whole displayed
                      offer, an after-the-fact book cannot still be showing it at that
                      price with that size.

  T3 sweep progression Consecutive prints on ONE instrument at the SAME ts_event walking
                      the book. If the quote is pre-trade and refreshed per record, the
                      ask should climb across a buy sweep. Identical quotes across the
                      group mean one snapshot taken before the whole sweep -- still
                      "before", but coarser, which matters for classifying sweeps.
"""

from __future__ import annotations

import argparse
import collections

import databento as db
import numpy as np

UNDEF = 9223372036854775807
CHUNK = 1_000_000


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("path")
    p.add_argument("--chunk", type=int, default=CHUNK)
    args = p.parse_args()

    st = db.DBNStore.from_file(args.path)

    n = 0
    quoted = 0
    at_ask = at_bid = 0
    above_ask = below_bid = 0
    inside = 0
    locked = crossed = 0
    ask_full = ask_over = 0
    bid_full = bid_over = 0
    side_ct: collections.Counter = collections.Counter()

    # T3 carries the previous chunk's tail so groups spanning a chunk edge survive.
    grp_same = grp_diff = grp_total = 0
    tail = None

    for arr in st.to_ndarray(count=args.chunk):
        n += len(arr)
        for k, v in zip(*np.unique(arr["side"], return_counts=True)):
            side_ct[k.decode() if isinstance(k, bytes) else str(k)] += int(v)

        b = arr["bid_px_00"]
        a = arr["ask_px_00"]
        px = arr["price"]
        sz = arr["size"]
        bs = arr["bid_sz_00"]
        asz = arr["ask_sz_00"]

        ok = (b != UNDEF) & (a != UNDEF)
        quoted += int(ok.sum())

        at_ask += int((ok & (px == a)).sum())
        at_bid += int((ok & (px == b)).sum())
        above_ask += int((ok & (px > a)).sum())
        below_bid += int((ok & (px < b)).sum())
        inside += int((ok & (px > b) & (px < a)).sum())
        locked += int((ok & (a == b)).sum())
        crossed += int((ok & (a < b)).sum())

        hit_a = ok & (px == a)
        ask_full += int((hit_a & (sz >= asz)).sum())
        ask_over += int((hit_a & (sz > asz)).sum())
        hit_b = ok & (px == b)
        bid_full += int((hit_b & (sz >= bs)).sum())
        bid_over += int((hit_b & (sz > bs)).sum())

        # T3: same instrument_id + same ts_event, more than one print.
        sub = arr[["instrument_id", "ts_event", "bid_px_00", "ask_px_00"]]
        if tail is not None:
            sub = np.concatenate([tail, sub])
        key_i = sub["instrument_id"]
        key_t = sub["ts_event"]
        # records arrive time-ordered, so a group is a contiguous run
        newgrp = np.empty(len(sub), dtype=bool)
        newgrp[0] = True
        newgrp[1:] = (key_i[1:] != key_i[:-1]) | (key_t[1:] != key_t[:-1])
        starts = np.flatnonzero(newgrp)
        bounds = list(starts) + [len(sub)]
        # hold back the final group; it may continue into the next chunk
        for s, e in zip(bounds[:-2], bounds[1:-1]):
            if e - s < 2:
                continue
            grp_total += 1
            qb = sub["bid_px_00"][s:e]
            qa = sub["ask_px_00"][s:e]
            if (qb == qb[0]).all() and (qa == qa[0]).all():
                grp_same += 1
            else:
                grp_diff += 1
        tail = sub[bounds[-2]:].copy()

    def pct(x: int, d: int) -> str:
        return f"{100.0 * x / d:6.2f}%" if d else "   n/a"

    print(f"file                {args.path}")
    print(f"prints              {n:,}")
    print(f"both sides quoted   {quoted:,}  ({pct(quoted, n)})")
    print(f"side field          {dict(side_ct)}")
    print()
    print("T1  where the print sits vs its own attached quote (of quoted prints)")
    print(f"  price == ask      {at_ask:>12,}  {pct(at_ask, quoted)}")
    print(f"  price == bid      {at_bid:>12,}  {pct(at_bid, quoted)}")
    print(f"  strictly inside   {inside:>12,}  {pct(inside, quoted)}")
    print(f"  ABOVE ask         {above_ask:>12,}  {pct(above_ask, quoted)}")
    print(f"  BELOW bid         {below_bid:>12,}  {pct(below_bid, quoted)}")
    print(f"  locked (a==b)     {locked:>12,}  {pct(locked, quoted)}")
    print(f"  crossed (a<b)     {crossed:>12,}  {pct(crossed, quoted)}")
    print(f"  at-touch total    {at_ask + at_bid:>12,}  {pct(at_ask + at_bid, quoted)}")
    print()
    print("T2  did the print consume the displayed size it traded against?")
    print(f"  at ask, sz>=asksz {ask_full:>12,}  {pct(ask_full, at_ask)} of at-ask")
    print(f"  at ask, sz> asksz {ask_over:>12,}  {pct(ask_over, at_ask)} of at-ask")
    print(f"  at bid, sz>=bidsz {bid_full:>12,}  {pct(bid_full, at_bid)} of at-bid")
    print(f"  at bid, sz> bidsz {bid_over:>12,}  {pct(bid_over, at_bid)} of at-bid")
    print()
    print("T3  multi-print groups (same instrument_id, same ts_event)")
    print(f"  groups >=2 prints {grp_total:>12,}")
    print(f"  identical quote   {grp_same:>12,}  {pct(grp_same, grp_total)}")
    print(f"  quote DIFFERS     {grp_diff:>12,}  {pct(grp_diff, grp_total)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
