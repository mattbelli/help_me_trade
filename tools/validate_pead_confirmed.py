"""Validate `pead_confirmed` — confirmation-gated post-earnings-announcement drift.  [SCAFFOLD]

Candidate #3 from the 2026-07-08 strategy foundry
(References/STRATEGY_VALIDATION_QUEUE_2026-07-08.md). This is the most involved of the three: it
needs TWO inputs the momentum path doesn't — earnings ANNOUNCE dates and daily VOLUME — so it ships
as a scaffold with those two seams marked. Everything else (the entry gate, the exit rules, the
forward-return sim, and the significance/DSR/PBO gate) is wired.

STRATEGY (from the queue):
  For each earnings announcement on day j:
    - at close of j+1 require ALL of: market-adjusted 2-day reaction AR > 0, in the top tercile of
      surprises, >= +3%, AND RVOL[j+1] >= 1.5  (surprise + price + volume must AGREE)
    - enter at OPEN of j+2, small equal weight
    - exit at T+45 trading days (sweep 30/45/63), -8% hard stop, optional +5%->10% trail
  Mechanism: prices under-react to earnings; the confirmation filter removes already-priced names.
  DECAY WARNING: PEAD is heavily published — validation must show it survives post-2010 net of cost.

TWO SEAMS TO WIRE ON THE PC (both raise NotImplementedError below):
  1. load_earnings_events(...)  -> the announce-date + surprise feed. You already have earnings infra:
     research/earnings.py + research/earnings_strategy.py + research/strategy_features.py:297
     (pre-earnings-drift entry). Reuse whatever produced earnings_drift's setups.
  2. load_volume_bars(...)      -> daily volume. research.momentum_reference.load_bars_for_symbols
     DROPS volume; query `SELECT date, v FROM daily_bars WHERE symbol=?` from the same bars.db.

Usage (after wiring the two seams):
  .venv/bin/python tools/validate_pead_confirmed.py --bars-db data/research/bars.db
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from research.momentum_reference import load_bars_for_symbols  # noqa: E402
from research.overfit import deflated_sharpe_ratio, pbo_cscv  # noqa: E402
from research.significance import significance_report  # noqa: E402

HOLD_GRID = [30, 45, 63]   # trading-day horizons swept (drives DSR n_trials + PBO)
PRIMARY_HOLD = 45
STOP_PCT = -8.0
REACTION_MIN_PCT = 3.0
RVOL_MIN = 1.5


# ── SEAM 1: earnings events ─────────────────────────────────────────────────────
def load_earnings_events(bars_db: Path) -> list[dict]:
    """RETURN: [{"symbol": str, "announce_date": "YYYY-MM-DD", "surprise": float}, ...].

    Wire to your existing earnings feed (research/earnings.py / earnings_strategy.py — the same
    source that built earnings_drift). `surprise` is the standardized earnings surprise used for the
    top-tercile gate. Point-in-time only: no restatements, announce_date is the ACTUAL report date.
    """
    import sqlite3

    ec = sqlite3.connect(str(bars_db.parent / "earnings.db"))
    raw = ec.execute("SELECT symbol, date FROM earnings WHERE date >= '2010-01-01'").fetchall()
    ec.close()
    syms = sorted({s for s, _ in raw})
    bars = load_bars_for_symbols(syms + ["SPY"], bars_db=bars_db)
    spy_dates, spy_close, _ = bars["SPY"]
    spy_i = {d: i for i, d in enumerate(spy_dates)}

    # surprise = EAR proxy: 2-day market-adjusted announcement return (close[j-1]->close[j+1] minus
    # SPY). Repo has NO PIT SUE (queue doc HARD GAP); EAR is the standard literature surrogate. Uses
    # only info known by close of j+1, before the j+2 open entry -> no lookahead.
    events: list[dict] = []
    for sym, adate in raw:
        if sym not in bars:
            continue
        dates, closes, _ = bars[sym]
        di = {d: i for i, d in enumerate(dates)}
        j = di.get(adate)
        si = spy_i.get(adate)
        if j is None or j < 1 or j + 1 >= len(closes) or si is None or si < 1 or si + 1 >= len(spy_close):
            continue
        if closes[j - 1] <= 0 or spy_close[si - 1] <= 0:
            continue
        stock_2d = (closes[j + 1] / closes[j - 1] - 1.0) * 100.0
        spy_2d = (spy_close[si + 1] / spy_close[si - 1] - 1.0) * 100.0
        events.append({"symbol": sym, "announce_date": adate, "surprise": stock_2d - spy_2d})
    return events


# ── SEAM 2: volume ──────────────────────────────────────────────────────────────
def load_volume_bars(symbols: list[str], bars_db: Path) -> dict[str, dict[str, float]]:
    """RETURN: {symbol: {date: volume}}. momentum_reference.load_bars_for_symbols drops volume;
    read it straight from daily_bars. Example:

        import sqlite3
        conn = sqlite3.connect(str(bars_db))
        out = {}
        for sym in symbols:
            rows = conn.execute("SELECT date, v FROM daily_bars WHERE symbol=?", (sym,)).fetchall()
            out[sym] = {d: float(v) for d, v in rows}
        return out
    """
    import sqlite3

    conn = sqlite3.connect(str(bars_db))
    out: dict[str, dict[str, float]] = {}
    for sym in symbols:
        rows = conn.execute("SELECT date, v FROM daily_bars WHERE symbol=?", (sym,)).fetchall()
        out[sym] = {d: float(v) for d, v in rows if v is not None}
    conn.close()
    return out


def _rvol(vol: dict[str, float], dates: list[str], idx: int, window: int = 20) -> float | None:
    if idx < window:
        return None
    hist = [vol.get(dates[k]) for k in range(idx - window, idx)]
    hist = [v for v in hist if v]
    if not hist:
        return None
    avg = float(np.mean(hist))
    today = vol.get(dates[idx])
    return (today / avg) if (today and avg > 0) else None


def _forward_return(closes, opens, entry_idx, *, hold, stop_pct, cost_bps) -> float | None:
    """Enter at open[entry_idx]; exit at min(T+hold close, -stop) — % net of round-trip cost."""
    if entry_idx >= len(opens):
        return None
    entry = opens[entry_idx]
    if entry <= 0:
        return None
    exit_idx = min(entry_idx + hold, len(closes) - 1)
    for k in range(entry_idx, exit_idx + 1):
        r = (closes[k] / entry - 1.0) * 100.0
        if r <= stop_pct:
            return stop_pct - 2.0 * cost_bps / 100.0
    return (closes[exit_idx] / entry - 1.0) * 100.0 - 2.0 * cost_bps / 100.0


def _cell_returns(bars, vol, spy, events, *, hold, cost_bps):
    """Confirmation-gated PEAD returns for one hold horizon."""
    spy_dates, spy_close, _ = spy
    spy_dmap = {d: i for i, d in enumerate(spy_dates)}
    # surprise tercile threshold across all events
    surprises = sorted(e["surprise"] for e in events if e.get("surprise") is not None)
    if not surprises:
        return [], []
    top_tercile = surprises[int(len(surprises) * 2 / 3)]

    entry_dates, rets = [], []
    for ev in events:
        sym, adate = ev["symbol"], ev["announce_date"]
        if sym not in bars or ev.get("surprise") is None or ev["surprise"] < top_tercile:
            continue
        dates, closes, opens = bars[sym]
        dmap = {d: i for i, d in enumerate(dates)}
        j = dmap.get(adate)
        if j is None or j + 2 >= len(dates):
            continue
        # 2-day market-adjusted reaction through close of j+1
        si = spy_dmap.get(dates[j + 1])
        if si is None or si < 2 or j < 1:
            continue
        stock_2d = (closes[j + 1] / closes[j - 1] - 1.0) * 100.0
        spy_2d = (spy_close[si] / spy_close[si - 2] - 1.0) * 100.0
        reaction = stock_2d - spy_2d
        rvol = _rvol(vol.get(sym, {}), dates, j + 1)
        if reaction <= 0 or reaction < REACTION_MIN_PCT or rvol is None or rvol < RVOL_MIN:
            continue
        r = _forward_return(closes, opens, j + 2, hold=hold, stop_pct=STOP_PCT, cost_bps=cost_bps)
        if r is not None:
            entry_dates.append(dates[j + 2])
            rets.append(r)
    return entry_dates, rets


def main() -> int:
    ap = argparse.ArgumentParser(description="Validate pead_confirmed (scaffold — wire 2 seams).")
    ap.add_argument("--bars-db", default="data/research/bars.db")
    ap.add_argument("--cost-bps", type=float, default=10.0)
    ap.add_argument("--block-size", type=int, default=10)
    args = ap.parse_args()
    bars_db = Path(args.bars_db)

    events = load_earnings_events(bars_db)              # SEAM 1
    symbols = sorted({e["symbol"] for e in events})
    bars = load_bars_for_symbols(symbols + ["SPY"], bars_db=bars_db)
    vol = load_volume_bars(symbols, bars_db)            # SEAM 2
    spy = bars["SPY"]

    by_cell = {}
    for hold in HOLD_GRID:
        d, r = _cell_returns(bars, vol, spy, events, hold=hold, cost_bps=args.cost_bps)
        by_cell[hold] = dict(zip(d, r, strict=True))
    common = sorted(set.intersection(*[set(m) for m in by_cell.values()]))
    matrix = np.array([[by_cell[h][d] for h in HOLD_GRID] for d in common], dtype=float)
    prim_dates = list(common)
    prim = [by_cell[PRIMARY_HOLD][d] for d in prim_dates]

    sig = significance_report(prim_dates, prim, block_size=args.block_size)
    pbo = pbo_cscv(matrix, n_splits=16)
    sr_var = float(np.var(matrix.mean(axis=0) / (matrix.std(axis=0, ddof=1) + 1e-12), ddof=1))
    dsr = deflated_sharpe_ratio(np.array(prim) / 100.0, n_trials=len(HOLD_GRID), sr_variance=sr_var)

    arr = np.array(prim)
    ci_lo, ci_hi = sig["block_ci"]
    passed = (ci_lo is not None and ci_lo > 0) and pbo["pbo"] < 0.5 and dsr["dsr"] >= 0.95
    row = {
        "label": "PEAD (confirmed)", "win_rate": round(float((arr > 0).mean()), 3),
        "ev_pct": round(float(arr.mean()), 3),
        "ev_ci_pct": [round(ci_lo, 3), round(ci_hi, 3)] if ci_lo is not None else None,
        "n_setups": len(prim),
        "validated": "validated" if passed else "unverified",
        "method": f"conf-gated PEAD, enter j+2 open, hold {PRIMARY_HOLD}td / {STOP_PCT}% stop, net {args.cost_bps}bps",
        "note": "Heavily published — trust only if it survives post-2010 net of cost. Set n_trials to your true grid.",
    }
    print(f"per-trade EV {arr.mean():+.3f}%  win {float((arr>0).mean())*100:.1f}%  n={len(prim)}")
    print(f"block CI [{ci_lo:+.3f},{ci_hi:+.3f}]  DSR={dsr['dsr']:.3f}  PBO={pbo['pbo']:.3f}")
    print(f"\nVERDICT: {'PASS' if passed else 'FAIL -> unverified'}\n{json.dumps(row, indent=2)}")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
