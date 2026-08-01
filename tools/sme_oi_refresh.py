"""Daily top-up of CME SME open interest — the JPM/JHEQX collar's live legs.

The 2026-07-20 purchase (data/databento/glbx_stats_sme_opt_2025-2026.dbn.zst) was a
one-shot: data/oi_walls.db fossilized at 2026-07-17 because nothing refreshed it. This
is the refresh, and the reason it must run DAILY rather than weekly is the quarterly
roll — the collar re-establishes at Mar/Jun/Sep/Dec month-end, so the one session a
weekly job could straddle is the session that defines the next quarter's legs.

⚠️ GLBX statistics is NOT free — unlike OPRA.PILLAR statistics, which the Databento
Standard subscription zeroes ($0.00 whole-market, measured 2026-07-28). Measured
2026-07-31 on GLBX.MDP3: SME.OPT statistics is $0.0033/session (~$0.05/mo), the whole
era re-pull $0.175, and ALL_SYMBOLS 7 days $6.28 — so the scope stays pinned to the
SME parent and every run quotes its cost first and aborts above --max-cost (Databento
batch submit has no funds pre-check; 2026-07-19: ~$43 billed past credit — streaming
get_range, used here, does pre-check).

Window: [last stored SME date, dataset available end). Starting AT the last stored date
rather than after it is deliberate — it re-reads one session every run, which costs
$0.003 and picks up CME's final-settlement revision of a preliminary OI value. Rows are
INSERT OR REPLACE keyed by (date, source, root, expiry, cp, strike), so overlap is free.

Publish timing (measured on the purchased corpus, 2,363 OI records over 177 sessions):
ts_recv clusters at 10:00 ET (891 records — the final settlement pass) and 21:00-23:00 ET
(905 — the preliminary), so a run in the ET afternoon has both for the prior session.
`oi_daily.date` is the UTC date of ts_recv (that is how the 2026-07-20 bulk load stamped
it and the column must stay one thing), so the ET-evening preliminary lands on the NEXT
date — read the column as a publish date, never as the session it describes.

Raw DBN is KEPT (~280 KB/session): the condensed table holds open interest only, while
settlement prices for later study stages exist nowhere else.

  python tools/sme_oi_refresh.py                    # top up from the DB's last date
  python tools/sme_oi_refresh.py --start 2026-01-01 # re-read a window (cost is quoted)
  python tools/sme_oi_refresh.py --dry-run          # quote the cost, fetch nothing
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import date
from pathlib import Path

import databento as db
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.oi_condense import condense_file, open_db, parse_sme  # noqa: E402

DATASET = "GLBX.MDP3"
SCHEMA = "statistics"
SYMBOLS = "SME.OPT"
STYPE = "parent"
ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "data" / "databento" / "sme_oi"
# The whole 10-month era quotes $0.175, so this ceiling passes any ordinary top-up
# and any plausible catch-up, while a full-era re-read has to be asked for explicitly.
DEFAULT_MAX_COST = 0.05

logger = logging.getLogger("sme_oi_refresh")


def _last_stored_date(conn) -> str | None:
    row = conn.execute("SELECT MAX(date) FROM oi_daily WHERE source='sme'").fetchone()
    return row[0] if row and row[0] else None


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--start", type=date.fromisoformat,
                    help="override the resume point (default: last stored SME date)")
    ap.add_argument("--max-cost", type=float, default=DEFAULT_MAX_COST)
    ap.add_argument("--dry-run", action="store_true", help="quote the cost, fetch nothing")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    load_dotenv(ROOT / ".env")
    client = db.Historical(os.environ["DATABENTO_API_KEY"])
    conn = open_db()

    last = _last_stored_date(conn)
    start = args.start or (date.fromisoformat(last) if last else date(2025, 11, 18))
    # The dataset's own end, not "today": GLBX rejects an end past its available range
    # (422 data_end_after_available_end), and that boundary moves intraday. Kept at full
    # timestamp precision — truncating it to a date would drop everything published today
    # and leave the store a permanent day further behind than the T+1 data requires.
    end_ts = str(client.metadata.get_dataset_range(dataset=DATASET)["end"])
    end_day = end_ts[:10]
    if end_ts <= start.isoformat():
        logger.info("nothing new: stored through %s, dataset ends %s", last, end_ts)
        return

    cost = client.metadata.get_cost(
        dataset=DATASET, schema=SCHEMA, symbols=SYMBOLS, stype_in=STYPE,
        start=start.isoformat(), end=end_ts)
    logger.info("window %s -> %s quotes $%.4f (stored through %s)",
                start, end_ts, cost, last)
    if args.dry_run:
        return
    if cost > args.max_cost:
        raise RuntimeError(
            f"quoted ${cost:.4f} > --max-cost ${args.max_cost:.4f}; aborting run")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    # Named by the WINDOW, not the run date: two runs in one UTC day fetch different
    # windows, and an end-date-only name would let the second overwrite the first —
    # silently dropping a session's settlement prices, which live only in the raw.
    raw = OUT_DIR / f"{start.isoformat()}_{end_day}.dbn.zst"
    part = raw.with_name(raw.name + ".part")
    part.unlink(missing_ok=True)
    client.timeseries.get_range(
        dataset=DATASET, schema=SCHEMA, symbols=SYMBOLS, stype_in=STYPE,
        start=start.isoformat(), end=end_ts, path=str(part))
    part.replace(raw)

    n = condense_file(conn, raw, "sme", parse_sme, day=None)
    new_last = _last_stored_date(conn)
    logger.info("condensed %d rows from %s; SME now stored through %s (was %s)",
                n, raw.name, new_last, last)
    for r in conn.execute(
            "SELECT root, expiry, cp, strike, oi FROM oi_daily"
            " WHERE source='sme' AND date=? ORDER BY oi DESC LIMIT 6", (new_last,)):
        logger.info("  wall %s %s %s %g: %s", *r[:4], f"{r[4]:,}")


if __name__ == "__main__":
    main()
