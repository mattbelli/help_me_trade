"""One-shot full-history backfill of the macro regime substrate (data/macro.db).

Keyless fredgraph.csv, sequential, ~16 requests total. Rerunnable — upserts.

    .venv/bin/python tools/macro_backfill.py
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db.macro import coverage  # noqa: E402
from signals.macro_substrate import MACRO_SERIES, backfill_macro_substrate  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
logger = logging.getLogger("macro_backfill")


def main() -> int:
    written = backfill_macro_substrate()
    failed = sorted(s for s, n in written.items() if n == 0)
    for row in coverage():
        logger.info(
            "%-14s %6d rows  %s -> %s", row["series"], row["rows"], row["first"], row["last"]
        )
    logger.info(
        "done=%d failed=%d total_rows=%d",
        len(MACRO_SERIES) - len(failed),
        len(failed),
        sum(written.values()),
    )
    if failed:
        logger.error("FAILED series: %s", ", ".join(failed))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
