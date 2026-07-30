"""Run the daily unusual-vs-peers flow perception scan (signals/flow_perception.py).

Post-close job. Reads the flow corpus (the only place the registered fence lives),
writes perception rows to the app DB, and — only when the founder has armed
``alerts.flow_perception_alerts_enabled`` — files inbox cards for names that cleared
the peer top decile on >= 2 components.

Kept out of the backend's report scheduler on purpose: this loads a full-market
numpy panel off a multi-GB corpus, which has no business inside the live event
loop. Run it after the corpus advances (tools/flow_corpus_rollup.py):

    python tools/flow_perception_scan.py                    # latest corpus session
    python tools/flow_perception_scan.py --session 2026-07-24 --dry-run
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from db.base import DEFAULT_DB_PATH  # noqa: E402
from signals.flow_perception import (  # noqa: E402
    earnings_phrase,
    emit_alerts,
    run_scan,
)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--session", help="corpus session to scan (default: the latest)")
    ap.add_argument("--corpus-db", type=Path, help="override the flow corpus path")
    ap.add_argument("--db", type=Path, default=DEFAULT_DB_PATH, help="app DB path")
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="compute and print only — persist nothing, alert nothing",
    )
    ap.add_argument("--top", type=int, default=15, help="rows to print")
    args = ap.parse_args()

    summary = run_scan(
        session=args.session,
        corpus_db=args.corpus_db,
        db_path=args.db,
        persist=not args.dry_run,
    )
    rows = summary.pop("rows_detail")
    print(
        f"session={summary['session']} (age {summary['session_age_days']} sessions) "
        f"rows={summary['rows']} written={summary['written']} "
        f"symbols={summary['symbols']} alert_candidates={summary['alert_candidates']}"
    )
    for row in sorted(rows, key=lambda r: (-r["n_fired"], r["symbol"], r["window"]))[
        : max(0, args.top)
    ]:
        earn = earnings_phrase(row)
        print(
            f"  {row['symbol']:<8} {row['window']:>2}d q{row['quintile']} "
            f"fired={row['n_fired']} [{row['fired_components']}] "
            f"{row['regime']}, {earn}"
        )
    if not args.dry_run:
        sent = emit_alerts(rows, db_path=args.db)
        print(f"alert cards filed: {len(sent)} (toggle off => 0)")


if __name__ == "__main__":
    main()
