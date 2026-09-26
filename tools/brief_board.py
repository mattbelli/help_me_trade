"""Build the nightly brief board -> data/briefs/<date>/board.json + board.md.

Stage 1 of the nightly deep brief: deterministic, no LLM. Exits nonzero only
when the board is empty (no candidates AND no index frame).

Usage:
    python tools/brief_board.py                 # the last closed session
    python tools/brief_board.py --date 2026-09-25 --stdout
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from research.brief.board import (  # noqa: E402
    board_is_empty,
    build_board,
    render_board_md,
    save_board,
)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--date", default=None, help="ET session YYYY-MM-DD (default: last closed)")
    p.add_argument("--stdout", action="store_true", help="also print board.md")
    a = p.parse_args()
    logging.basicConfig(level=logging.WARNING)

    board = build_board(date.fromisoformat(a.date) if a.date else None)
    out_dir = save_board(board)
    if a.stdout:
        print(render_board_md(board))
    cards = len((board.get("candidates") or {}).get("cards") or [])
    print(f"{board['et_date']}: {cards} candidates -> {out_dir}", file=sys.stderr)
    return 1 if board_is_empty(board) else 0


if __name__ == "__main__":
    raise SystemExit(main())
