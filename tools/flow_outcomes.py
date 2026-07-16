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


MIN_UNITS = 20  # cells under this show "accruing" — correlated events overstate n
_UNIT_BY_HORIZON = {  # honest aggregation unit per horizon (design §Q2)
    "ret_15m": "hour",
    "ret_1h": "hour",
    "ret_eod": "day",
    "ret_1d": "day",
    "ret_5d": "day",
}


def _unit_key(r: sqlite3.Row, unit: str) -> tuple:
    # symbol-day for daily horizons; symbol-hour for intraday ones
    base = (r["underlying"], r["ts"][:10])
    return base + ((r["ts"][11:13],) if unit == "hour" else ())


def _aggregate_units(rows: list[sqlite3.Row], hz: str) -> list[float]:
    """Collapse correlated events into units: one value per symbol-day/hour,
    using the DOMINANT side's premium-weighted events; mixed units (<60%
    dominance by premium) are dropped — they have no honest single signal."""
    unit = _UNIT_BY_HORIZON[hz]
    by_unit: dict[tuple, list[sqlite3.Row]] = {}
    for r in rows:
        if r[hz] is None:
            continue
        by_unit.setdefault(_unit_key(r, unit), []).append(r)
    out: list[float] = []
    for _, rs in by_unit.items():
        prem = {"bullish": 0.0, "bearish": 0.0}
        for r in rs:
            sig = _signal(r["direction"], r["right"])
            if sig:
                prem[sig] += r["premium_usd"] or 0
        total = prem["bullish"] + prem["bearish"]
        if total <= 0:
            continue
        dom = "bullish" if prem["bullish"] >= prem["bearish"] else "bearish"
        if prem[dom] / total < 0.6:
            continue  # mixed unit — no dominant side
        vals = [
            r[hz] for r in rs if _signal(r["direction"], r["right"]) == dom
        ]
        if vals:
            out.append(median(vals))
    return out


def report() -> None:
    conn = sqlite3.connect(DEFAULT_DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        SELECT underlying, ts, event_type, direction, right, premium_usd, n_prints,
               ret_15m, ret_1h, ret_eod, ret_1d, ret_5d
        FROM options_flow_events
        WHERE event_type IN ('sweep','block','directional_pressure',
                             'flow_burst','flow_cluster')
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
    print(
        "Units = symbol-days (daily horizons) / symbol-hours (intraday) with a\n"
        f"dominant side; cells gate at n>={MIN_UNITS} units — correlated events\n"
        "in one session are NOT independent samples.\n"
    )
    order = ["$1M+", "$250k-1M", "$100k-250k", "$25k-100k"]
    for sig in ("bullish", "bearish"):
        print(f"## {sig} prints")
        print(f"{'bucket':12} {'hz':7} {'n_ev':>5} {'units':>6} {'median':>8} {'up%':>6}")
        for b in order:
            rs = groups.get((sig, b), [])
            if not rs:
                continue
            for hz in HORIZONS:
                n_ev = sum(1 for r in rs if r[hz] is not None)
                if n_ev == 0:
                    continue
                units = _aggregate_units(rs, hz)
                if len(units) < MIN_UNITS:
                    print(
                        f"{b:12} {hz[4:]:7} {n_ev:>5} {len(units):>6} "
                        f"{'accruing':>8} {'—':>6}"
                    )
                    continue
                up = sum(1 for v in units if v > 0) / len(units) * 100
                print(
                    f"{b:12} {hz[4:]:7} {n_ev:>5} {len(units):>6} "
                    f"{median(units) * 100:>7.2f}% {up:>5.0f}%"
                )
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
