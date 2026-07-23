"""Replay an already-filed nightly book decision into phone proposals.

One-off arming-night tool (2026-07-23): tonight's cycles filed while the books
were still dry, so their advanced picks registered in lanes but produced no
proposals. After arming, this replays the LATEST filed decision for a book
through the exact same proposal path the nightly driver uses
(research.book_driver._propose_advanced) — MOO entry style, analyst stop,
self-sized notional, RiskEngine precheck, phone push. Lane registration is NOT
repeated (the cycle already did it).

Explicit invocation only (script-mains-deny-by-default): requires --book, does
nothing on import, and refuses books that are not armed.

    uv run python tools/book_replay_proposals.py --book deepseek --book fable
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


async def _replay(book_id: str) -> int:
    from db.base import DEFAULT_DB_PATH
    from db.reports import recent_reports_by_kind
    from db.strategies import get_strategy
    from research.book_driver import BookDecision, _extract_decision, _propose_advanced
    from research.books import BOOKS

    book = BOOKS.get(book_id)
    if book is None or not book.strategy_id:
        print(f"[{book_id}] unknown or advisory-only book — skipped")
        return 1
    strategy = get_strategy(book.strategy_id, db_path=DEFAULT_DB_PATH)
    if not strategy or not strategy.get("enabled"):
        print(f"[{book_id}] NOT ARMED — refusing to replay")
        return 1

    decision: BookDecision | None = None
    # Session books leave their decision JSON on disk; in-process books embed
    # it as the report's last fenced block. Try disk first, then the report.
    work = Path("data/book_sessions/work") / f"{book_id}_decision.json"
    if work.exists():
        try:
            decision = BookDecision.model_validate(json.loads(work.read_text()))
        except Exception as exc:
            print(f"[{book_id}] work file unusable ({exc}); falling back to report")
    if decision is None:
        reports = recent_reports_by_kind(f"book_{book_id}", limit=1, db_path=DEFAULT_DB_PATH)
        if not reports:
            print(f"[{book_id}] no report on file")
            return 1
        decision, err = _extract_decision(reports[0]["body"])
        if decision is None:
            print(f"[{book_id}] could not parse decision from report: {err}")
            return 1

    results = await _propose_advanced(book_id, strategy, decision, db_path=DEFAULT_DB_PATH)
    if not results:
        print(f"[{book_id}] no advanced picks to replay")
        return 0
    for r in results:
        print(f"[{book_id}] {r.get('symbol')}: {r.get('status')}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--book",
        action="append",
        required=True,
        choices=["deepseek", "fable", "panel"],
        help="book id to replay (repeatable)",
    )
    args = parser.parse_args()
    rc = 0
    for book_id in dict.fromkeys(args.book):
        rc |= asyncio.run(_replay(book_id))
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
