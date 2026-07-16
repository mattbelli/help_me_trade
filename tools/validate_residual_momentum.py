"""Validate `residual_momentum` — the EV/return backtest of the residual-momentum sleeve.

Candidate #1 from the 2026-07-08 strategy foundry
(References/STRATEGY_VALIDATION_QUEUE_2026-07-08.md). NOTE: the ranking is ALREADY BUILT and
correctness-gated — `research.momentum_reference.compute_target_set` (golden fixtures, checked by
tools/momentum_validate.py). This script adds ONLY the missing piece: the monthly portfolio-return
backtest + the significance / DSR / PBO gate. It reuses your proven ranking verbatim; it does not
redesign it.

The GATE (must clear ALL — same bar dip_buy / earnings_drift cleared):
  - block-bootstrap 95% CI (per-month) EXCLUDES 0     -> research.significance
  - PBO < 0.5 across the K / SPY-gate grid            -> research.overfit.pbo_cscv
  - DSR high after deflating for the grid searched    -> research.overfit.deflated_sharpe_ratio
  - survives ~10 bps round-trip cost (monthly rebalance -> turnover matters here)
Below that -> `validated="unverified"`, never a published win%.

Requires the survivorship-free Norgate universe in data/research/bars.db (a "Current & Past"
watchlist — delisted names present) + research/fixtures/momentum_sectors.json. Run on the PC.

SIMPLIFICATIONS (flagged so you can tighten before trusting the number):
  - Enters at the rank-date CLOSE (production would use next-day open); exits at next month-end.
  - Delisted names: exit = last close on/before the next rank date (delisting proxy).
Both are conservative-ish but adjust to your fill model before publishing a stat.

Usage:
  .venv/bin/python tools/validate_residual_momentum.py --bars-db data/research/bars.db
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from research.momentum_reference import (  # noqa: E402
    compute_target_set,
    load_bars_for_symbols,
    load_sector_map,
)
from research.overfit import deflated_sharpe_ratio, pbo_cscv  # noqa: E402
from research.significance import significance_report  # noqa: E402

# Grid searched -> DSR n_trials + PBO matrix. Primary = full quintile, SPY>SMA200 gate ON.
TOP_K = [None, 20, 10]        # None = the full top-quintile compute_target_set returns
SPY_GATE = [True, False]      # True = no new entries when SPY < its 200d SMA
PRIMARY = (None, True)
SMA_WINDOW = 200


def _month_end_dates(dates: list[str]) -> list[str]:
    """Last trading day of each calendar month, from a reference symbol's date list."""
    last: dict[str, str] = {}
    for d in dates:
        last[d[:7]] = d  # dates ascending -> keeps the latest per month
    return [last[m] for m in sorted(last)]


def _dmaps(bars: dict) -> dict[str, dict[str, int]]:
    return {sym: {d: i for i, d in enumerate(dts)} for sym, (dts, _c, _o) in bars.items()}


def _close_on_or_before(bars: dict, dmap: dict, sym: str, target: str) -> float | None:
    """Close at `target`, else the last close before it (delisting proxy)."""
    dts, closes, _ = bars[sym]
    idx = dmap[sym].get(target)
    if idx is not None:
        return closes[idx]
    lo, hi = 0, len(dts) - 1  # bisect_right for largest date <= target
    if not dts or dts[0] > target:
        return None
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if dts[mid] <= target:
            lo = mid
        else:
            hi = mid - 1
    return closes[lo]


def _spy_above_sma(bars: dict, dmap: dict, rank_date: str) -> bool:
    if "SPY" not in bars:
        return True  # no SPY loaded -> gate is a no-op (warned in main)
    dts, closes, _ = bars["SPY"]
    i = dmap["SPY"].get(rank_date)
    if i is None or i < SMA_WINDOW:
        return True
    return closes[i] >= float(np.mean(closes[i - SMA_WINDOW + 1 : i + 1]))


def _cell_returns(bars, dmap, sector_map, month_ends, *, top_k, spy_gate, cost_bps):
    """Monthly equal-weight portfolio % returns for one (top_k, spy_gate) cell."""
    rt_cost = 2.0 * cost_bps / 100.0
    entry_dates: list[str] = []
    rets: list[float] = []
    for m in range(len(month_ends) - 1):
        rank_date, exit_date = month_ends[m], month_ends[m + 1]
        if spy_gate and not _spy_above_sma(bars, dmap, rank_date):
            entry_dates.append(rank_date)
            rets.append(0.0)  # regime-off -> flat that month
            continue
        targets = compute_target_set(bars, sector_map, rank_date)
        if not targets:
            continue
        if top_k is not None:
            targets = targets[:top_k]
        names = [t["symbol"] for t in targets]
        name_rets: list[float] = []
        for sym in names:
            if sym not in bars:
                continue
            entry = _close_on_or_before(bars, dmap, sym, rank_date)
            exit_ = _close_on_or_before(bars, dmap, sym, exit_date)
            if entry and exit_ and entry > 0:
                name_rets.append((exit_ / entry - 1.0) * 100.0)
        if not name_rets:
            continue
        entry_dates.append(rank_date)
        rets.append(float(np.mean(name_rets)) - rt_cost)  # equal-weight, net round-trip
    return entry_dates, rets


def main() -> int:
    ap = argparse.ArgumentParser(description="Validate the residual_momentum sleeve (EV backtest).")
    ap.add_argument("--bars-db", default="data/research/bars.db")
    ap.add_argument("--sector-map", default=None)
    ap.add_argument("--cost-bps", type=float, default=10.0)
    ap.add_argument("--block-size", type=int, default=3, help="block-bootstrap block (months)")
    args = ap.parse_args()

    sector_map = load_sector_map(args.sector_map)
    syms = list(sector_map) + ["SPY"]
    bars = load_bars_for_symbols(syms, bars_db=Path(args.bars_db))
    if "SPY" not in bars:
        print("[warn] SPY not in bars.db -> the SPY>SMA200 regime gate is a NO-OP this run.")
    dmap = _dmaps(bars)
    ref = "SPY" if "SPY" in bars else max(bars, key=lambda s: len(bars[s][0]))
    month_ends = _month_end_dates(bars[ref][0])
    print(f"universe {len(bars)} symbols, {len(month_ends)} month-ends "
          f"({month_ends[0]}..{month_ends[-1]}) via {ref}, cost {args.cost_bps}bps/side\n")

    cells = [(k, g) for k in TOP_K for g in SPY_GATE]
    by_cell = {}
    for k, g in cells:
        d, r = _cell_returns(bars, dmap, sector_map, month_ends, top_k=k, spy_gate=g, cost_bps=args.cost_bps)
        by_cell[(k, g)] = dict(zip(d, r, strict=True))
    common = sorted(set.intersection(*[set(m) for m in by_cell.values()]))
    matrix = np.array([[by_cell[c][d] for c in cells] for d in common], dtype=float)
    prim_dates = list(common)
    prim = [by_cell[PRIMARY][d] for d in prim_dates]

    sig = significance_report(prim_dates, prim, block_size=args.block_size)
    pbo = pbo_cscv(matrix, n_splits=16)
    per_cell_sharpe = matrix.mean(axis=0) / (matrix.std(axis=0, ddof=1) + 1e-12)
    sr_var = float(np.var(per_cell_sharpe, ddof=1)) if len(cells) > 1 else 1e-6
    dsr = deflated_sharpe_ratio(np.array(prim) / 100.0, n_trials=len(cells), sr_variance=sr_var)

    arr = np.array(prim)
    years = sorted({d[:4] for d in prim_dates})
    yr_pos = sum(sum(r for dd, r in zip(prim_dates, prim, strict=True) if dd[:4] == y) > 0 for y in years)
    ci_lo, ci_hi = sig["block_ci"]
    passed = (ci_lo is not None and ci_lo > 0) and pbo["pbo"] < 0.5 and dsr["dsr"] >= 0.95
    row = {
        "label": "Residual Momentum",
        "win_rate": round(float((arr > 0).mean()), 3),
        "ev_pct": round(float(arr.mean()), 3),
        "ev_ci_pct": [round(ci_lo, 3), round(ci_hi, 3)] if ci_lo is not None else None,
        "n_setups": len(prim),
        "per_year_positive": f"{yr_pos}/{len(years)}",
        "validated": "validated" if passed else "unverified",
        "method": (f"12-1 residual (sector-neutral) top-quintile, monthly, SPY>SMA200 gate; "
                   f"net {args.cost_bps}bps; block-CI + DSR(n_trials>={len(cells)}) + PBO. "
                   f"Ranking = research.momentum_reference (golden-fixture correct)."),
        "note": ("Set DSR n_trials to the TRUE number of param cells your research searched, not just "
                 f"{len(cells)}. Monthly-return series -> per-MONTH not per-trade EV."),
    }

    print(f"per-MONTH EV: {arr.mean():+.3f}%   win {float((arr>0).mean())*100:.1f}%   "
          f"n={len(prim)} months   per-year+ {yr_pos}/{len(years)}")
    print(f"block-bootstrap 95% CI: [{ci_lo:+.3f}, {ci_hi:+.3f}]   P(<=0)={sig['p_le_zero']}")
    print(f"DSR={dsr['dsr']:.3f} (sr*={dsr['sr_star']:.3f})   PBO={pbo['pbo']:.3f}   "
          f"annualized SR≈{dsr['sr_annual']:.2f}")
    print(f"\nVERDICT: {'PASS -> validated' if passed else 'FAIL -> stays unverified'}\n")
    print("strategy_stats.json row (only publish if PASS):")
    print(json.dumps(row, indent=2))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
