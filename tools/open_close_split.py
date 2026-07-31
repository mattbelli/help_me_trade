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

--stage count --registered (Addendum G obligation 3b):
  The SAME counting stage, re-run on the REGISTERED pipeline — the v7 fence, the
  equity universe, the $250k classified floor, off the fenced per-order cache
  rather than the multileg-INCLUDED, unfenced, index/ETF-carrying rollup that
  2.4's census used (`open_close_split.py:68`). Two pins from G.10 #3b, so no
  choice is made at run time: the span is the one 2.4's census used
  (2025-07-28 → 2026-05-29) and ties in the max-per-quintile-min rule break
  toward the HIGHER quintile. Still outcome-blind — no forward return enters a
  census. 2.4's own recorded run keeps its exact comparator, untouched.
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

from tools.base_rate_table import (  # noqa: E402
    _build,
    _name_index,
    _trailing_quintiles,
    real_tilt,
    require_cache,
)
from tools.flow_tilt import tilt_rows  # noqa: E402
from tools.order_grain_null import (  # noqa: E402
    FENCED_OUT_DIR,
    LOOKBACK,
    PREMIUM_FLOOR,
    _fire,
    _sessions,
    classification,
)

TRAIN_END = "2026-05-31"          # test split (2026-06-01 ..) stays untouched
# G.10 #3b: the re-count runs on the same event span 2.4's census used.
CENSUS_START = "2025-07-28"
CENSUS_END = "2026-05-29"
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
                right: str | None = None, resolve=None):
    """(session, underlying) -> percentile of name-day f_open within that session.

    Optional restrictions implement robustness legs: `dte_buckets` for the
    DTE-5-89 leg, `right` ('C'/'P') for the same-side leg. The percentile is
    always taken within the correspondingly-restricted cross-section.

    `resolve(root, session)` re-keys the cross-section onto G.2's resolved
    identities (obligation 3b). The OI table stores the PRINTED root, so without
    it every renamed name-day would read as unmatched against a panel keyed by
    identity. Volume and volume-weighted f_open are summed per identity BEFORE
    the percentile, so a seam session cannot rank one identity twice.
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
    agg: dict[tuple[str, str], list[float]] = {}
    for s, u, v, wf in rows:
        a = agg.setdefault((s, resolve(u, s) if resolve else u), [0.0, 0.0])
        a[0] += v
        a[1] += wf
    by_s: dict[str, list[tuple[str, float]]] = {}
    for (s, u), (v, wf) in agg.items():
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


KEY_ORDER = ("max per-quintile MIN(open dates, close dates) -> max total events "
             "-> higher quintile")


def _select_tier(minima: list[int], totals: list[int], higher_wins: bool) -> int:
    """2.4's rule: max per-quintile MIN of the two census sides, ties by total events.

    **Key order, adjudicated 2026-07-31 and recorded verbatim as `KEY_ORDER`:**
    max per-quintile MIN -> max total events -> higher quintile. 2.4's registered
    rule already carried the total-events tie-break ("ties by total events,
    disclosed"), G.10 #3b inherits 2.4's FORM by name, and re-ordering the keys
    now would be exactly the post-hoc selection-rule edit G.10 forbids — so the
    higher-quintile pin applies to RESIDUAL ties, after total events.

    `higher_wins` is that pin. 2.4's own recorded run kept the lower tier, so the
    flag exists rather than a silent edit to a comparator whose result is already
    on the record.

    A census where every tier's MIN is zero selects nothing: `higher_wins` would
    otherwise hand back Q5 on an all-zero table and "confirm" the pre-stated Q5
    expectation out of a degenerate count.
    """
    if max(minima) == 0:
        raise SystemExit(
            "degenerate census: every quintile has 0 event dates on at least one "
            "side, so the registered rule selects nothing — check the cache span "
            "and the f_open join before recording a tier")
    best, best_key = 0, (-1, -1)
    for q in range(5):
        key = (minima[q], totals[q])
        if key > best_key or (higher_wins and key == best_key):
            best, best_key = q, key
    return best


def _report_census(names, sessions, ev, no_quint, higher_wins: bool,
                   label: str = "training window") -> dict:
    print(f"{label}: {sessions[0]} .. {sessions[-1]} "
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
    minima, totals = [], []
    for q in range(5):
        od, cd = len(dates[q][0]), len(dates[q][1])
        minima.append(min(od, cd))
        totals.append(int(counts[q].sum()))
        print(f"{QUINT_NAMES[q]:<16} {counts[q, 0]:>8,} {od:>8} "
              f"{counts[q, 1]:>9,} {cd:>9} {counts[q, 2]:>7,} "
              f"{counts[q, 3]:>10,} {min(od, cd):>7}")
    best = _select_tier(minima, totals, higher_wins)

    tot, unm = counts.sum(), counts[:, 3].sum()
    ranked = float(tot - unm) / max(int(tot), 1)
    print(f"\nf_open match coverage: {tot - unm:,}/{tot:,} events ranked "
          f"({100.0 * ranked:.1f}%) — unmatched = below the "
          f"{VOL_FLOOR:,}-contract floor or absent from the OI join")
    od, cd = len(dates[best][0]), len(dates[best][1])
    bar = "MEETS" if min(od, cd) >= 20 else "FAILS"
    # The applied key order is printed and stored, so the record says which rule
    # produced the tier rather than leaving a reader to infer it from the table.
    print(f"\nselection key order (adjudicated 2026-07-31): {KEY_ORDER}"
          f"{'' if higher_wins else ' [2.4 legacy: ties keep the LOWER quintile]'}")
    print(f"PRIMARY CELL by the registered rule: {QUINT_NAMES[best]} "
          f"(open {od} / close {cd} independent event dates; {bar} the "
          f">=20-per-side bar)")
    return {
        "window": [sessions[0], sessions[-1]], "sessions": len(sessions),
        "names": len(names), "events": len(ev) + no_quint,
        "events_no_quintile": no_quint,
        "key_order": KEY_ORDER, "higher_quintile_breaks_residual_ties": higher_wins,
        "open_events": counts[:, 0].tolist(), "close_events": counts[:, 1].tolist(),
        "middle_events": counts[:, 2].tolist(), "unmatched_events": counts[:, 3].tolist(),
        "open_dates": [len(dates[q][0]) for q in range(5)],
        "close_dates": [len(dates[q][1]) for q in range(5)],
        "min_dates": minima, "total_events": totals,
        "f_open_ranked_share": ranked,
        "tier": best, "tier_name": QUINT_NAMES[best],
        "tier_open_dates": od, "tier_close_dates": cd,
        "meets_20_per_side": bool(min(od, cd) >= 20),
    }


def stage_count() -> None:
    names, sessions, ev, no_quint = _events()
    _report_census(names, sessions, ev, no_quint, higher_wins=False)


def _cached_resolver(rc):
    """`resolve(root, session)`, memoised where memoising is provably safe.

    The OI cross-section is one row per (session, underlying), so a cache keyed
    on the PAIR would never hit — ~10^6 resolves at ~10^5 roots. A root with NO
    registered reuse-split boundary is date-invariant under `resolve()`
    (`_segment` returns (None, None) at every date, so every rename hop applies
    regardless), so its answer caches by root alone; a root WITH boundaries
    falls through to the real call every time.
    """
    cache: dict[str, str] = {}
    dated: set[str] = set()

    def _resolve(root: str, session: str) -> str:
        if root in dated:
            return rc.resolve(root, session)
        hit = cache.get(root)
        if hit is not None:
            return hit
        if rc.split_boundaries(root):
            dated.add(root)
            return rc.resolve(root, session)
        cache[root] = out = rc.resolve(root, session)
        return out

    return _resolve


def _registered_events(cache_dir: Path):
    """Obligation 3b's events: the registered pipeline over 2.4's census span."""
    rc = classification()
    panel = [s for s in _sessions(0) if CENSUS_START <= s.isoformat() <= CENSUS_END]
    if not panel:
        raise SystemExit(f"no sessions on the census span {CENSUS_START}..{CENSUS_END}")
    files = [cache_dir / f"{s.isoformat()}.npz" for s in panel]
    require_cache(files, cache_dir)
    names, ni = _name_index(files)
    # draws=0: the census needs the real panel only, never a null draw.
    P, _Q, B, _T = _build(files, len(names), ni, draws=0)
    prem = P["ALL"]
    tilt = real_tilt(prem, B["ALL"])
    fired, _tested = _fire(tilt, prem > 0, prem >= PREMIUM_FLOOR)
    quint = _trailing_quintiles(prem)
    pct = _f_open_pct(resolve=_cached_resolver(rc))
    sessions = [s.isoformat() for s in panel]
    ev, no_quint = [], 0
    for i, j in zip(*(a.tolist() for a in np.nonzero(fired))):
        q = int(quint[i, j])
        if q < 0:
            no_quint += 1
            continue
        ev.append((names[i], sessions[j], q, float(tilt[i, j]),
                   pct.get((sessions[j], names[i]))))
    return list(names), sessions, ev, no_quint


def stage_count_registered(cache_dir: Path = FENCED_OUT_DIR) -> dict:
    """G.10 obligation 3b — the 2.4 counting stage on the registered pipeline."""
    print("\n=== obligation 3b — 2.4 census RE-COUNT on the registered pipeline "
          "(v7 fence, equity universe, $250k classified floor) ===")
    print("=== outcome-blind: no forward return enters a census ===")
    names, sessions, ev, no_quint = _registered_events(cache_dir)
    out = _report_census(names, sessions, ev, no_quint, higher_wins=True,
                         label="census span")
    out["cache_dir"] = str(cache_dir)
    out["census_span"] = [CENSUS_START, CENSUS_END]
    return out


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


def _signed_alpha(ev, bars, horizon: int = HORIZON) -> tuple[list, int]:
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
        if b is None or k is None or k + horizon >= len(spy_d):
            skipped += 1
            continue
        dts, h, low, c = b
        di = {d: m for m, d in enumerate(dts)}
        m0, m1 = di.get(s), di.get(spy_d[k + horizon])
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
        alpha = (c[m1] / c[m0] - 1.0) - (spy_c[k + horizon] / spy_c[k] - 1.0)
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


def stage_horizons() -> None:
    """2.4 Addendum A: exploratory horizon curve on the primary cell.

    Registered 2026-07-29 BEFORE running, AFTER the 10d verdict was seen —
    therefore report-only with NO authority over 2.4's FAIL. Anything found
    here is a hypothesis for fresh data, never a retro-pass.
    """
    _names, _sessions, ev, _nq = _events()
    bars = _bars({u for u, *_ in ev} | {"SPY"})
    print("=== 2.4 ADDENDUM A — exploratory horizon curve, primary cell (Q5) ===")
    print("=== NO AUTHORITY over the registered 10d verdict ===")
    for hz in (1, 2, 5, HORIZON):
        graded, _sk = _signed_alpha(ev, bars, horizon=hz)
        cell = [(s, p, a) for _u, s, q, p, a in graded
                if q == PRIMARY_CELL and p is not None]
        tag = " (the registered verdict)" if hz == HORIZON else ""
        _fmt_cell(f"{hz:>2}d{tag}", _diff_ci(cell))


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
    ap.add_argument("--stage", choices=("count", "grade", "horizons"),
                    default="count")
    ap.add_argument("--registered", action="store_true",
                    help="obligation 3b: re-count on the registered pipeline")
    ap.add_argument("--cache-dir", type=Path, default=FENCED_OUT_DIR)
    args = ap.parse_args()
    if args.registered:
        if args.stage != "count":
            raise SystemExit("--registered applies to the counting stage only")
        stage_count_registered(args.cache_dir)
        return
    {"count": stage_count, "grade": stage_grade,
     "horizons": stage_horizons}[args.stage]()


if __name__ == "__main__":
    main()
