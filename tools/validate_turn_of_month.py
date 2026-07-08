"""Validate `turn_of_month` — buy SPY near month-end, exit early next month.

Candidate #2 from the 2026-07-08 strategy foundry
(References/STRATEGY_VALIDATION_QUEUE_2026-07-08.md). The CHEAPEST clean test in the queue:
SPY + a trading calendar only — NO survivorship-free data needed. Run this FIRST to shake out
the whole validation pipeline (significance + DSR + PBO) end-to-end before the bigger jobs.

The GATE (must clear ALL — mirrors how dip_buy / earnings_drift earned "validated"):
  - block-bootstrap 95% CI (per-entry) EXCLUDES 0     -> research.significance.significance_report
  - PBO < 0.5 across the param grid                   -> research.overfit.pbo_cscv
  - DSR high after deflating for the grid searched    -> research.overfit.deflated_sharpe_ratio
  - survives ~10 bps round-trip cost (default below)
Anything short of that stays `validated="unverified"` — NEVER a published win%.
This is a HYPOTHESIS test, not a result: turn-of-month is a well-known effect that has DECAYED
post-publication, so the real question is "still alive post-2010, net of cost?". Let the CI answer.

Data: SPY daily bars in data/research/bars.db (table daily_bars; source doesn't matter for a
single always-listed ETF). On the PC point --bars-db at whatever you ingested.

Usage:
  .venv/bin/python tools/validate_turn_of_month.py --bars-db data/research/bars.db
  .venv/bin/python tools/validate_turn_of_month.py --cost-bps 10 --start 2010-01-01
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from research.momentum_reference import load_bars_for_symbols  # noqa: E402
from research.overfit import deflated_sharpe_ratio, pbo_cscv  # noqa: E402
from research.significance import significance_report  # noqa: E402

# Grid searched (drives DSR n_trials + the PBO matrix). Primary cell = (entry_from_end=1, hold=3),
# i.e. enter at the 2nd-to-last session's close, exit at the close of the 3rd session of next month.
ENTRY_FROM_END = [1, 2]        # 1 = 2nd-to-last session of month, 2 = 3rd-to-last
HOLD_SESSIONS = [2, 3, 4, 5]   # sessions into the new month to exit (close)
PRIMARY = (1, 3)


def _month_sessions(dates: list[str]) -> list[list[int]]:
    """Group bar indices by calendar month (YYYY-MM), preserving chronological order."""
    buckets: dict[str, list[int]] = defaultdict(list)
    for i, d in enumerate(dates):
        buckets[d[:7]].append(i)
    return [buckets[m] for m in sorted(buckets)]


def _cell_returns(
    closes: list[float], dates: list[str], months: list[list[int]],
    *, entry_from_end: int, hold: int, cost_bps: float,
) -> tuple[list[str], list[float]]:
    """Per-month % return for one (entry_from_end, hold) cell, net of round-trip cost."""
    entry_dates: list[str] = []
    rets: list[float] = []
    rt_cost = 2.0 * cost_bps / 100.0  # bps -> % round trip
    for m in range(len(months) - 1):
        cur, nxt = months[m], months[m + 1]
        if len(cur) < entry_from_end + 1 or len(nxt) < hold:
            continue
        i_entry = cur[-1 - entry_from_end]   # e.g. entry_from_end=1 -> 2nd-to-last session
        i_exit = nxt[hold - 1]               # hold=3 -> 3rd session of next month
        c0, c1 = closes[i_entry], closes[i_exit]
        if c0 <= 0 or c1 <= 0:
            continue
        entry_dates.append(dates[i_entry])
        rets.append((c1 / c0 - 1.0) * 100.0 - rt_cost)
    return entry_dates, rets


def main() -> int:
    ap = argparse.ArgumentParser(description="Validate the turn_of_month SPY sleeve.")
    ap.add_argument("--bars-db", default="data/research/bars.db")
    ap.add_argument("--symbol", default="SPY")
    ap.add_argument("--start", default=None, help="ISO date; ignore bars before this")
    ap.add_argument("--cost-bps", type=float, default=10.0, help="one-way cost in bps")
    ap.add_argument("--block-size", type=int, default=3, help="block-bootstrap block (months)")
    args = ap.parse_args()

    bars = load_bars_for_symbols([args.symbol], bars_db=Path(args.bars_db))
    if args.symbol not in bars:
        print(f"[FAIL] no bars for {args.symbol} in {args.bars_db}")
        return 2
    dates, closes, _opens = bars[args.symbol]
    if args.start:
        keep = [i for i, d in enumerate(dates) if d >= args.start]
        dates = [dates[i] for i in keep]
        closes = [closes[i] for i in keep]
    months = _month_sessions(dates)
    print(f"{args.symbol}: {len(dates)} bars, {len(months)} months "
          f"({dates[0]}..{dates[-1]}), cost {args.cost_bps}bps/side\n")

    # Build the grid -> aligned returns matrix (rows = months common to all cells).
    cells: list[tuple[int, int]] = [(e, h) for e in ENTRY_FROM_END for h in HOLD_SESSIONS]
    by_cell: dict[tuple[int, int], dict[str, float]] = {}
    for e, h in cells:
        d, r = _cell_returns(closes, dates, months, entry_from_end=e, hold=h, cost_bps=args.cost_bps)
        by_cell[(e, h)] = dict(zip(d, r, strict=True))
    common = sorted(set.intersection(*[set(m.keys()) for m in by_cell.values()]))
    matrix = np.array([[by_cell[c][d] for c in cells] for d in common], dtype=float)  # (T, M)

    prim_dates = [d for d in common]
    prim_rets = [by_cell[PRIMARY][d] for d in prim_dates]

    # 1) Overlap-aware block-bootstrap CI (the "does the edge exclude 0" gate).
    sig = significance_report(prim_dates, prim_rets, block_size=args.block_size)
    # 2) PBO across the grid.
    pbo = pbo_cscv(matrix, n_splits=16)
    # 3) DSR deflated by the number of grid cells actually searched.
    per_cell_sharpe = matrix.mean(axis=0) / (matrix.std(axis=0, ddof=1) + 1e-12)
    sr_var = float(np.var(per_cell_sharpe, ddof=1)) if len(cells) > 1 else 1e-6
    dsr = deflated_sharpe_ratio(np.array(prim_rets) / 100.0, n_trials=len(cells), sr_variance=sr_var)

    arr = np.array(prim_rets)
    win_rate = float((arr > 0).mean())
    ev_pct = float(arr.mean())
    years = sorted({d[:4] for d in prim_dates})
    yr_pos = sum(sum(r for dd, r in zip(prim_dates, prim_rets, strict=True) if dd[:4] == y) > 0
                 for y in years)

    ci_lo, ci_hi = sig["block_ci"]
    passed = (ci_lo is not None and ci_lo > 0) and pbo["pbo"] < 0.5 and dsr["dsr"] >= 0.95
    row = {
        "label": "Turn-of-Month (SPY)",
        "win_rate": round(win_rate, 3),
        "ev_pct": round(ev_pct, 3),
        "ev_ci_pct": [round(ci_lo, 3), round(ci_hi, 3)] if ci_lo is not None else None,
        "n_setups": len(prim_rets),
        "per_year_positive": f"{yr_pos}/{len(years)}",
        "validated": "validated" if passed else "unverified",
        "method": (f"SPY {PRIMARY[0]+1}th-to-last close -> {PRIMARY[1]}rd session next month; "
                   f"net {args.cost_bps}bps/side; block-bootstrap CI + DSR(n_trials={len(cells)}) + PBO"),
        "note": ("Well-known calendar effect — validation checks post-2010 persistence net of cost. "
                 "Diversifier: pure calendar trigger, independent of dip_buy/earnings_drift."),
    }

    print(f"per-trade EV: {ev_pct:+.3f}%   win {win_rate*100:.1f}%   n={len(prim_rets)}   "
          f"per-year+ {yr_pos}/{len(years)}")
    print(f"block-bootstrap 95% CI: [{ci_lo:+.3f}, {ci_hi:+.3f}]   P(<=0)={sig['p_le_zero']}")
    print(f"DSR={dsr['dsr']:.3f} (sr*={dsr['sr_star']:.3f}, n_trials={len(cells)})   "
          f"PBO={pbo['pbo']:.3f}")
    print(f"\nVERDICT: {'PASS -> validated' if passed else 'FAIL -> stays unverified'}\n")
    print("strategy_stats.json row (only publish if PASS):")
    print(json.dumps(row, indent=2))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
