"""What the three registered-but-unbuilt prereg clauses would actually change.

`FLOW_BASELINE_PREREG_2026-07-26.md:60-61` registers two print-quality rules that
`tools/flow_corpus_rollup.py` never implemented (the 2026-07-29 audit, Finding 4):

    1. drop prints whose spread > 50% of mid
    2. drop a whole name-day if > 20% of its prints were dropped

Both live at the INGEST layer, so building them means re-rolling the full year and
discarding the Addendum B base-rate table. That is too expensive to spend on a
guess, so this measures the consequence first and the founder decides against a
number (founder call 2026-07-29: "measure first, then decide").

The decisive quantity is NOT how many prints get dropped — it is how far the
registered tilt MOVES when they are. A clause that removes 3% of prints but never
changes a name-day's sign is cosmetic; one that removes 0.5% and flips signs is
load-bearing. So this reports |delta tilt| and the sign-flip rate, alongside the
raw drop shares.

Clause 3 (subtract the whole-market daily mean) is NOT measured here: it is a
statistic-level transform needing no re-roll, so it can be applied or retired
without a corpus rebuild and is not what this decision is gated on.

  python tools/prereg_clause_impact.py                 # default sample
  python tools/prereg_clause_impact.py --sessions 2026-07-23 2025-08-15
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tools.flow_corpus_rollup import (  # noqa: E402
    PX,
    TCBBO_DIR,
    UNDEF,
    _load_definitions,
)
from tools.order_grain_null import UNDEF_SIZE  # noqa: E402

SPREAD_MAX = 0.50         # prereg:60 — drop when (ask - bid) / mid exceeds this
NAMEDAY_DROP_MAX = 0.20   # prereg:61 — drop the name-day above this dropped share
# Spread across the year AND both sides of the 2026-06-26 sentinel regime break.
DEFAULT_SESSIONS = ("2025-08-15", "2025-11-14", "2026-02-13",
                    "2026-05-15", "2026-06-30", "2026-07-23")

logger = logging.getLogger("prereg_clause_impact")


def _session(session: date) -> dict | None:
    defs = _load_definitions(session)
    if defs is None:
        logger.warning("%s: no definitions — skipped", session)
        return None
    d_ids, d_und, uniq_und, d_right, _strike, _dte = defs

    import databento as dbn

    arr = dbn.DBNStore.from_file(TCBBO_DIR / f"{session.isoformat()}.dbn.zst").to_ndarray()
    arr = arr[arr["size"] != UNDEF_SIZE]
    pid = arr["instrument_id"].astype(np.int64)
    idx = np.searchsorted(d_ids, pid)
    np.clip(idx, 0, len(d_ids) - 1, out=idx)
    hit = d_ids[idx] == pid
    arr, idx = arr[hit], idx[hit]
    if not len(arr):
        return None
    und = d_und[idx]
    right = d_right[idx]            # 0 = call, 1 = put

    price = arr["price"].astype(np.float64)
    size = arr["size"].astype(np.float64)
    bid = arr["bid_px_00"].astype(np.float64)
    ask = arr["ask_px_00"].astype(np.float64)
    good_q = ((arr["bid_px_00"] != UNDEF) & (arr["ask_px_00"] != UNDEF)
              & (bid > 0) & (ask > 0) & (ask > bid))
    mid = np.where(good_q, (bid + ask) / 2.0, np.nan)
    with np.errstate(invalid="ignore", divide="ignore"):
        spread_ratio = np.where(good_q, (ask - bid) / mid, np.nan)

    wide = good_q & (spread_ratio > SPREAD_MAX)
    classified = good_q & (price != mid)
    premium = price / PX * size * 100.0

    # bull = call_buy + put_sell; bear = call_sell + put_buy  (prereg:68-72)
    buy = classified & (price > mid)
    is_bull = (buy & (right == 0)) | (~buy & (right == 1))

    n_names = int(und.max()) + 1

    def _tilt(mask):
        b = np.bincount(und[mask & is_bull], weights=premium[mask & is_bull], minlength=n_names)
        r = np.bincount(und[mask & ~is_bull], weights=premium[mask & ~is_bull], minlength=n_names)
        den = b + r
        with np.errstate(invalid="ignore", divide="ignore"):
            return np.where(den > 0, (b - r) / np.maximum(den, 1e-30), np.nan), den

    t_all, den_all = _tilt(classified)
    t_clean, _ = _tilt(classified & ~wide)

    # Clause 2's denominator is the name-day's own prints; "dropped" is everything
    # the prereg says to discard -- bad quotes AND wide spreads.
    tot_p = np.bincount(und, minlength=n_names).astype(float)
    drop_p = np.bincount(und[~good_q | wide], minlength=n_names).astype(float)
    with np.errstate(invalid="ignore", divide="ignore"):
        drop_share = np.where(tot_p > 0, drop_p / np.maximum(tot_p, 1), np.nan)

    return {"session": session, "names": uniq_und, "t_all": t_all, "t_clean": t_clean,
            "den": den_all, "drop_share": drop_share, "tot_p": tot_p,
            "prem_wide": float(premium[classified & wide].sum()),
            "prem_cls": float(premium[classified].sum()),
            "n_wide": int(wide.sum()), "n_cls": int(classified.sum()),
            "n_prints": int(len(arr))}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sessions", nargs="*", default=list(DEFAULT_SESSIONS))
    ap.add_argument("--floor", type=float, default=250_000.0,
                    help="premium floor for the tilt-movement rows (registered $250k)")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    rows = [r for r in (_session(date.fromisoformat(s)) for s in args.sessions) if r]
    if not rows:
        print("no sessions measured")
        return

    print(f"\n=== clause 1: prints with spread > {SPREAD_MAX:.0%} of mid ===")
    print("  session       prints    classified    wide     wide%   wide premium%")
    for r in rows:
        wp = r["prem_wide"] / r["prem_cls"] * 100 if r["prem_cls"] else 0.0
        print(f"  {r['session']}  {r['n_prints']:>11,}  {r['n_cls']:>11,}  "
              f"{r['n_wide']:>9,}  {r['n_wide']/max(r['n_cls'],1):>6.2%}  {wp:>12.3f}%")

    print(f"\n=== the number that decides it: does the tilt MOVE? (>= ${args.floor:,.0f}) ===")
    print("  session       name-days   median|dt|    p95|dt|   >0.05    sign flips")
    for r in rows:
        k = (r["den"] >= args.floor) & np.isfinite(r["t_all"]) & np.isfinite(r["t_clean"])
        if not k.sum():
            continue
        dt = np.abs(r["t_all"][k] - r["t_clean"][k])
        flip = np.sign(r["t_all"][k]) != np.sign(r["t_clean"][k])
        print(f"  {r['session']}  {k.sum():>10,}   {np.median(dt):>9.5f}  "
              f"{np.percentile(dt,95):>9.5f}  {(dt>0.05).mean():>6.2%}  {flip.mean():>10.2%}")

    print(f"\n=== clause 2: name-days dropping > {NAMEDAY_DROP_MAX:.0%} of prints ===")
    print("  session      name-days   would drop    share   of those, premium share")
    for r in rows:
        k = r["tot_p"] > 0
        over = k & (r["drop_share"] > NAMEDAY_DROP_MAX)
        prem = r["den"][over].sum() / max(r["den"][k].sum(), 1e-30) * 100
        print(f"  {r['session']}  {int(k.sum()):>10,}  {int(over.sum()):>11,}  "
              f"{over.sum()/max(k.sum(),1):>7.2%}  {prem:>21.3f}%")


if __name__ == "__main__":
    main()
