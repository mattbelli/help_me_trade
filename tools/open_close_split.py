"""Roadmap 2.4: the open-heavy vs close-heavy split of Phase-1 events.

Registered 2026-07-29 (FLOW_ROADMAP.md 2.4). Two stages, run in order:

--stage count (OUTCOME-BLIND — the default):
  - extracts every Phase-1 event (registered tilt, |z| >= 2.0, 20-day baseline,
    >=15-of-20 presence, $250k floor) in the TRAINING window only,
  - splits each event name-day by f_open's percentile within that session's
    >=1,000-contract cross-section (top third open-heavy, bottom third
    close-heavy, middle third set aside),
  - counts events and INDEPENDENT EVENT DATES per liquidity quintile per side,
  - applies the registered outcome-blind primary-cell rule (max per-quintile
    min(open_dates, close_dates); ties by total events, disclosed).
  No outcome is computed or read in this stage.

--stage grade (run only after the primary cell is declared in the roadmap):
  - outcome = sign(event tilt) x (name return - SPY return) over the 10
    trading days AFTER the event session, close-to-close, in 14-day-ATR units
    (ATR choice disclosed in the declaration BEFORE outcomes were seen; it
    scales both sides of every comparison identically),
  - PRIMARY: median signed alpha, open-heavy minus close-heavy, in the
    declared cell; 95% CI from a date-resampling bootstrap (whole event dates
    drawn with replacement, both sides recomputed per draw — 1.18's clustering
    logic), 10,000 draws, seeded,
  - exploratory cells and the five registered report-only robustness legs.

Events that cannot be ranked (below the volume floor or absent from the OI
join) are REPORTED as unmatched, never silently dropped. Forward windows of
late-training events extend past TRAIN_END on the calendar; that reads equity
CLOSES from those dates, not flow events — the test split's flow data stays
unmined (disclosed in the roadmap record).
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import date
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tools.base_rate_table import _trailing_quintiles  # noqa: E402
from tools.flow_tilt import tilt_rows  # noqa: E402
from tools.order_grain_null import LOOKBACK, PREMIUM_FLOOR, _fire  # noqa: E402

TRAIN_END = "2026-05-31"          # test split (2026-06-01 ..) stays untouched
CORPUS_DB = ROOT / "data" / "flow_corpus.db"
OI_DB = ROOT / "data" / "oi_reconcile.db"
VOL_FLOOR = 1_000                 # same cross-section as the 07-29 pre-check
TERCILE_LO = 100.0 / 3.0
TERCILE_HI = 200.0 / 3.0
HORIZON = 10                      # 1.17 horizon of record, trading days
ATR_N = 14
N_BOOT = 10_000
SEED = 20260729
PRIMARY_CELL = 4                  # Q5 — declared in the roadmap 2026-07-29
QUINT_NAMES = ["Q1 least liquid", "Q2", "Q3", "Q4", "Q5 most liquid"]
DTE_589 = ("5-29", "30-89")


def _panels():
    """(names x sessions) tilt / presence / classified-premium panels, training only."""
    d = tilt_rows()  # ALL arm, multileg included — the 1.7 registered application
    keep = d["date"] <= TRAIN_END
    dates, unds = d["date"][keep], d["underlying"][keep]
    tilt_v, prem_v = d["tilt"][keep], d["classified"][keep]
    sessions = sorted(set(dates.tolist()))
    names = sorted(set(unds.tolist()))
    si = {s: j for j, s in enumerate(sessions)}
    ni = {n: i for i, n in enumerate(names)}
    tilt = np.zeros((len(names), len(sessions)))
    prem = np.zeros_like(tilt)
    present = np.zeros(tilt.shape, bool)
    r = np.array([ni[u] for u in unds.tolist()])
    c = np.array([si[s] for s in dates.tolist()])
    tilt[r, c], prem[r, c], present[r, c] = tilt_v, prem_v, True
    return names, sessions, tilt, prem, present


def _f_open_pct(dte_buckets: tuple[str, ...] | None = None,
                right: str | None = None):
    """(session, underlying) -> percentile of name-day f_open within that session.

    Optional restrictions implement robustness legs: `dte_buckets` for the
    DTE-5-89 leg, `right` ('C'/'P') for the same-side leg. The percentile is
    always taken within the correspondingly-restricted cross-section.
    """
    where = "volume > 0 AND vol_weighted_f_open IS NOT NULL"
    if dte_buckets:
        where += f" AND dte_bucket IN {dte_buckets!r}"
    if right:
        where += f" AND right = '{right}'"
    con = sqlite3.connect(f"file:{OI_DB}?mode=ro", uri=True)
    rows = con.execute(
        "SELECT session, underlying, SUM(volume), "
        "SUM(volume * vol_weighted_f_open) "
        f"FROM oi_reconcile_rollup WHERE {where} GROUP BY session, underlying"
    ).fetchall()
    con.close()
    by_s: dict[str, list[tuple[str, float]]] = {}
    for s, u, v, wf in rows:
        if v >= VOL_FLOOR:
            by_s.setdefault(s, []).append((u, wf / v))
    pct: dict[tuple[str, str], float] = {}
    for s, pairs in by_s.items():
        f = np.asarray([p[1] for p in pairs])
        rank = np.argsort(np.argsort(f)).astype(float)
        for (u, _), p in zip(pairs, 100.0 * rank / max(len(f) - 1, 1)):
            pct[(s, u)] = p
    return pct


def _events():
    """Fired events with quintile + f_open percentile. Shared by both stages."""
    names, sessions, tilt, prem, present = _panels()
    fired, _ = _fire(tilt, present, prem >= PREMIUM_FLOOR)
    quint = _trailing_quintiles(prem)
    pct = _f_open_pct()
    ev = []
    no_quint = 0
    for i, j in zip(*(a.tolist() for a in np.nonzero(fired))):
        q = int(quint[i, j])
        if q < 0:
            no_quint += 1
            continue
        ev.append((names[i], sessions[j], q, float(tilt[i, j]),
                   pct.get((sessions[j], names[i]))))
    return names, sessions, ev, no_quint


def _bucket(p: float | None, lo: float = TERCILE_LO, hi: float = TERCILE_HI) -> int:
    """0=open-heavy 1=close-heavy 2=middle 3=unmatched."""
    return 3 if p is None else 0 if p >= hi else 1 if p <= lo else 2


def stage_count() -> None:
    names, sessions, ev, no_quint = _events()
    print(f"training window: {sessions[0]} .. {sessions[-1]} "
          f"({len(sessions)} sessions, {len(names)} names; "
          f"events fire from session {LOOKBACK + 1})")
    print(f"events: {len(ev) + no_quint:,} on "
          f"{len({s for _, s, *_ in ev})} distinct sessions")
    print(f"events with no quintile (no trailing history): {no_quint}\n")

    counts = np.zeros((5, 4), np.int64)
    dates: list[list[set]] = [[set() for _ in range(4)] for _ in range(5)]
    for _u, s, q, _t, p in ev:
        b = _bucket(p)
        counts[q, b] += 1
        dates[q][b].add(s)

    print(f"{'quintile':<16} {'open ev':>8} {'open dt':>8} {'close ev':>9} "
          f"{'close dt':>9} {'mid ev':>7} {'unmatched':>10} {'min dt':>7}")
    best, best_key = None, (-1, -1)
    for q in range(5):
        od, cd = len(dates[q][0]), len(dates[q][1])
        key = (min(od, cd), int(counts[q].sum()))
        if key > best_key:
            best, best_key = q, key
        print(f"{QUINT_NAMES[q]:<16} {counts[q, 0]:>8,} {od:>8} "
              f"{counts[q, 1]:>9,} {cd:>9} {counts[q, 2]:>7,} "
              f"{counts[q, 3]:>10,} {min(od, cd):>7}")

    tot, unm = counts.sum(), counts[:, 3].sum()
    print(f"\nf_open match coverage: {tot - unm:,}/{tot:,} events ranked "
          f"({100.0 * (tot - unm) / max(tot, 1):.1f}%) — unmatched = below the "
          f"{VOL_FLOOR:,}-contract floor or absent from the OI join")
    od, cd = len(dates[best][0]), len(dates[best][1])
    bar = "MEETS" if min(od, cd) >= 20 else "FAILS"
    print(f"\nPRIMARY CELL by the registered rule: {QUINT_NAMES[best]} "
          f"(open {od} / close {cd} independent event dates; {bar} the "
          f">=20-per-side bar)")


# ---------------------------------------------------------------- stage 2

def _bars(symbols: set[str]):
    """symbol -> (date array, o/h/l/c arrays), split-adjusted closes."""
    con = sqlite3.connect(f"file:{CORPUS_DB}?mode=ro", uri=True)
    marks = ",".join("?" * len(symbols))
    rows = con.execute(
        f"SELECT symbol, date, h, l, c FROM equity_bars "
        f"WHERE adjustment='split' AND symbol IN ({marks}) ORDER BY symbol, date",
        sorted(symbols)).fetchall()
    con.close()
    out: dict[str, tuple] = {}
    cur, dts, hs, ls, cs = None, [], [], [], []
    for sym, dt, h, low, c in rows + [(None, None, None, None, None)]:
        if sym != cur:
            if cur is not None:
                out[cur] = (dts, np.array(hs), np.array(ls), np.array(cs))
            cur, dts, hs, ls, cs = sym, [], [], [], []
        if sym is not None:
            dts.append(dt)
            hs.append(h)
            ls.append(low)
            cs.append(c)
    return out


def _signed_alpha(ev, bars) -> tuple[list, int]:
    """Per event: (und, session, quint, pct, signed alpha in ATR units).

    Skips (and counts) events missing bars at either endpoint or with an
    incomplete ATR window — reported, never silent.
    """
    spy_d, _h, _l, spy_c = bars["SPY"]
    spy_i = {d: k for k, d in enumerate(spy_d)}
    graded, skipped = [], 0
    for und, s, q, tilt, p in ev:
        b = bars.get(und)
        k = spy_i.get(s)
        if b is None or k is None or k + HORIZON >= len(spy_d):
            skipped += 1
            continue
        dts, h, low, c = b
        di = {d: m for m, d in enumerate(dts)}
        m0, m1 = di.get(s), di.get(spy_d[k + HORIZON])
        if m0 is None or m1 is None or m0 < ATR_N:
            skipped += 1
            continue
        tr = np.maximum(h[m0 - ATR_N + 1:m0 + 1] - low[m0 - ATR_N + 1:m0 + 1],
                        np.maximum(np.abs(h[m0 - ATR_N + 1:m0 + 1] - c[m0 - ATR_N:m0]),
                                   np.abs(low[m0 - ATR_N + 1:m0 + 1] - c[m0 - ATR_N:m0])))
        atr = float(tr.mean())
        if atr <= 0:
            skipped += 1
            continue
        alpha = (c[m1] / c[m0] - 1.0) - (spy_c[k + HORIZON] / spy_c[k] - 1.0)
        graded.append((und, s, q, p, np.sign(tilt) * alpha * c[m0] / atr))
    return graded, skipped


def _diff_ci(rows, lo=TERCILE_LO, hi=TERCILE_HI):
    """Median signed alpha open minus close + date-bootstrap 95% CI.

    rows: (session, pct, signed_alpha). Bootstrap resamples whole event DATES
    with replacement; each draw recomputes both sides' medians.
    """
    op = [a for s, p, a in rows if _bucket(p, lo, hi) == 0]
    cl = [a for s, p, a in rows if _bucket(p, lo, hi) == 1]
    if not op or not cl:
        return None
    point = float(np.median(op) - np.median(cl))
    by_date: dict[str, tuple[list, list]] = {}
    for s, p, a in rows:
        b = _bucket(p, lo, hi)
        if b in (0, 1):
            by_date.setdefault(s, ([], []))[b].append(a)
    dts = sorted(by_date)
    rng = np.random.default_rng(SEED)
    diffs = []
    for _ in range(N_BOOT):
        o_s, c_s = [], []
        for d in rng.choice(len(dts), size=len(dts), replace=True):
            o, c = by_date[dts[d]]
            o_s += o
            c_s += c
        if o_s and c_s:
            diffs.append(np.median(o_s) - np.median(c_s))
    lo_ci, hi_ci = np.percentile(diffs, [2.5, 97.5])
    o_dt = len({s for s, p, a in rows if _bucket(p, lo, hi) == 0})
    c_dt = len({s for s, p, a in rows if _bucket(p, lo, hi) == 1})
    return point, float(lo_ci), float(hi_ci), len(op), len(cl), o_dt, c_dt


def _fmt_cell(label, r, verdict=False) -> None:
    if r is None:
        print(f"  {label}: one side empty — no comparison")
        return
    point, lo, hi, n_o, n_c, d_o, d_c = r
    line = (f"  {label}: open-close = {point:+.3f} ATR  [{lo:+.3f}, {hi:+.3f}]  "
            f"(open n={n_o}/{d_o}dt, close n={n_c}/{d_c}dt)")
    if verdict:
        if min(d_o, d_c) < 20:
            v = "UNEVALUABLE (<20 event dates on a side)"
        elif lo > 0:
            v = "PASS"
        elif hi < 0:
            v = "FAIL (wrong direction, CI excludes zero)"
        else:
            v = "FAIL (CI includes zero)"
        line += f"  => {v}"
    print(line)


def _third_fridays(sessions: list[str]) -> set[str]:
    out = set()
    for s in sessions:
        y, m, d = map(int, s.split("-"))
        dt = date(y, m, d)
        if dt.weekday() == 4 and 15 <= d <= 21:
            out.add(s)
    return out


def stage_grade() -> None:
    names, sessions, ev, _nq = _events()
    bars = _bars({u for u, *_ in ev} | {"SPY"})
    graded, skipped = _signed_alpha(ev, bars)
    print(f"graded {len(graded):,}/{len(ev):,} events "
          f"({skipped} skipped: missing bars or ATR window — reported, not silent)")

    cell = [(s, p, a) for _u, s, q, p, a in graded
            if q == PRIMARY_CELL and p is not None]
    print(f"\n=== PRIMARY — {QUINT_NAMES[PRIMARY_CELL]}, terciles 33.3/66.7, "
          f"{HORIZON}d signed alpha vs SPY in ATR({ATR_N}) units ===")
    _fmt_cell("primary", _diff_ci(cell), verdict=True)

    print("\n=== exploratory cells (labelled, no authority) ===")
    for q in (2, 3):
        rows = [(s, p, a) for _u, s, qq, p, a in graded if qq == q and p is not None]
        _fmt_cell(QUINT_NAMES[q], _diff_ci(rows))

    print("\n=== robustness legs (report-only, no authority) ===")
    # (iii) tercile edges +/-5pt
    _fmt_cell("(iii) cut 28.3/61.7", _diff_ci(cell, TERCILE_LO - 5, TERCILE_HI - 5))
    _fmt_cell("(iii) cut 38.3/71.7", _diff_ci(cell, TERCILE_LO + 5, TERCILE_HI + 5))
    # (ii) drop monthly-expiry sessions
    tf = _third_fridays(sessions)
    _fmt_cell("(ii) ex-expiry", _diff_ci([r for r in cell if r[0] not in tf]))
    # (i) f_open from DTE 5-89 only
    pct589 = _f_open_pct(dte_buckets=DTE_589)
    cell589 = [(s, pct589.get((s, u)), a) for u, s, q, _p, a in graded
               if q == PRIMARY_CELL and pct589.get((s, u)) is not None]
    _fmt_cell("(i) DTE 5-89", _diff_ci(cell589))
    # (iv) dose-response gradient across f_open rank quintiles
    print("  (iv) dose gradient (median signed alpha by f_open rank bucket):")
    a = np.array([x for _s, _p, x in cell])
    p = np.array([x for _s, x, _a in cell])
    for k in range(5):
        m = (p >= 20 * k) & (p < 20 * (k + 1) + (5 if k == 4 else 0))
        med = np.median(a[m]) if m.any() else float("nan")
        print(f"      pct {20*k:>3}-{20*(k+1):>3}: n={int(m.sum()):>5}  "
              f"median={med:+.3f} ATR")
    # (v) same-side f_open: dominant right per event
    dom = _dominant_right()
    out = []
    pc = {r: _f_open_pct(right=r) for r in ("C", "P")}
    for u, s, q, _p, a in graded:
        if q != PRIMARY_CELL:
            continue
        r = dom.get((s, u))
        if r is None:
            continue
        pp = pc[r].get((s, u))
        if pp is not None:
            out.append((s, pp, a))
    _fmt_cell("(v) same-side f_open", _diff_ci(out))


def _dominant_right() -> dict[tuple[str, str], str]:
    """(session, underlying) -> right carrying the majority of classified premium."""
    con = sqlite3.connect(f"file:{CORPUS_DB}?mode=ro", uri=True)
    rows = con.execute(
        "SELECT date, underlying, "
        "SUM(CASE WHEN right='C' THEN premium_usd ELSE 0 END), "
        "SUM(CASE WHEN right='P' THEN premium_usd ELSE 0 END) "
        "FROM flow_rollup WHERE side IN ('buy','sell') AND date <= ? "
        "GROUP BY date, underlying", (TRAIN_END,)).fetchall()
    con.close()
    return {(s, u): ("C" if c >= p else "P") for s, u, c, p in rows}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stage", choices=("count", "grade"), default="count")
    args = ap.parse_args()
    (stage_count if args.stage == "count" else stage_grade)()


if __name__ == "__main__":
    main()
