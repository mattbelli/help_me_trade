"""Manual SQLite maintenance for data/trading.db.

Run ``--vacuum`` once after the first retention pass, then quarterly. VACUUM locks the
whole database — never schedule it; use quiet hours and avoid market hours.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = ROOT / "data" / "trading.db"
_ET = ZoneInfo("America/New_York")


def _db_size_bytes(path: Path) -> int:
    return path.stat().st_size if path.exists() else 0


def _market_hours(now_et: datetime) -> bool:
    return 9 <= now_et.hour < 17


def vacuum_db(db_path: Path, *, force: bool = False) -> None:
    now_et = datetime.now(_ET)
    if _market_hours(now_et) and not force:
        print(
            "Refusing VACUUM during market hours (09:00–17:00 ET). "
            "Use --force to override.",
            file=sys.stderr,
        )
        sys.exit(1)

    if not db_path.exists():
        print(f"Database not found: {db_path}", file=sys.stderr)
        sys.exit(1)

    before = _db_size_bytes(db_path)
    print(f"Before: {before:,} bytes ({before / 1e6:.1f} MB)")

    conn = sqlite3.connect(db_path, timeout=60.0)
    try:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        conn.execute("VACUUM")
        conn.commit()
    finally:
        conn.close()

    after = _db_size_bytes(db_path)
    print(f"After:  {after:,} bytes ({after / 1e6:.1f} MB)")
    saved = before - after
    print(f"Reclaimed: {saved:,} bytes ({saved / 1e6:.1f} MB)")


def main() -> None:
    parser = argparse.ArgumentParser(description="Manual SQLite maintenance.")
    parser.add_argument(
        "--vacuum",
        action="store_true",
        help="Checkpoint WAL and VACUUM the trading database.",
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=DEFAULT_DB,
        help=f"Database path (default: {DEFAULT_DB})",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Run VACUUM even during market hours (09:00–17:00 ET).",
    )
    args = parser.parse_args()

    if not args.vacuum:
        parser.print_help()
        sys.exit(0)

    vacuum_db(args.db, force=args.force)


if __name__ == "__main__":
    main()
