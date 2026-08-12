#!/usr/bin/env python3
"""Snapshot index gamma exposure into the history store. One row per run.

Cron this AFTER the close: Alpaca option quotes are RTH-only and freeze at 16:00
ET, so a post-close run prices the board on the session's closing quotes against
the open interest published that morning — which is the prior session's
positioning, exactly the standard construction and labelled as such in the row.

    .venv/bin/python tools/gex_refresh.py

Exits non-zero when no board could be priced, so a cron wrapper's failure
notification fires instead of the history silently flat-lining.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db.gamma_exposure import record  # noqa: E402
from signals.gamma_exposure import build_board, summarize  # noqa: E402


def main() -> int:
    board = build_board()
    if board is None:
        print("no priced board (OI store or index chain unavailable)", file=sys.stderr)
        return 1
    summary = summarize(board)
    as_of = record(summary)
    if as_of is None:
        print("board priced but not storable (no quote timestamp)", file=sys.stderr)
        return 1
    print(json.dumps({
        "stored": as_of,
        "spot": summary["spot"],
        "net_b": summary["net_gamma_usd_per_1pct_b"],
        "flip": summary.get("gamma_flip_spot"),
        "oi_as_of": summary["oi_as_of"],
        "contracts": summary["coverage"]["contracts_priced"],
    }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
