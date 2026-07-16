"""earnings_drift extra-day experiment — brief: References/EARNINGS_DRIFT_EXTRA_DAY_EXPERIMENT.md.

Four PRE-REGISTERED arms (all run, all reported; n_trials=4):
  A  control — exit at jn-1 (the validated baseline)
  B  extra day when EDGAR session == amc
  C  as B, restricted to corroborated amc (filing accepted 16:00-17:30 ET — the
     tight-window option from the brief; filing lag rarely lands there from a BMO release)
  D  extra day on EVERYTHING — deliberately wrong NEGATIVE CONTROL (holds bmo
     reporters through their print; must look visibly worse or the sim is broken)

Session labels derive from the RAW 8-K acceptanceDateTime via the SAME rule live
uses (signals.earnings_edgar.session_hour). EDGAR labels are directionally biased
(~7% of "amc" are late-filed BMO releases -> their extra day contains a real
earnings gap). That contamination is CONSERVATIVE for arm B: the measured value
is a FLOOR vs live (which uses Finnhub's clean forward hour).

The setup loop mirrors research/earnings_strategy.py::_symbol_setups exactly
(same guards, same 0.20% net cost, no extra round trip for the extra day); arm A
is asserted against the stored dataset as the equivalence check.

Extra-day guards (correctness, per brief):
  1. only when bars[jn]['d'] == e_next exactly (holiday misalignment would sell
     AFTER the report) — else fall back to jn-1;
  2. existing bounds guard unchanged.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from datetime import time as dtime
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np

from research.earnings_strategy import _blowout_reaction, _idx_of, _load_all_bars
from research.significance import significance_report
from signals.earnings_edgar import session_hour

ROOT = Path(__file__).resolve().parents[2]
EARNINGS_DB = ROOT / "data" / "research" / "earnings.db"
BARS_DB = ROOT / "data" / "research" / "bars.db"
LABELED_DB = ROOT / "data" / "research" / "earnings_labeled.db"
SOURCE = "norgate"

BLOWOUT_PCT = 5.0
ENTRY_LEAD_TD = 10
EXIT_LEAD_TD = 1
MIN_GAP_TD = 40
MAX_GAP_TD = 90
COST_PCT = 0.20

_ET = ZoneInfo("America/New_York")
_CORROBORATION_WINDOW = (dtime(16, 0), dtime(17, 30))


def _et_time(accepted: str | None) -> dtime | None:
    if not accepted:
        return None
    try:
        dt = datetime.fromisoformat(str(accepted).strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(_ET).time()


def load_earnings_with_sessions() -> dict[str, list[tuple[str, str | None, bool]]]:
    """symbol -> [(date, session, corroborated)] sorted by date."""
    conn = sqlite3.connect(EARNINGS_DB)
    try:
        rows = conn.execute(
            "SELECT symbol, date, accepted_at FROM earnings ORDER BY symbol, date"
        ).fetchall()
    finally:
        conn.close()
    out: dict[str, list[tuple[str, str | None, bool]]] = {}
    for sym, d, accepted in rows:
        session = session_hour(accepted)
        t = _et_time(accepted)
        corroborated = (
            session == "amc"
            and t is not None
            and _CORROBORATION_WINDOW[0] <= t < _CORROBORATION_WINDOW[1]
        )
        out.setdefault(sym, []).append((d, session, corroborated))
    return out


def build_setups() -> list[dict]:
    """One record per setup with both exit variants + session metadata."""
    earnings_by_sym = load_earnings_with_sessions()
    setups: list[dict] = []
    for sym, events in earnings_by_sym.items():
        bars = _load_all_bars(sym, source=SOURCE, db_path=BARS_DB)
        if len(bars) < 3 or len(events) < 2:
            continue
        bar_dates = [b["d"] for b in bars]
        closes = [float(b["c"]) for b in bars]
        opens = [float(b["o"]) for b in bars]

        for i in range(len(events) - 1):
            e_cur, _, _ = events[i]
            e_next, session, corroborated = events[i + 1]
            j = _idx_of(bar_dates, e_cur)
            if j is None:
                continue
            reaction = _blowout_reaction(closes, j)
            if reaction is None or reaction * 100.0 < BLOWOUT_PCT:
                continue
            jn = _idx_of(bar_dates, e_next)
            if jn is None:
                continue
            gap_td = jn - j
            if gap_td < MIN_GAP_TD or gap_td > MAX_GAP_TD:
                continue
            entry_idx = jn - ENTRY_LEAD_TD
            exit_idx = jn - EXIT_LEAD_TD
            if entry_idx <= j or entry_idx < 0 or exit_idx <= entry_idx or exit_idx >= len(bars):
                continue
            entry_close = closes[entry_idx]
            if entry_close <= 0:
                continue

            ret_base = (closes[exit_idx] / entry_close - 1.0) * 100.0 - COST_PCT
            aligned = bar_dates[jn] == e_next  # guard 1: holiday misalignment
            if aligned:
                ret_extra = (closes[jn] / entry_close - 1.0) * 100.0 - COST_PCT
                xday_pct = (closes[jn] / closes[jn - 1] - 1.0) * 100.0
                # Robustness cut (not a registered arm): exit at day-D OPEN
                # (market-on-open) — overnight drift only, zero same-session
                # pre-print exposure.
                ret_extra_open = (
                    ((opens[jn] / entry_close - 1.0) * 100.0 - COST_PCT)
                    if opens[jn] > 0
                    else ret_base
                )
            else:
                ret_extra = ret_base
                ret_extra_open = ret_base
                xday_pct = None

            setups.append(
                {
                    "symbol": sym,
                    "entry_date": bar_dates[entry_idx],
                    "session": session,
                    "corroborated": corroborated,
                    "aligned": aligned,
                    "ret_base": ret_base,
                    "ret_extra": ret_extra,
                    "ret_extra_open": ret_extra_open,
                    "xday_pct": xday_pct,
                }
            )
    return setups


def arm_returns(setups: list[dict], arm: str) -> list[float]:
    out = []
    for s in setups:
        field = "ret_extra"
        if arm == "A":
            take_extra = False
        elif arm == "B":
            take_extra = s["session"] == "amc"
        elif arm == "B_open":  # robustness cut, not a registered arm
            take_extra = s["session"] == "amc"
            field = "ret_extra_open"
        elif arm == "C":
            take_extra = s["corroborated"]
        elif arm == "D":
            take_extra = True
        else:
            raise ValueError(arm)
        out.append(s[field] if take_extra and s["aligned"] else s["ret_base"])
    return out


def describe(rets: list[float]) -> str:
    a = np.array(rets)
    win = (a > 0).mean() * 100.0
    return (
        f"n={len(a):>5}  mean {a.mean():+.3f}%  median {np.median(a):+.3f}%  "
        f"win {win:.1f}%"
    )


def main() -> None:
    setups = build_setups()
    dates = [s["entry_date"] for s in setups]
    n_aligned = sum(s["aligned"] for s in setups)

    # Equivalence check: arm A must reproduce the stored validated dataset.
    conn = sqlite3.connect(LABELED_DB)
    n_stored, mean_stored = conn.execute(
        "SELECT COUNT(*), AVG(blended_return_pct) FROM strategy_setups WHERE source=?",
        (SOURCE,),
    ).fetchone()
    conn.close()
    a = arm_returns(setups, "A")
    print(
        f"EQUIVALENCE: arm A n={len(a)} mean={np.mean(a):+.4f}% "
        f"vs stored n={n_stored} mean={mean_stored:+.4f}% "
        f"({'MATCH' if len(a) == n_stored else 'DRIFT - explain before trusting'})"
    )

    by_session: dict[str | None, int] = {}
    for s in setups:
        by_session[s["session"]] = by_session.get(s["session"], 0) + 1
    n_corr = sum(s["corroborated"] for s in setups)
    print(f"\nsession coverage on {len(setups)} setups (aligned next-earnings day: {n_aligned}):")
    print(f"  {by_session}  corroborated-amc: {n_corr}")

    print("\n--- Arms (all pre-registered, all reported; per-trade net returns) ---")
    arms = {arm: arm_returns(setups, arm) for arm in "ABCD"}
    for arm, rets in arms.items():
        n_changed = sum(1 for x, y in zip(rets, arms["A"], strict=True) if x != y)
        print(f"arm {arm}: {describe(rets)}  trades-with-extra-day={n_changed}")

    def _ci(rep: dict) -> str:
        lo, hi = rep["block_ci"]
        if lo is None:
            return "CI n/a"
        return f"CI [{lo:+.4f}, {hi:+.4f}] p<=0 {rep['p_le_zero']:.4f}"

    arms["B_open"] = arm_returns(setups, "B_open")
    print(f"arm B_open (robustness cut — exit day-D OPEN): {describe(arms['B_open'])}")

    for arm in ("B", "B_open", "C", "D"):
        deltas = [x - y for x, y in zip(arms[arm], arms["A"], strict=True)]
        rep = significance_report(dates, deltas, block_size=10)
        sub = [(d, x) for d, x in zip(dates, deltas, strict=True) if x != 0.0]
        print(f"\narm {arm} minus A: mean delta {np.mean(deltas):+.4f}%/trade, {_ci(rep)}")
        if sub:
            sd, sx = [d for d, _ in sub], [x for _, x in sub]
            rep_c = significance_report(sd, sx, block_size=10)
            print(
                f"          conditional (extra-day trades only, n={len(sx)}): "
                f"{np.mean(sx):+.4f}%/trade, {_ci(rep_c)}"
            )

    print("\n--- Task 4: extra-day return close[jn]/close[jn-1]-1 by EDGAR label ---")
    print("(bmo bucket IS a post-report day by construction — the gap-day reference;")
    print(" a clean amc bucket should look nothing like it)")
    print(f"{'label':<9} {'n':>6} {'mean':>8} {'median':>8} {'stdev':>7} {'p1':>8} {'p99':>8}")
    for label in ("amc", "bmo", "dmh", None):
        vals = np.array(
            [s["xday_pct"] for s in setups if s["session"] == label and s["xday_pct"] is not None]
        )
        if len(vals) == 0:
            continue
        name = label if label else "unknown"
        print(
            f"{name:<9} {len(vals):>6} {vals.mean():>+7.3f}% {np.median(vals):>+7.3f}% "
            f"{vals.std():>6.2f} {np.percentile(vals, 1):>+7.2f}% {np.percentile(vals, 99):>+7.2f}%"
        )


if __name__ == "__main__":
    main()
