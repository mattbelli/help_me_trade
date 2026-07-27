"""What is missing from the flow corpus — raw files, rolled sessions, truncated pulls.

Roadmap 0.2. Written 2026-07-27, an hour after a hand-rolled check found
`2025-09-02` absent from an otherwise complete 89-session backfill. Nothing in
the system would have reported it: the backfill logs each day it finishes and
never revisits the ones it did not, so a gap is invisible unless something
enumerates what *should* be there.

Three checks, because this corpus has three distinct ways to be quietly wrong:

  1. **Absent** — no file at all. The 2025-09-02 case.
  2. **Not rolled** — file on disk, no rows at the current SCHEMA_VERSION. A
     crashed roll, or a session skipped because its definitions were missing.
  3. **Truncated** — a file that exists and is small. This is the dangerous one,
     because **the backfill's skip guard is `exists() and size > 0`**, so a
     partial download is skipped forever and looks finished on disk. It has
     already happened twice here: the eight 7 KB definition files from the
     UTC-window bug (which would have resolved 1.6M prints to nothing), and
     2025-09-02's 64.5 MB partial against a ~270 MB median.

The expected-session calendar is DERIVED, not hardcoded: a date is a trading day
iff `equity_bars` has bars for it. Same database, no market-calendar dependency,
and it cannot drift out of sync with the corpus it is checking.

    python tools/flow_corpus_gaps.py                  # whole range
    python tools/flow_corpus_gaps.py --start 2025-09-01 --end 2025-10-01

Exits 1 when anything is missing, so it can run as a cron and be noticed.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db.flow_corpus import (  # noqa: E402
    DEFAULT_DB_PATH,
    connect,
    ingested_dates,
)

ROOT = Path(__file__).resolve().parent.parent
TCBBO_DIR = ROOT / "data" / "databento" / "opra_tcbbo"
DEFS_DIR = ROOT / "data" / "databento" / "opra_defs"

# A file below this fraction of the median is reported as truncated. Deliberately
# loose: the point is to catch a partial download (64.5 MB against a 270 MB
# median = 0.24), not to police the natural spread between a quiet August session
# and a quarterly expiry. Half-days sit around 0.6 and must not trip it.
TRUNCATED_FRACTION = 0.35
# Below this, a "file" is an empty holiday marker rather than a truncated pull.
HOLIDAY_MAX_BYTES = 1024

logger = logging.getLogger("flow_corpus_gaps")


def expected_sessions(
    start: str, end: str, db_path: Path = DEFAULT_DB_PATH
) -> set[str]:
    """Trading days in ``[start, end)``, derived from equity bar coverage.

    A date with equity bars is a date the market traded, which is exactly the set
    of dates the corpus owes prints for. Holidays never appear, so they are not
    reported as gaps and their zero-byte marker files are simply extra.
    """
    with connect(db_path) as conn:
        return {
            r["date"]
            for r in conn.execute(
                "SELECT DISTINCT date FROM equity_bars "
                "WHERE date >= ? AND date < ? AND adjustment = 'split'",
                (start, end),
            )
        }


def file_sizes(directory: Path) -> dict[str, int]:
    """session date -> bytes, for every ``.dbn.zst`` in a pull directory."""
    return {p.name.split(".")[0]: p.stat().st_size
            for p in directory.glob("*.dbn.zst")}


def truncated(sizes: dict[str, int], expected: set[str]) -> list[tuple[str, int]]:
    """Sessions whose file is present but far below the median size.

    Median over the expected sessions only, and excluding holiday markers, so a
    directory that is half holidays cannot drag the reference down and mask a
    real truncation.
    """
    real = sorted(v for k, v in sizes.items()
                  if k in expected and v > HOLIDAY_MAX_BYTES)
    if len(real) < 5:            # too few to have a meaningful median
        return []
    median = real[len(real) // 2]
    floor = median * TRUNCATED_FRACTION
    return sorted(
        (k, v) for k, v in sizes.items()
        if k in expected and HOLIDAY_MAX_BYTES < v < floor
    )


def report(start: str, end: str, db_path: Path = DEFAULT_DB_PATH) -> int:
    expected = expected_sessions(start, end, db_path)
    if not expected:
        logger.error("no equity bars in %s..%s — cannot derive the calendar, "
                     "so an empty gap list here would be a false all-clear",
                     start, end)
        return 1

    tcbbo, defs = file_sizes(TCBBO_DIR), file_sizes(DEFS_DIR)
    rolled = {d for d in ingested_dates(db_path) if start <= d < end}

    missing_prints = sorted(expected - set(tcbbo))
    missing_defs = sorted(expected - set(defs))
    # Only sessions we actually hold prints for can be rolled; listing the rest
    # as un-rolled would just restate the missing-prints gap.
    unrolled = sorted((expected & set(tcbbo)) - rolled)
    short_prints = truncated(tcbbo, expected)
    short_defs = truncated(defs, expected)

    logger.info("%s..%s: %d trading days | prints %d | defs %d | rolled %d",
                start, end, len(expected), len(expected) - len(missing_prints),
                len(expected) - len(missing_defs), len(rolled))

    for label, items in (
        ("MISSING prints", missing_prints),
        ("MISSING definitions", missing_defs),
        ("NOT ROLLED at the current schema_version", unrolled),
    ):
        if items:
            logger.error("%s (%d): %s", label, len(items),
                         ", ".join(items[:20]) + (" ..." if len(items) > 20 else ""))
    for label, pairs in (("TRUNCATED prints", short_prints),
                         ("TRUNCATED definitions", short_defs)):
        if pairs:
            logger.error("%s (%d) — a partial download passes the size>0 skip "
                         "guard and is skipped forever: %s", label, len(pairs),
                         ", ".join(f"{d} {n / 1e6:.1f}MB" for d, n in pairs[:20]))

    bad = (len(missing_prints) + len(missing_defs) + len(unrolled)
           + len(short_prints) + len(short_defs))
    if not bad:
        logger.info("no gaps")
    return 1 if bad else 0


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--start", default="2025-07-28")
    p.add_argument("--end", default="2026-07-26", help="exclusive")
    args = p.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    return report(args.start, args.end)


if __name__ == "__main__":
    raise SystemExit(main())
