#!/usr/bin/env python3
"""Rebuild the per-name flow rarity lookup from the corpus, then backfill today.

Reads ``data/flow_corpus.db`` (16 GB, read-only). Writes the lookup into the
app DB. Never called from the 5s poll path.

    .venv/bin/python tools/flow_rarity_refresh.py
    .venv/bin/python tools/flow_rarity_refresh.py --backfill-since 2026-08-12T00:00:00Z

Hooked from ``tools/flow_perception_nightly.sh`` after the corpus roll.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db.base import DEFAULT_DB_PATH  # noqa: E402
from db.flow_corpus import DEFAULT_DB_PATH as CORPUS_DB_PATH  # noqa: E402
from db.flow_rarity import (  # noqa: E402
    build_lookup,
    laplace_p,
    lookup_cell,
    stamp_existing,
)

_ET = ZoneInfo("America/New_York")
logger = logging.getLogger("flow_rarity_refresh")


def _session_open_utc(now: datetime | None = None) -> str:
    """00:00 ET of the current ET date, as a UTC ISO timestamp."""
    et = (now or datetime.now(UTC)).astimezone(_ET)
    start = et.replace(hour=0, minute=0, second=0, microsecond=0)
    return start.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _pack(found: tuple | None, prem: float) -> dict:
    if found is None:
        return {"ranked": False}
    n, peaks, asof = found
    return {
        "ranked": True,
        "n": n,
        "n_active": len(peaks),
        "max_usd": peaks[-1] if peaks else 0,
        "asof": asof,
        "p": round(laplace_p(prem, peaks, n), 6),
    }


def _probe(db_path: Path) -> dict:
    """Live sanity: firehose vs quieter names in the same shape cell."""
    return {
        "TSLA_90+_otm2_10_C_765k": _pack(
            lookup_cell("TSLA", "C", "90+", "otm2_10", db_path=db_path), 765_000
        ),
        "TSLA_0-7_atm_C_765k": _pack(
            lookup_cell("TSLA", "C", "0-7", "atm", db_path=db_path), 765_000
        ),
        "PAAS_31-90_otm10p_C_150k": _pack(
            lookup_cell("PAAS", "C", "31-90", "otm10p", db_path=db_path), 150_000
        ),
        "LAZ_31-90_otm10p_C_100k": _pack(
            lookup_cell("LAZ", "C", "31-90", "otm10p", db_path=db_path), 100_000
        ),
    }


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--corpus-db", type=Path, default=CORPUS_DB_PATH)
    ap.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    ap.add_argument("--window", type=int, default=0, help="override trailing sessions")
    ap.add_argument("--skip-build", action="store_true", help="backfill only")
    ap.add_argument("--skip-backfill", action="store_true")
    ap.add_argument("--backfill-since", default="", help="ISO ts; default ET midnight")
    args = ap.parse_args()

    out: dict = {}
    if not args.skip_build:
        kw = {"corpus_path": args.corpus_db, "db_path": args.db}
        if args.window:
            kw["window"] = args.window
        out["build"] = build_lookup(**kw)
        logger.info(
            "lookup asof=%s names=%s cells=%s skipped=%s",
            out["build"]["asof_date"],
            out["build"]["n_names"],
            out["build"]["n_cells"],
            out["build"]["n_skipped"],
        )
        logger.info("calibration %s", json.dumps(out["build"]["calibration"]))

    out["probe"] = _probe(args.db)

    if not args.skip_backfill:
        since = args.backfill_since or _session_open_utc()
        n = stamp_existing(since=since, db_path=args.db)
        out["backfill"] = {"since": since, "stamped": n}
        logger.info("backfill since=%s stamped=%s", since, n)

    print(json.dumps(out, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
