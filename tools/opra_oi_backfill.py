"""Backfill whole-market OPRA open interest (roadmap 2.2), condense-on-arrival, resumable.

statistics is L0-tier — every pull quotes $0.00 with no rolling window (measured
2026-07-28: one day AND the full corpus window both $0.00) — so unlike tcbbo the
raw file is always re-obtainable free. Raw is therefore condensed to OI-only and
DELETED per day (678 MB/day raw would be ~170 GB over the corpus; the condensed
npz is ~10-20 MB/day). Deny-by-default on spend anyway: every day's cost is
quoted immediately before download and anything above --max-cost aborts the run
(Databento batch submit has no funds pre-check; 2026-07-19: ~$43 billed past
credit).

Payload facts (measured on 2026-07-24, whole market): stat_type=9 = open
interest, 34.3M records / 1.98M instruments (~17 publishers each — OI is
OCC-cleared so publishers should agree; --smoke measures instead of assuming);
quantity==0 published explicitly (40.7% of records), so day-over-day deltas see
zero crossings; OI ts_event lands 06:30-08:44 ET pre-open ⇒ day-D's value is
D-1's close positions (T+1: backtests may only read it next-morning).

Output: data/databento/opra_oi/YYYY-MM-DD.npz — iid (uint32, sorted), oi
(uint32), one row per instrument (last ts_event wins). Empty sessions
(holidays) write an empty npz so re-runs skip them.

  python tools/opra_oi_backfill.py --start 2025-07-28 --end 2026-07-25
  python tools/opra_oi_backfill.py --smoke   # condense data/databento/stats_probe_20260724.dbn.zst only
"""

from __future__ import annotations

import argparse
import logging
import os
import time
from datetime import date, timedelta
from pathlib import Path

import databento as db
import numpy as np
from dotenv import load_dotenv

DATASET = "OPRA.PILLAR"
SCHEMA = "statistics"
STAT_OPEN_INTEREST = 9
CHUNK = 5_000_000  # full-day to_ndarray() is ~16 GB; chunked stays under ~2 GB
DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "databento"
OUT_DIR = DATA_DIR / "opra_oi"
SMOKE_FILE = DATA_DIR / "stats_probe_20260724.dbn.zst"

logger = logging.getLogger("opra_oi_backfill")


def _sessions(start: date, end: date) -> list[date]:
    """Weekdays in [start, end). Holidays are not filtered — they come back empty
    and are recorded as such rather than guessed at from a calendar."""
    out, d = [], start
    while d < end:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def _condense(path: Path) -> tuple[np.ndarray, np.ndarray, int, int]:
    """DBN statistics file -> (iid sorted, oi) one row per instrument, last
    ts_event wins across publishers. Returns (iid, oi, n_oi_records, n_disagree)
    where n_disagree = instruments whose publishers reported different values."""
    store = db.DBNStore.from_file(path)
    iids, tss, qtys = [], [], []
    for chunk in store.to_ndarray(count=CHUNK):
        m = chunk["stat_type"] == STAT_OPEN_INTEREST
        if m.any():
            iids.append(chunk["instrument_id"][m])
            tss.append(chunk["ts_event"][m])
            qtys.append(chunk["quantity"][m].astype(np.int64))
    if not iids:
        return np.empty(0, np.uint32), np.empty(0, np.uint32), 0, 0
    iid = np.concatenate(iids)
    ts = np.concatenate(tss)
    qty = np.concatenate(qtys)
    order = np.lexsort((ts, iid))
    iid, ts, qty = iid[order], ts[order], qty[order]
    last = np.r_[iid[1:] != iid[:-1], True]
    first = np.r_[True, iid[1:] != iid[:-1]]
    disagree = int(
        (np.maximum.reduceat(qty, np.flatnonzero(first))
         != np.minimum.reduceat(qty, np.flatnonzero(first))).sum()
    ) if len(iid) else 0
    return (iid[last].astype(np.uint32), qty[last].astype(np.uint32),
            len(qty), disagree)


def _process_day(client: db.Historical, d: date, max_cost: float) -> None:
    out = OUT_DIR / f"{d.isoformat()}.npz"
    if out.exists():
        return
    day, nxt = d.isoformat(), (d + timedelta(days=1)).isoformat()
    cost = client.metadata.get_cost(
        dataset=DATASET, schema=SCHEMA, symbols="ALL_SYMBOLS", start=day, end=nxt)
    if cost > max_cost:
        raise RuntimeError(f"{day}: quoted ${cost:.2f} > --max-cost {max_cost:.2f}; aborting run")
    raw = OUT_DIR / f"{d.isoformat()}.dbn.zst.part"
    t0 = time.time()
    client.timeseries.get_range(
        dataset=DATASET, schema=SCHEMA, symbols="ALL_SYMBOLS",
        start=day, end=nxt, path=str(raw))
    t_dl = time.time() - t0
    try:
        iid, oi, n_rec, n_dis = _condense(raw)
        # ends in .npz on purpose — savez_compressed silently appends .npz otherwise
        tmp = out.with_name(f".{out.name}.part.npz")
        np.savez_compressed(tmp, iid=iid, oi=oi)
        os.replace(tmp, out)
    finally:
        raw.unlink(missing_ok=True)
    logger.info(
        "%s: %s instruments (%s OI records, %s cross-publisher disagreements) "
        "dl %.0fs total %.0fs", day, f"{len(iid):,}", f"{n_rec:,}", f"{n_dis:,}",
        t_dl, time.time() - t0)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", type=date.fromisoformat, default=date(2025, 7, 28))
    ap.add_argument("--end", type=date.fromisoformat, default=date(2026, 7, 25))
    ap.add_argument("--max-cost", type=float, default=0.0)
    ap.add_argument("--smoke", action="store_true",
                    help="condense the already-downloaded probe file, print, exit")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")

    if args.smoke:
        iid, oi, n_rec, n_dis = _condense(SMOKE_FILE)
        nz = oi > 0
        print(f"instruments: {len(iid):,}  OI records: {n_rec:,}")
        print(f"cross-publisher disagreements: {n_dis:,} "
              f"({n_dis / max(len(iid), 1):.5f} of instruments)")
        print(f"zero-OI instruments: {(~nz).sum():,}  total OI: {oi[nz].sum():,}")
        return

    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
    client = db.Historical(os.environ["DATABENTO_API_KEY"])
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    days = [d for d in _sessions(args.start, args.end)
            if not (OUT_DIR / f"{d.isoformat()}.npz").exists()]
    logger.info("%d sessions to fetch", len(days))
    for d in days:
        _process_day(client, d, args.max_cost)
    logger.info("done")


if __name__ == "__main__":
    main()
