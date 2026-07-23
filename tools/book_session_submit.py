"""Submit a session-driven book cycle (Fable / Claude Panel) — the session's LAST act.

Validates the decision JSON, registers every candidate into the book's ledger
lanes, creates proposals only if the strategy record is armed, saves the report,
and delivers it (app/web inbox + phone push). Exits nonzero on invalid/skipped
so the calling session sees it and can fix + re-run.

Usage:
    uv run python tools/book_session_submit.py --book fable \
        --report data/book_sessions/work/fable_report.md \
        --decision data/book_sessions/work/fable_decision.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from research.book_driver import submit_session_cycle  # noqa: E402

SESSION_BOOKS = ("fable", "panel")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--book", choices=SESSION_BOOKS, required=True)
    parser.add_argument("--report", required=True, help="path to the markdown report")
    parser.add_argument("--decision", required=True, help="path to the decision JSON")
    args = parser.parse_args()

    report_md = Path(args.report).read_text(encoding="utf-8")
    try:
        decision = json.loads(Path(args.decision).read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(f"decision JSON does not parse: {exc}", file=sys.stderr)
        return 1
    if not isinstance(decision, dict):
        print("decision JSON must be an object", file=sys.stderr)
        return 1

    result = asyncio.run(submit_session_cycle(args.book, report_md, decision))
    print(json.dumps(result, default=str))
    return 0 if result.get("status") == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
