"""Backfill whole-market OPRA tcbbo, one session per file, resumable.

The free L1 window is exactly 12 months and rolls daily (measured 2026-07-26:
12mo = $0.00, 18mo = $14,964.49), so days not on disk before they age out become
a purchase. This runs the window down and is safe to re-run — existing files are
skipped.

Deny-by-default on spend: every day's cost is quoted immediately before its
download and anything above --max-cost aborts the run. Databento's batch submit
has no funds pre-check (2026-07-19: ~$43 billed past credit), so the check is the
only guard that exists.

  python tools/opra_tcbbo_backfill.py --start 2025-07-28 --end 2026-07-24
"""

from __future__ import annotations

import argparse
import logging
import os
import time
from datetime import date, timedelta
from pathlib import Path

import databento as db
from dotenv import load_dotenv

DATASET = "OPRA.PILLAR"
RTH = ("T13:30", "T20:00")  # UTC; OPRA regular session
DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "databento"
# tcbbo carries the prints; definition resolves instrument_id → underlying/strike/
# expiry/right. ALL_SYMBOLS files ship no symbology mappings, so the definition
# pull is not optional — without it a print cannot be attributed to a name.
OUT_DIRS = {"tcbbo": DATA_DIR / "opra_tcbbo", "definition": DATA_DIR / "opra_defs"}

logger = logging.getLogger("opra_tcbbo_backfill")


def _sessions(start: date, end: date) -> list[date]:
    """Weekdays in [start, end). Holidays are not filtered — they come back empty
    and are recorded as such rather than guessed at from a calendar."""
    out, d = [], start
    while d < end:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--start", required=True, help="first session, YYYY-MM-DD")
    p.add_argument("--end", required=True, help="exclusive end, YYYY-MM-DD")
    p.add_argument("--max-cost", type=float, default=0.0,
                   help="per-day ceiling in USD; default 0.0 = subscription-covered only")
    p.add_argument("--schema", default="tcbbo", choices=sorted(OUT_DIRS),
                   help="tcbbo = the prints; definition = the instrument_id resolver")
    args = p.parse_args()
    schema = args.schema
    out_dir = OUT_DIRS[schema]

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
    client = db.Historical(os.environ["DATABENTO_API_KEY"])
    out_dir.mkdir(parents=True, exist_ok=True)

    days = _sessions(date.fromisoformat(args.start), date.fromisoformat(args.end))
    logger.info("backfill %s %s → %s: %d sessions",
                schema, args.start, args.end, len(days))

    done = skipped = failed = 0
    total_bytes = 0
    for i, d in enumerate(days, 1):
        out = out_dir / f"{d.isoformat()}.dbn.zst"
        if out.exists() and out.stat().st_size > 0:
            skipped += 1
            total_bytes += out.stat().st_size
            continue

        start, end = f"{d.isoformat()}{RTH[0]}", f"{d.isoformat()}{RTH[1]}"
        try:
            cost = client.metadata.get_cost(
                dataset=DATASET, schema=schema, symbols="ALL_SYMBOLS",
                start=start, end=end,
            )
        except Exception:
            logger.exception("[%d/%d] %s cost check failed — skipping", i, len(days), d)
            failed += 1
            continue

        if cost > args.max_cost:
            logger.error("[%d/%d] %s quoted $%.2f > max $%.2f — ABORTING RUN",
                         i, len(days), d, cost, args.max_cost)
            return 2

        t0 = time.time()
        tmp = out.with_suffix(".part")
        try:
            client.timeseries.get_range(
                dataset=DATASET, schema=schema, symbols="ALL_SYMBOLS",
                start=start, end=end, path=str(tmp),
            )
        except Exception:
            logger.exception("[%d/%d] %s download failed", i, len(days), d)
            tmp.unlink(missing_ok=True)
            failed += 1
            continue

        size = tmp.stat().st_size
        # A holiday returns a valid but tiny file; keep it so the gap is a
        # recorded fact rather than a hole that looks like a failed download.
        tmp.rename(out)
        total_bytes += size
        done += 1
        logger.info("[%d/%d] %s  %6.1f MB  %4.1f min  (cum %.1f GB)",
                    i, len(days), d, size / 1e6, (time.time() - t0) / 60,
                    total_bytes / 1e9)

    logger.info("done=%d skipped=%d failed=%d total=%.1f GB",
                done, skipped, failed, total_bytes / 1e9)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
