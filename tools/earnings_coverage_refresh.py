"""Give every corpus name a forward earnings date — exact where known, estimated where not.

Why: the daily flow perception scan (`signals/flow_perception.py`) tags each
unusual-flow row with its earnings adjacency, and research finding 1.14e measured
that a filing inside 10 sessions multiplies day-grain big-move odds ~7-8x — the
single most informative bit on the card. But the live `earnings_calendar` only
covers what the backend's refresh loop tracks (enabled earnings strategies + the
watchlist: ~513 of the corpus's 5,939 names on 2026-07-29), so almost every card
read "no earnings data". This job closes that gap:

  * CONFIRMED dates keep coming from the vendor calendar path — untouched.
  * For everything else, EDGAR 8-K Item 2.02 history gives a CADENCE PROJECTION
    (last filing + the name's own median inter-filing gap), stored with
    `estimated = 1` / `source = 'edgar_estimate'` so it can never be mistaken for
    a confirmed date.
  * Names EDGAR says file nothing (ETFs like ARKG/FBT) are recorded as
    `non_filer` — "no earnings" is the CORRECT answer for them, and recording it
    stops us re-fetching them daily.

Standalone CLI, not a backend loop: this is thousands of sequential HTTP requests
and sync SQLite writes, which is exactly the sync-on-loop class that stalled the
event loop before. Cron it after the nightly capture chain; the orchestrator wires
the crontab.

    python tools/earnings_coverage_refresh.py --report
    python tools/earnings_coverage_refresh.py --limit 500
    python tools/earnings_coverage_refresh.py --symbols AAPL,ARKG --dry-run
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from db.base import DEFAULT_DB_PATH  # noqa: E402
from db.earnings_calendar import (  # noqa: E402
    coverage_summary,
    set_estimated_next_earnings,
    symbols_needing_refresh,
    upsert_coverage,
    upsert_earnings,
)
from db.flow_corpus import DEFAULT_DB_PATH as CORPUS_DB  # noqa: E402
from signals.earnings_edgar import (  # noqa: E402
    SOURCE,
    afetch_edgar_filings,
    classify_coverage,
)

logger = logging.getLogger("earnings_coverage_refresh")

_ET = ZoneInfo("America/New_York")

# One EDGAR request per symbol at ~7-8/s (the throttle lives in earnings_edgar).
# Batched so a run that dies mid-sweep still committed everything before the batch.
BATCH = 100


def corpus_universe(corpus_db: Path = CORPUS_DB) -> list[str]:
    """Distinct symbols in the equity bars store — the perception scan's universe."""
    con = sqlite3.connect(f"file:{corpus_db}?mode=ro", uri=True)
    try:
        return sorted(
            str(r[0]).strip().upper()
            for r in con.execute("SELECT DISTINCT symbol FROM equity_bars")
            if r[0]
        )
    finally:
        con.close()


def _record(
    symbol: str,
    fetched: dict[str, object] | None,
    *,
    today: str,
    db_path: Path,
) -> str | None:
    """Persist one symbol's verdict. Returns the state, or None if we never reached it."""
    if fetched is None:
        # Absent = the lookup failed. Recording anything here would turn a network
        # blip into a stored "we checked, nothing there".
        return None
    events = list(fetched.get("events") or [])
    verdict = classify_coverage(
        fetched.get("cik"), [str(e["date"]) for e in events], today=today
    )
    # Past filing dates are confirmed history — same source tag the live loop uses,
    # so the two paths converge on one row per real event.
    if events:
        upsert_earnings(
            [{"symbol": symbol, "source": SOURCE, **e} for e in events],
            db_path=db_path,
        )
    set_estimated_next_earnings(
        symbol, verdict["next_estimated_date"], db_path=db_path
    )
    upsert_coverage([{"symbol": symbol, **verdict}], db_path=db_path)
    return str(verdict["state"])


async def refresh(
    symbols: list[str],
    *,
    today: str,
    db_path: Path,
    dry_run: bool = False,
) -> dict[str, int]:
    counts = {"dated": 0, "non_filer": 0, "unknown": 0, "unreachable": 0}
    for start in range(0, len(symbols), BATCH):
        batch = symbols[start : start + BATCH]
        fetched = await afetch_edgar_filings(batch)
        for symbol in batch:
            got = fetched.get(symbol)
            if dry_run:
                state = (
                    None
                    if got is None
                    else str(
                        classify_coverage(
                            got.get("cik"),
                            [str(e["date"]) for e in (got.get("events") or [])],
                            today=today,
                        )["state"]
                    )
                )
            else:
                state = _record(symbol, got, today=today, db_path=db_path)
            counts[state or "unreachable"] += 1
        logger.info(
            "  %d/%d symbols: dated=%d non_filer=%d unknown=%d unreachable=%d",
            min(start + BATCH, len(symbols)),
            len(symbols),
            counts["dated"],
            counts["non_filer"],
            counts["unknown"],
            counts["unreachable"],
        )
    return counts


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", type=Path, default=DEFAULT_DB_PATH, help="app DB path")
    ap.add_argument(
        "--corpus-db", type=Path, default=CORPUS_DB, help="flow corpus DB path"
    )
    ap.add_argument("--symbols", help="comma-separated symbols instead of the corpus")
    ap.add_argument(
        "--limit", type=int, default=0, help="cap symbols this run (0 = no cap)"
    )
    ap.add_argument(
        "--max-age-days",
        type=int,
        default=7,
        help="re-check a known name after this many days (default 7)",
    )
    ap.add_argument(
        "--non-filer-max-age-days",
        type=int,
        default=90,
        help="re-check a confirmed non-filer after this many days (default 90)",
    )
    ap.add_argument(
        "--all", action="store_true", help="ignore staleness and re-check everything"
    )
    ap.add_argument(
        "--dry-run", action="store_true", help="fetch and classify, write nothing"
    )
    ap.add_argument("--report", action="store_true", help="print coverage and exit")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

    if args.report:
        print(coverage_summary(db_path=args.db))
        return 0

    if args.symbols:
        universe = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    else:
        universe = corpus_universe(args.corpus_db)
    today = datetime.now(_ET).date().isoformat()
    due = (
        universe
        if args.all
        else symbols_needing_refresh(
            universe,
            today=today,
            max_age_days=args.max_age_days,
            non_filer_max_age_days=args.non_filer_max_age_days,
            db_path=args.db,
        )
    )
    if args.limit > 0:
        due = due[: args.limit]
    logger.info("universe=%d due=%d today=%s", len(universe), len(due), today)
    if not due:
        print("nothing due")
        return 0

    counts = asyncio.run(
        refresh(due, today=today, db_path=args.db, dry_run=args.dry_run)
    )
    print(
        f"checked={len(due)} dated={counts['dated']} non_filer={counts['non_filer']} "
        f"unknown={counts['unknown']} unreachable={counts['unreachable']}"
        + (" (dry run — nothing written)" if args.dry_run else "")
    )
    if not args.dry_run:
        print(f"coverage now: {coverage_summary(db_path=args.db)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
