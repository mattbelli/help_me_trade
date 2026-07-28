"""Import a date,value CSV into macro.db under a FRED series id — verify, then write.

Built for References/convex-bamlh0a0hym2-1825d.csv (Convex Trade, CC BY 4.0,
attribution in the file header): ICE BofA HY OAS is vendor-truncated to ~3 years
on every FRED endpoint, and this 5-year export patches 2021-07-29..2023-07-27
into the live store — including the 2022 hiking-cycle widening (peak 5.99).

Safety gate: rows whose date already exists in the store must MATCH the stored
value exactly, or the import aborts without writing. Values are taken as-is
(the Convex header says ``value_bps`` but the values are in percent, matching
FRED's units — verified 0 mismatches on 785 overlapping dates, 2026-07-28).

Dry-run by default; ``--apply`` writes.

Usage:
    python tools/macro_import_csv.py References/convex-bamlh0a0hym2-1825d.csv \
        --series BAMLH0A0HYM2 [--db data/macro.db] [--apply]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db.macro import DEFAULT_MACRO_DB_PATH, series_history, upsert_macro  # noqa: E402


def parse_csv(path: Path) -> list[tuple[str, float]]:
    """Parse date,value rows; skip '#' comments and a 'date,...' header line."""
    rows: list[tuple[str, float]] = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.lower().startswith("date,"):
            continue
        obs_date, value = line.split(",", 1)
        rows.append((obs_date.strip(), float(value.strip())))
    rows.sort()
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("csv_path", type=Path)
    ap.add_argument("--series", required=True, help="FRED series id to store under")
    ap.add_argument("--db", type=Path, default=DEFAULT_MACRO_DB_PATH)
    ap.add_argument("--apply", action="store_true", help="write (default: dry-run)")
    args = ap.parse_args()

    rows = parse_csv(args.csv_path)
    if not rows:
        print("no data rows parsed — aborting")
        return 1

    stored = dict(series_history(args.series, db_path=args.db))
    overlap = [d for d, _ in rows if d in stored]
    mismatches = [
        (d, v, stored[d]) for d, v in rows if d in stored and abs(v - stored[d]) > 1e-9
    ]
    new = [(d, v) for d, v in rows if d not in stored]

    print(f"csv rows: {len(rows)} ({rows[0][0]} .. {rows[-1][0]})")
    print(f"stored {args.series}: {len(stored)} rows")
    print(f"overlap: {len(overlap)}  mismatches: {len(mismatches)}  new: {len(new)}")
    if mismatches:
        for d, v, sv in mismatches[:10]:
            print(f"  MISMATCH {d}: csv={v} stored={sv}")
        print("overlap disagrees with the store — NOT writing")
        return 1
    if not new:
        print("nothing new to import")
        return 0
    if not args.apply:
        print(f"dry-run: would import {len(new)} rows ({new[0][0]} .. {new[-1][0]}); rerun with --apply")
        return 0

    written = upsert_macro([(args.series, d, v) for d, v in new], db_path=args.db)
    print(f"imported {written} rows ({new[0][0]} .. {new[-1][0]}) into {args.db}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
