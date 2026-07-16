"""The decisive test for pead_confirmed: momentum-neutralized control + honest-block-size CI.

Per the quant-review: the earnings entry gate collapses to a single signal — a 2-day market-adjusted
pop R >= max(top_tercile, 3%) with RVOL>=1.5. If the SAME filter on NON-earnings days (no earnings
within +/-5 td) drifts the same excess-over-SPY, the mechanism is generic short-term momentum, not
PEAD — which this repo already killed ("mostly beta"). Identical mechanics on both groups so the
CONTRAST is clean (the shared stop-fill optimism cancels).

Also recomputes the EARNINGS excess-over-SPY CI at block_size=45 (matches the 45td hold), the honest
autocorrelation length — the scaffold used 10, which is ~2x too tight.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from research.momentum_reference import load_bars_for_symbols  # noqa: E402
from research.significance import significance_report  # noqa: E402
from tools.validate_pead_confirmed import (  # noqa: E402
    PRIMARY_HOLD,
    RVOL_MIN,
    STOP_PCT,
    _rvol,
    load_earnings_events,
    load_volume_bars,
)

BARS_DB = Path("data/research/bars.db")
COST = 10.0
HOLD = PRIMARY_HOLD
REACTION_FLOOR = 3.0
BLACKOUT = 5  # trading days around an earnings date excluded from the control


def _trade(closes, opens, spy_close, spy_open, spy_dmap, dates, t):
    """Excess-over-SPY for entry at open[t+2], 45td hold, -8% stop. Returns (entry_date, excess)."""
    if t + 2 >= len(opens):
        return None
    entry = opens[t + 2]
    if entry <= 0:
        return None
    exit_idx = min(t + 2 + HOLD, len(closes) - 1)
    hit = None
    for k in range(t + 2, exit_idx + 1):
        if (closes[k] / entry - 1.0) * 100.0 <= STOP_PCT:
            hit = k
            break
    if hit is not None:
        raw = STOP_PCT - 2.0 * COST / 100.0
        xk = hit
    else:
        raw = (closes[exit_idx] / entry - 1.0) * 100.0 - 2.0 * COST / 100.0
        xk = exit_idx
    se = spy_dmap.get(dates[t + 2])
    sx = spy_dmap.get(dates[xk])
    if se is None or sx is None or spy_open[se] <= 0:
        return None
    spy_win = (spy_close[sx] / spy_open[se] - 1.0) * 100.0
    return dates[t + 2], raw - spy_win


def _reaction(closes, spy_close, spy_dmap, dates, t):
    """2-day market-adjusted return over [t-1, t+1] (the earnings 'surprise'/'reaction' signal)."""
    if t < 1 or t + 1 >= len(closes) or closes[t - 1] <= 0:
        return None
    si = spy_dmap.get(dates[t + 1])
    if si is None or si < 2:
        return None
    stock = (closes[t + 1] / closes[t - 1] - 1.0) * 100.0
    spy = (spy_close[si] / spy_close[si - 2] - 1.0) * 100.0
    return stock - spy


def main() -> None:
    events = load_earnings_events(BARS_DB)
    symbols = sorted({e["symbol"] for e in events})
    bars = load_bars_for_symbols(symbols + ["SPY"], bars_db=BARS_DB)
    vol = load_volume_bars(symbols, BARS_DB)
    spy_dates, spy_close, spy_open = bars["SPY"]
    spy_dmap = {d: i for i, d in enumerate(spy_dates)}

    surprises = sorted(e["surprise"] for e in events if e.get("surprise") is not None)
    top_tercile = surprises[int(len(surprises) * 2 / 3)]
    threshold = max(top_tercile, REACTION_FLOOR)
    print(f"gate: 2d mkt-adj reaction >= {threshold:.2f}% (tercile {top_tercile:.2f}) AND RVOL>={RVOL_MIN}\n")

    # earnings announce indices per symbol (for the control blackout)
    ann_idx: dict[str, set[int]] = {}
    for ev in events:
        sym, adate = ev["symbol"], ev["announce_date"]
        if sym not in bars:
            continue
        di = {d: i for i, d in enumerate(bars[sym][0])}
        j = di.get(adate)
        if j is not None:
            ann_idx.setdefault(sym, set()).add(j)

    earn_rows, ctrl_rows = [], []
    for sym in symbols:
        if sym not in bars:
            continue
        dates, closes, opens = bars[sym]
        ann = ann_idx.get(sym, set())
        v = vol.get(sym, {})
        for t in range(21, len(dates) - (HOLD + 3)):
            R = _reaction(closes, spy_close, spy_dmap, dates, t)
            if R is None or R < threshold:
                continue
            rvol = _rvol(v, dates, t + 1)
            if rvol is None or rvol < RVOL_MIN:
                continue
            is_earn = any(abs(t - a) <= BLACKOUT for a in ann)
            tr = _trade(closes, opens, spy_close, spy_open, spy_dmap, dates, t)
            if tr is None:
                continue
            (earn_rows if is_earn else ctrl_rows).append(tr)

    def summarize(name, rows):
        exc = np.array([r[1] for r in rows])
        rep = significance_report([r[0] for r in rows], list(exc), block_size=45)
        lo, hi = rep["block_ci"]
        print(f"{name:28s} n={len(rows):5d}  excess EV {exc.mean():+.3f}%  win {(exc>0).mean()*100:.1f}%  "
              f"CI[bs45] [{lo:+.3f},{hi:+.3f}]  P(<=0)={rep['p_le_zero']:.3f}")
        return exc.mean()

    print("EARNINGS group = the pead_confirmed trades; CONTROL = identical filter, non-earnings days.\n")
    e = summarize("EARNINGS (pead_confirmed)", earn_rows)
    c = summarize("CONTROL (non-earnings pop)", ctrl_rows)
    print(f"\nearnings-specific excess = EARNINGS - CONTROL = {e - c:+.3f}%/trade")
    print("  ~0 (control ~= earnings)  -> generic momentum, NOT PEAD -> KILL")
    print("  earnings >> control       -> genuinely earnings-driven drift -> KEEP")


if __name__ == "__main__":
    main()
