"""Roadmap 1.11b: lead, lag, or coincident — the arrow-of-time scan.

Registered 2026-07-29 (FLOW_ROADMAP.md 1.11b). Discovery look, 1c: the whole
curve is recorded whatever it shows; no pass/fail. The reading rule (fixed in
the registration, not here): lag 0 is MECHANICAL and carries no authority;
"flow leads" needs positive-lag correlations nonzero by CI AND coherent across
adjacent lags — an isolated one-lag spike is pre-registered as noise.

Per lag k in -10..+10: tilt on session t is paired with the ONE-day excess
return vs SPY of session t+k (close of t+k-1 -> close of t+k), on SPY's
session calendar. Per session, the cross-name Spearman rank correlation
(>=30 qualifying names or the session is skipped and counted); the reported
number is the mean over sessions, with a 95% CI from resampling whole
sessions — ONE shared resampling across all lags per draw, so the curve
stays coherent.

Arms: fenced (primary — registered tilt, multileg excluded, index/ETF names
out), all (leg i), short_otm (leg ii). --floor 1000000 is leg iii;
--magnitude is leg iv (|tilt| vs |excess return|).

Positive lags from late-May sessions read June equity CLOSES only — the test
split's flow stays unmined (2.4 precedent, disclosed in the registration).
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from db.flow_corpus import INDEX_ROOTS  # noqa: E402
from tools.flow_tilt import tilt_rows  # noqa: E402
from tools.multileg_arm_probe import ETF_SET  # noqa: E402

TRAIN_END = "2026-05-31"
CORPUS_DB = ROOT / "data" / "flow_corpus.db"
FLOOR = 250_000                # registered panel floor; leg (iii) = 1,000,000
LAGS = list(range(-10, 11))
MIN_NAMES = 30
N_BOOT = 10_000
SEED = 20260730

ARMS = {
    # name -> (tilt_rows arm, exclude_multileg)
    "fenced": ("ALL", True),       # PRIMARY: registered tilt, package fence on
    "all": ("ALL", False),         # leg (i): what the fence changes
    "short_otm": ("SHORT_OTM", True),  # leg (ii): the academic prior's home
}


def _panel(arm: str, floor: float):
    """names, sessions, tilt (names x sessions), present — training, fenced universe."""
    tr_arm, ex_ml = ARMS[arm]
    d = tilt_rows(arm=tr_arm, exclude_multileg=ex_ml)
    fence = np.isin(d["underlying"], sorted(set(INDEX_ROOTS) | ETF_SET))
    keep = (d["date"] <= TRAIN_END) & (d["classified"] >= floor) & ~fence
    dates, unds, tilt_v = d["date"][keep], d["underlying"][keep], d["tilt"][keep]
    sessions = sorted(set(dates.tolist()))
    names = sorted(set(unds.tolist()))
    si = {s: j for j, s in enumerate(sessions)}
    ni = {n: i for i, n in enumerate(names)}
    tilt = np.zeros((len(names), len(sessions)))
    present = np.zeros(tilt.shape, bool)
    r = np.array([ni[u] for u in unds.tolist()])
    c = np.array([si[s] for s in dates.tolist()])
    tilt[r, c], present[r, c] = tilt_v, True
    return names, sessions, tilt, present


def _daily_excess(names: list[str]):
    """spy_dates, dr (names x spy_dates): one-day close-to-close excess return
    vs SPY per SPY-calendar date (NaN where the name lacks either bar).
    dr[:, t] is the return of date t relative to date t-1; column 0 is NaN."""
    con = sqlite3.connect(f"file:{CORPUS_DB}?mode=ro", uri=True)
    want = set(names) | {"SPY"}
    marks = ",".join("?" * len(want))
    rows = con.execute(
        f"SELECT symbol, date, c FROM equity_bars "
        f"WHERE adjustment='split' AND symbol IN ({marks}) ORDER BY date",
        sorted(want)).fetchall()
    con.close()
    spy = {d: c for s, d, c in rows if s == "SPY"}
    spy_dates = sorted(spy)
    di = {d: t for t, d in enumerate(spy_dates)}
    ni = {n: i for i, n in enumerate(names)}
    close = np.full((len(names), len(spy_dates)), np.nan)
    for s, d, c in rows:
        i, t = ni.get(s), di.get(d)
        if i is not None and t is not None:
            close[i, t] = c
    spy_c = np.array([spy[d] for d in spy_dates])
    with np.errstate(invalid="ignore", divide="ignore"):
        dr = close[:, 1:] / close[:, :-1] - 1.0 - (spy_c[1:] / spy_c[:-1] - 1.0)
    return spy_dates, np.concatenate(
        [np.full((len(names), 1), np.nan), dr], axis=1)


def _spearman(x: np.ndarray, y: np.ndarray) -> float:
    rx = np.argsort(np.argsort(x)).astype(float)
    ry = np.argsort(np.argsort(y)).astype(float)
    return float(np.corrcoef(rx, ry)[0, 1])


def _rho_matrix(tilt, present, sessions, spy_dates, dr, magnitude: bool):
    """rho (sessions x lags), NaN where <MIN_NAMES qualifying names."""
    di = {d: t for t, d in enumerate(spy_dates)}
    rho = np.full((len(sessions), len(LAGS)), np.nan)
    n_names = np.zeros_like(rho)
    for s_idx, s in enumerate(sessions):
        k = di.get(s)
        if k is None:
            continue
        pres = present[:, s_idx]
        tv = tilt[:, s_idx]
        for l_idx, lag in enumerate(LAGS):
            t = k + lag
            if t < 1 or t >= len(spy_dates):
                continue
            valid = pres & np.isfinite(dr[:, t])
            if valid.sum() < MIN_NAMES:
                continue
            a, b = tv[valid], dr[valid, t]
            if magnitude:
                a, b = np.abs(a), np.abs(b)
            rho[s_idx, l_idx] = _spearman(a, b)
            n_names[s_idx, l_idx] = valid.sum()
    return rho, n_names


def main() -> None:
    ap = argparse.ArgumentParser(description="1.11b lead/lag curve")
    ap.add_argument("--arm", default="fenced", choices=sorted(ARMS))
    ap.add_argument("--floor", type=float, default=FLOOR)
    ap.add_argument("--magnitude", action="store_true",
                    help="leg (iv): |tilt| vs |excess return|")
    args = ap.parse_args()

    names, sessions, tilt, present = _panel(args.arm, args.floor)
    n_nd = int(present.sum())
    print(f"=== 1.11b {'|tilt| vs |move|' if args.magnitude else 'tilt vs move'}"
          f" — arm {args.arm}, floor ${args.floor:,.0f} ===")
    print(f"panel: {len(sessions)} training sessions x {len(names)} names, "
          f"{n_nd:,} name-days (~{n_nd / len(sessions):.0f}/session)")

    spy_dates, dr = _daily_excess(names)
    rho, n_names = _rho_matrix(tilt, present, sessions, spy_dates, dr,
                               args.magnitude)

    rng = np.random.default_rng(SEED)
    S = len(sessions)
    draws = np.empty((N_BOOT, len(LAGS)))
    for b in range(N_BOOT):
        idx = rng.integers(0, S, size=S)        # one draw shared across lags
        draws[b] = np.nanmean(rho[idx], axis=0)
    lo, hi = np.nanpercentile(draws, [2.5, 97.5], axis=0)

    print(f"\n{'lag':>4} {'mean rho':>9} {'95% CI':>18} {'sessions':>9} "
          f"{'names/sess':>11}")
    for l_idx, lag in enumerate(LAGS):
        col = rho[:, l_idx]
        used = int(np.isfinite(col).sum())
        mn = np.nanmean(col) if used else float("nan")
        nm = n_names[:, l_idx][np.isfinite(col)].mean() if used else 0.0
        tag = "  <- lag 0 (mechanical, no authority)" if lag == 0 else ""
        print(f"{lag:>+4} {mn:>+9.4f} [{lo[l_idx]:>+7.4f}, {hi[l_idx]:>+7.4f}] "
              f"{used:>9} {nm:>11.0f}{tag}")
    skipped = S - int(np.isfinite(rho).any(axis=1).sum())
    print(f"\nsessions with no evaluable lag at all: {skipped} "
          f"(edge sessions lack full lag windows; counted, never silent)")


if __name__ == "__main__":
    main()
