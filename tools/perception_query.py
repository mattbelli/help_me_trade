"""Perception layer as a CLI — the agent surface for headless book sessions.

The in-process books (DeepSeek) reach our data through research/book_tools.py.
The headless Claude Code books had NO perception access at all, so their seats
went to the open web for prices and OHLC we already hold on disk. This is the
same read-only layer behind a subprocess so those sessions can query it too.

Read-only by construction: it exposes exactly get_context / query /
capabilities. The perception layer has no write path, and the mutating chat
tools (orders, rules, watchlist edits) are not reachable from here.

Usage:
    uv run python tools/perception_query.py capabilities
    uv run python tools/perception_query.py context NVDA
    uv run python tools/perception_query.py query market
    uv run python tools/perception_query.py query news --filter symbol=NVDA --filter limit=5
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ai.perception import build_symbol_context, list_capabilities  # noqa: E402
from ai.perception.resources import query  # noqa: E402
from db.base import DEFAULT_DB_PATH  # noqa: E402

# One read can be huge (get_context on a busy symbol). Cap what a session can
# pull into its context in a single call — the same guard research/book_tools.py
# applies on the in-process path.
_RESULT_CHAR_CAP = 20_000


def _coerce(value: str) -> object:
    """`--filter limit=5` arrives as a string; readers expect int/bool."""
    low = value.strip().lower()
    if low in ("true", "false"):
        return low == "true"
    try:
        return int(value)
    except ValueError:
        return value


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("capabilities", help="manifest of every readable resource and field group")

    ctx = sub.add_parser("context", help="everything the app knows about one symbol")
    ctx.add_argument("symbol")

    qry = sub.add_parser("query", help="read one resource (see `capabilities` for names)")
    qry.add_argument("resource")
    qry.add_argument(
        "--filter",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="repeatable resource filter, e.g. --filter symbol=NVDA",
    )

    parser.add_argument(
        "--max-chars",
        type=int,
        default=_RESULT_CHAR_CAP,
        help=f"truncate output (default {_RESULT_CHAR_CAP:,})",
    )
    args = parser.parse_args()

    if args.command == "capabilities":
        payload: object = list_capabilities()
    elif args.command == "context":
        payload = asyncio.run(build_symbol_context(args.symbol, db_path=DEFAULT_DB_PATH))
    else:
        filters: dict[str, object] = {}
        for item in args.filter:
            if "=" not in item:
                parser.error(f"--filter expects KEY=VALUE, got {item!r}")
            key, _, value = item.partition("=")
            filters[key.strip()] = _coerce(value)
        payload = asyncio.run(query(args.resource, filters, db_path=DEFAULT_DB_PATH))

    rendered = json.dumps(payload, indent=2, default=str)
    if len(rendered) > args.max_chars:
        rendered = rendered[: args.max_chars] + f"\n…(truncated at {args.max_chars:,} chars)"
    print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
