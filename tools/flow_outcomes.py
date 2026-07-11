"""Flow-outcome CLI: fill forward returns + print the pattern report.

Usage:
  uv run python tools/flow_outcomes.py --fill            # fetch bars, stamp outcomes
  uv run python tools/flow_outcomes.py --report          # aggregate hit-rate table
  uv run python tools/flow_outcomes.py --fill --report

Report groups the directional tape (sweep/block/pressure, side known) by
aggressor DIRECTION SIGNAL (bullish = buy call / sell put; bearish = buy put /
sell call) and premium bucket. Raw underlying returns; n= shown everywhere —
no conclusions implied under tiny samples.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path
from statistics import median

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db.base import DEFAULT_DB_PATH  # noqa: E402

HORIZONS = ("ret_15m", "ret_1h", "ret_eod", "ret_1d", "ret_5d")


def _signal(direction: str, right: str | None) -> str | None:
    r = (right or "").lower()
    if direction == "buy_side":
        return "bullish" if r.startswith("c") else "bearish" if r.startswith("p") else None
    if direction == "sell_side":
        return "bearish" if r.startswith("c") else "bullish" if r.startswith("p") else None
    return None


def _bucket(premium: float) -> str:
    if premium >= 1_000_000:
        return "$1M+"
    if premium >= 250_000:
        return "$250k-1M"
    if premium >= 100_000:
        return "$100k-250k"
    return "$25k-100k"


def report() -> None:
    conn = sqlite3.connect(DEFAULT_DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        SELECT underlying, ts, event_type, direction, right, premium_usd, n_prints,
               ret_15m, ret_1h, ret_eod, ret_1d, ret_5d
        FROM options_flow_events
        WHERE event_type IN ('sweep','block','directional_pressure')
          AND direction != 'undetermined'
        """
    ).fetchall()

    groups: dict[tuple[str, str], list[sqlite3.Row]] = {}
    for r in rows:
        sig = _signal(r["direction"], r["right"])
        if sig is None:
            continue
        groups.setdefault((sig, _bucket(r["premium_usd"] or 0)), []).append(r)

    print(f"# Flow outcomes — directional tape, {len(rows)} sided events\n")
    print("Signal convention: bullish print → ret>0 = 'flow was right'; "
          "bearish print → ret<0 = right.\n")
    order = ["$1M+", "$250k-1M", "$100k-250k", "$25k-100k"]
    for sig in ("bullish", "bearish"):
        print(f"## {sig} prints")
        print(f"{'bucket':12} {'hz':7} {'n':>4} {'median':>8} {'up%':>6}")
        for b in order:
            rs = groups.get((sig, b), [])
            if not rs:
                continue
            for hz in HORIZONS:
                vals = [r[hz] for r in rs if r[hz] is not None]
                if not vals:
                    continue
                up = sum(1 for v in vals if v > 0) / len(vals) * 100
                print(f"{b:12} {hz[4:]:7} {len(vals):>4} {median(vals)*100:>7.2f}% {up:>5.0f}%")
        print()

    # the founder's anecdote check: top-5 premium sided sweeps, individually
    print("## largest sided sweeps — individual outcomes")
    big = sorted(
        (r for r in rows if r["event_type"] == "sweep"),
        key=lambda r: -(r["premium_usd"] or 0),
    )[:8]
    print(f"{'ticker':7} {'time':17} {'sig':8} {'prem':>8} {'15m':>7} {'1h':>7} {'eod':>7} {'1d':>7}")
    for r in big:
        sig = _signal(r["direction"], r["right"]) or "?"
        cells = [
            f"{r[h]*100:>6.2f}%" if r[h] is not None else "     —"
            for h in ("ret_15m", "ret_1h", "ret_eod", "ret_1d")
        ]
        prem = (r["premium_usd"] or 0) / 1000
        print(f"{r['underlying']:7} {r['ts'][:16]:17} {sig:8} {prem:>7.0f}k {' '.join(cells)}")


def fill() -> None:
    from brokers.factory import get_broker
    from signals.options_flow.outcomes import fill_outcomes

    adapter = get_broker("alpaca")
    summary = fill_outcomes(adapter)
    print(f"fill: {summary}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--fill", action="store_true")
    ap.add_argument("--report", action="store_true")
    args = ap.parse_args()
    if not (args.fill or args.report):
        ap.error("pass --fill and/or --report")
    if args.fill:
        fill()
    if args.report:
        report()
