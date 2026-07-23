"""Render nightly context packs for the LLM paper books (spec §5.1).

Deterministic, no LLM, DB reads only. Packs are archived per ET date under
data/book_packs/ (gitignored) so every book decision is auditable against
exactly what the model saw.

Usage:
    uv run python tools/book_context_pack.py --all
    uv run python tools/book_context_pack.py --book deepseek [--stdout]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db.base import DEFAULT_DB_PATH  # noqa: E402
from research.books import BOOKS, archive_book_pack, build_book_pack  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--book", choices=sorted(BOOKS), help="render one book's pack")
    group.add_argument("--all", action="store_true", help="render every book's pack")
    parser.add_argument(
        "--stdout", action="store_true", help="print instead of archiving to data/book_packs/"
    )
    args = parser.parse_args()

    book_ids = sorted(BOOKS) if args.all else [args.book]
    for book_id in book_ids:
        pack = build_book_pack(book_id, db_path=DEFAULT_DB_PATH)
        if args.stdout:
            print(pack)
            continue
        out_path = archive_book_pack(book_id, pack)
        print(f"wrote {out_path} ({len(pack):,} chars)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
