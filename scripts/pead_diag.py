"""Adversarial diagnostics on pead_confirmed's PASS — is it alpha or beta/momentum?

Reuses the wired seams from tools/validate_pead_confirmed.py. For the PRIMARY hold (45td) it
recomputes each trade's RAW return AND the SPY-matched return over the *same* calendar window,
then reports: excess-over-SPY, pre/post-2018 OOS split, and a crude momentum-neutral control
(same entry filter but on NON-earnings days). Diagnostic only — no gate, no published number.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from research.momentum_reference import load_bars_for_symbols  # noqa: E402
from tools.validate_pead_confirmed import (  # noqa: E402
    PRIMARY_HOLD,
    REACTION_MIN_PCT,
    RVOL_MIN,
    STOP_PCT,
    _rvol,
    load_earnings_events,
    load_volume_bars,
)

BARS_DB = Path("data/research/bars.db")
COST = 10.0
HOLD = PRIMARY_HOLD


def _fwd(closes, opens, entry_idx, hold, stop_pct, cost_bps):
    if entry_idx >= len(opens) or opens[entry_idx] <= 0:
        return None
    entry = opens[entry_idx]
    exit_idx = min(entry_idx + hold, len(closes) - 1)
    for k in range(entry_idx, exit_idx + 1):
        r = (closes[k] / entry - 1.0) * 100.0
        if r <= stop_pct:
            return stop_pct - 2.0 * cost_bps / 100.0, k
    return (closes[exit_idx] / entry - 1.0) * 100.0 - 2.0 * cost_bps / 100.0, exit_idx


def main() -> None:
    events = load_earnings_events(BARS_DB)
    symbols = sorted({e["symbol"] for e in events})
    bars = load_bars_for_symbols(symbols + ["SPY"], bars_db=BARS_DB)
    vol = load_volume_bars(symbols, BARS_DB)
    spy_dates, spy_close, spy_open = bars["SPY"]
    spy_dmap = {d: i for i, d in enumerate(spy_dates)}

    surprises = sorted(e["surprise"] for e in events if e.get("surprise") is not None)
    top_tercile = surprises[int(len(surprises) * 2 / 3)]

    rows = []  # (entry_date, raw, spy_window, excess)
    for ev in events:
        sym, adate = ev["symbol"], ev["announce_date"]
        if sym not in bars or ev.get("surprise") is None or ev["surprise"] < top_tercile:
            continue
        dates, closes, opens = bars[sym]
        dmap = {d: i for i, d in enumerate(dates)}
        j = dmap.get(adate)
        if j is None or j + 2 >= len(dates) or j < 1:
            continue
        si = spy_dmap.get(dates[j + 1])
        if si is None or si < 2:
            continue
        stock_2d = (closes[j + 1] / closes[j - 1] - 1.0) * 100.0
        spy_2d = (spy_close[si] / spy_close[si - 2] - 1.0) * 100.0
        if (stock_2d - spy_2d) <= 0 or (stock_2d - spy_2d) < REACTION_MIN_PCT:
            continue
        rvol = _rvol(vol.get(sym, {}), dates, j + 1)
        if rvol is None or rvol < RVOL_MIN:
            continue
        res = _fwd(closes, opens, j + 2, HOLD, STOP_PCT, COST)
        if res is None:
            continue
        raw, exit_idx = res
        # SPY over the SAME calendar window: entry open date dates[j+2] -> exit date dates[exit_idx]
        se = spy_dmap.get(dates[j + 2])
        sx = spy_dmap.get(dates[exit_idx])
        if se is None or sx is None or spy_open[se] <= 0:
            continue
        spy_win = (spy_close[sx] / spy_open[se] - 1.0) * 100.0
        rows.append((dates[j + 2], raw, spy_win, raw - spy_win))

    a = np.array([r[1] for r in rows])
    spy = np.array([r[2] for r in rows])
    exc = np.array([r[3] for r in rows])
    print(f"n={len(rows)}  hold={HOLD}td  cost={COST}bps/side\n")
    print(f"RAW      EV {a.mean():+.3f}%   win {(a>0).mean()*100:.1f}%")
    print(f"SPY-win  EV {spy.mean():+.3f}%   (same calendar window, buy-and-hold SPY)")
    print(f"EXCESS   EV {exc.mean():+.3f}%   win {(exc>0).mean()*100:.1f}%   <-- the alpha question\n")

    # bootstrap 95% CI on excess (simple iid on entry-date-collapsed means)
    by_date: dict[str, list[float]] = {}
    for d, _, _, e in rows:
        by_date.setdefault(d, []).append(e)
    daily = np.array([np.mean(v) for v in by_date.values()])
    boot = np.array([np.mean(np.random.choice(daily, len(daily), replace=True)) for _ in range(5000)])
    print(f"excess 95% CI (entry-date clustered, {len(daily)} dates): "
          f"[{np.percentile(boot,2.5):+.3f}, {np.percentile(boot,97.5):+.3f}]\n")

    # OOS split
    for lo, hi, name in [("2010", "2018", "IS  2010-2017"), ("2018", "2027", "OOS 2018-2026")]:
        m = np.array([e for d, _, _, e in rows if lo <= d[:4] < hi])
        mr = np.array([raw for d, raw, _, _ in rows if lo <= d[:4] < hi])
        if len(m):
            print(f"{name}: n={len(m):4d}  RAW {mr.mean():+.3f}%  EXCESS {m.mean():+.3f}%  win {(m>0).mean()*100:.1f}%")


if __name__ == "__main__":
    main()
