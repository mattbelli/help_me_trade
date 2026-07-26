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
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import databento as db
from dotenv import load_dotenv

DATASET = "OPRA.PILLAR"
_ET = ZoneInfo("America/New_York")
# Session bounds are computed per-date in ET, never as a fixed UTC slice. A fixed
# 13:30-20:00Z window is only 09:30-16:00 ET under EDT; from 2025-11-03 to
# 2026-03-06 it is 08:30-15:00 ET, which silently DROPS the 15:00-16:00 ET hour
# (MOC hedging, closing rebalance) on ~85 of 250 sessions. A 20-day baseline
# straddling a DST change would then fire a fabricated "distribution change" on
# every name at once — the same manufactured-seam failure Addendum B exists to
# prevent, in time-of-day space instead of price space.
# End is 16:15 ET, not 16:00: index options (SPX/SPXW/VIX/NDX/RUT) settle in the
# 16:00-16:15 session and carry ~31% of corpus premium. The free window rolls
# daily, so a minute not captured now is a purchase later.
SESSION_OPEN_ET = (9, 30)
SESSION_CLOSE_ET = (16, 15)
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


def _session_bounds_utc(d: date) -> tuple[str, str]:
    """ET session bounds for this date, rendered as UTC timestamps."""
    open_et = datetime(d.year, d.month, d.day, *SESSION_OPEN_ET, tzinfo=_ET)
    close_et = datetime(d.year, d.month, d.day, *SESSION_CLOSE_ET, tzinfo=_ET)
    fmt = "%Y-%m-%dT%H:%M"
    return (open_et.astimezone(ZoneInfo("UTC")).strftime(fmt),
            close_et.astimezone(ZoneInfo("UTC")).strftime(fmt))


def _definition_bounds_utc(d: date) -> tuple[str, str]:
    """Full UTC day. Definitions are a start-of-day snapshot, not intraday events,
    so a request that does not begin at UTC midnight returns only the contracts
    newly listed inside the window — Databento warns on this explicitly. Measured
    on 2025-07-28: the ET session window bills 0.1 MB (285 records) against
    575.4 MB for the full day, i.e. no usable instrument_id → underlying/strike/
    expiry/right map, which leaves every print in the tcbbo corpus unattributable."""
    return (f"{d.isoformat()}T00:00", f"{(d + timedelta(days=1)).isoformat()}T00:00")


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

        start, end = (_definition_bounds_utc(d) if schema == "definition"
                      else _session_bounds_utc(d))
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
