"""Covid-dependence + dynamic-rebalance study on the earnings-drift trade list.

Q1 (gates everything): is breadth's drawdown advantage one sidestepped month in
2020? -> top drawdown episodes per config + full metrics with calendar-2020
spliced out of the daily return series.

Q2: the founder's rule — take every signal, equal-weight all open positions to
~full deployment (per-position cap 10%), rebalancing existing positions on
every entry/exit event — with explicit one-way churn cost on every adjustment.

MARKING CONVENTION (approximation, stated): we have no intra-trade price path
(only entry_date/holding_days/net return), so each trade's return accrues
LINEARLY over its holding window. Both the fixed-fraction baselines and the
rebalanced sims below use this same accrual marking, so the comparison is
internally consistent; intra-trade path risk (a trade down 8% mid-window that
recovers) is invisible to ALL configs equally. blended_return_pct is stored
net of the 0.20% build-time round-trip cost; rebalance churn is charged extra
on resize deltas only (initial entry / final exit are covered by the baked
cost).
"""

from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path

import numpy as np

from research.portfolio import _max_drawdown, _sharpe

ROOT = Path(__file__).resolve().parents[2]
DB = ROOT / "data" / "research" / "earnings_labeled.db"
SOURCE = "norgate"
START_EQUITY = 100_000.0


def load_trades() -> list[tuple[int, int, float]]:
    conn = sqlite3.connect(DB)
    try:
        rows = conn.execute(
            """
            SELECT entry_date, holding_days, blended_return_pct
            FROM strategy_setups WHERE source = ? ORDER BY entry_date ASC
            """,
            (SOURCE,),
        ).fetchall()
    finally:
        conn.close()
    out = []
    for d, h, r in rows:
        h = max(1, int(h))
        out.append((date.fromisoformat(d).toordinal(), h, float(r) / 100.0))
    return out


class _Pos:
    __slots__ = ("entry", "exit", "ret_frac", "hold", "notional")

    def __init__(self, entry: int, hold: int, ret_frac: float):
        self.entry = entry
        self.exit = entry + hold
        self.ret_frac = ret_frac
        self.hold = hold
        self.notional = 0.0


def simulate(
    trades: list[tuple[int, int, float]],
    *,
    mode: str,  # "fixed" | "rebalance"
    per_trade_fraction: float = 0.05,
    max_concurrent: int = 50,
    cap: float = 0.10,
    deploy: float = 0.95,
    churn_oneway_pct: float = 0.05,  # % of traded notional per adjustment
    band: float = 0.0,  # skip resizes within +/- band (relative to target)
) -> dict:
    entries_by_day: dict[int, list[tuple[int, float]]] = {}
    for entry, hold, ret in trades:
        entries_by_day.setdefault(entry, []).append((hold, ret))
    first = min(e for e, _, _ in trades)
    last = max(e + h for e, h, _ in trades)

    equity = START_EQUITY
    open_pos: list[_Pos] = []
    curve: list[tuple[int, float]] = []
    n_taken = n_skipped = 0
    churn_paid = 0.0
    traded_rebal = 0.0
    invested_sum = 0.0

    for d in range(first, last + 1):
        # All sizing below uses start-of-day equity: today's model-mark accrual
        # is not observable when placing today's orders (quant-review finding).
        equity_sod = equity
        for p in open_pos:
            if p.entry < d <= p.exit:
                equity += p.notional * p.ret_frac / p.hold
        event = False
        still = []
        for p in open_pos:
            if p.exit <= d:
                event = True
            else:
                still.append(p)
        open_pos = still

        for hold, ret in entries_by_day.get(d, ()):
            if mode == "fixed":
                deployed = sum(p.notional for p in open_pos)
                size = per_trade_fraction * equity_sod
                if len(open_pos) < max_concurrent and deployed + size <= equity_sod + 1e-9:
                    p = _Pos(d, hold, ret)
                    p.notional = size
                    open_pos.append(p)
                    n_taken += 1
                else:
                    n_skipped += 1
            else:
                open_pos.append(_Pos(d, hold, ret))
                n_taken += 1
                event = True

        if mode == "rebalance" and event and open_pos:
            n = len(open_pos)
            target = min(cap, deploy / n) * equity_sod
            deployed = sum(p.notional for p in open_pos)
            for p in open_pos:
                if p.notional == 0.0:
                    # New entry: initial fill, no churn (baked RT cost covers it);
                    # never lever up past equity.
                    p.notional = min(target, max(0.0, equity_sod - deployed))
                    deployed += p.notional
                    continue
                delta = target - p.notional
                if band > 0.0 and target > 0 and abs(delta) <= band * target:
                    continue
                cost = abs(delta) * churn_oneway_pct / 100.0
                equity -= cost
                churn_paid += cost
                traded_rebal += abs(delta)
                deployed += delta
                p.notional = target

        if equity <= 0:
            curve.append((d, 0.0))
            break
        curve.append((d, equity))
        invested_sum += sum(p.notional for p in open_pos) / equity

    eq = np.array([e for _, e in curve])
    days = np.array([d for d, _ in curve])
    rets = np.zeros(len(eq) - 1)
    prev = eq[:-1]
    mask = prev > 0
    rets[mask] = (eq[1:][mask] - prev[mask]) / prev[mask]
    years = (days[-1] - days[0]) / 365.25
    cagr = (eq[-1] / START_EQUITY) ** (1 / years) - 1 if eq[-1] > 0 else -1.0
    return {
        "curve_days": days,
        "curve_eq": eq,
        "daily_rets": rets,
        "final": eq[-1],
        "cagr": cagr,
        "max_dd": _max_drawdown(eq),
        "sharpe": _sharpe(rets, periods_per_year=252),
        "n_taken": n_taken,
        "n_skipped": n_skipped,
        "avg_invested": invested_sum / len(curve),
        "churn_paid": churn_paid,
        "rebal_turnover_per_year": traded_rebal / START_EQUITY / years,  # rough, vs start eq
        "years": years,
    }


def dd_episodes(days: np.ndarray, eq: np.ndarray, min_depth: float = 0.05) -> list[dict]:
    """Peak-to-trough episodes (recovered or ongoing) deeper than min_depth."""
    episodes = []
    peak = eq[0]
    peak_d = days[0]
    trough = eq[0]
    trough_d = days[0]
    in_dd = False
    for d, e in zip(days, eq, strict=True):
        if e >= peak:
            if in_dd:
                depth = (peak - trough) / peak
                if depth >= min_depth:
                    episodes.append({"peak": peak_d, "trough": trough_d, "depth": depth})
                in_dd = False
            peak = e
            peak_d = d
            trough = e
        else:
            in_dd = True
            if e < trough:
                trough = e
                trough_d = d
    if in_dd:
        depth = (peak - trough) / peak
        if depth >= min_depth:
            episodes.append({"peak": peak_d, "trough": trough_d, "depth": depth, "ongoing": True})
    return sorted(episodes, key=lambda x: -x["depth"])


def ex_2020(days: np.ndarray, rets: np.ndarray) -> dict:
    """Splice calendar-2020 out of the daily return series and recompute."""
    ret_days = days[1:]
    keep = np.array([date.fromordinal(int(d)).year != 2020 for d in ret_days])
    r = rets[keep]
    eq = START_EQUITY * np.cumprod(1.0 + r)
    eq = np.concatenate(([START_EQUITY], eq))
    years = len(r) / 365.25
    cagr = (eq[-1] / START_EQUITY) ** (1 / years) - 1 if eq[-1] > 0 else -1.0
    return {"cagr": cagr, "max_dd": _max_drawdown(eq), "sharpe": _sharpe(r, periods_per_year=252)}


def fmt_row(name: str, r: dict, x: dict) -> str:
    return (
        f"{name:<42} {r['cagr'] * 100:>6.2f}% {r['max_dd'] * 100:>6.2f}% {r['sharpe']:>6.2f} "
        f"{r['avg_invested'] * 100:>6.1f}% {r['final']:>12,.0f} | "
        f"{x['cagr'] * 100:>6.2f}% {x['max_dd'] * 100:>6.2f}% {x['sharpe']:>6.2f}"
    )


def main() -> None:
    trades = load_trades()
    print(f"{len(trades)} trades loaded\n")

    configs: list[tuple[str, dict]] = [
        ("fixed concentrated 5 x 10%", dict(mode="fixed", per_trade_fraction=0.10, max_concurrent=5)),
        ("fixed breadth 50 x 5%", dict(mode="fixed", per_trade_fraction=0.05, max_concurrent=50)),
        ("fixed breadth 50 x 10% (deploy-matched)", dict(mode="fixed", per_trade_fraction=0.10, max_concurrent=50)),
        ("rebal cap10 deploy95 churn 0bps", dict(mode="rebalance", churn_oneway_pct=0.0)),
        ("rebal cap10 deploy95 churn 5bps", dict(mode="rebalance", churn_oneway_pct=0.05)),
        ("rebal cap10 deploy95 churn 10bps", dict(mode="rebalance", churn_oneway_pct=0.10)),
        ("rebal cap10 deploy95 churn 5bps band25%", dict(mode="rebalance", churn_oneway_pct=0.05, band=0.25)),
    ]

    header = (
        f"{'config':<42} {'CAGR':>7} {'maxDD':>7} {'sharpe':>6} {'avginv':>7} {'final eq':>12} | "
        f"{'exCAGR':>7} {'exDD':>7} {'exShp':>6}   (ex-2020 spliced; exCAGR directional only — "
        f"splice resets the base, use exDD/exShp as the gate)"
    )
    print(header)
    print("-" * len(header))

    results = {}
    for name, kw in configs:
        r = simulate(trades, **kw)
        x = ex_2020(r["curve_days"], r["daily_rets"])
        results[name] = (r, x)
        line = fmt_row(name, r, x)
        if kw.get("mode") == "rebalance":
            line += (
                f"   churn ${r['churn_paid']:,.0f}, "
                f"rebal turnover {r['rebal_turnover_per_year']:.1f}x start-eq/yr"
            )
        print(line)

    for name in (
        "fixed concentrated 5 x 10%",
        "fixed breadth 50 x 5%",
        "rebal cap10 deploy95 churn 5bps",
    ):
        r, _ = results[name]
        print(f"\nTop drawdown episodes — {name}  (>=5% depth, n="
              f"{len(dd_episodes(r['curve_days'], r['curve_eq']))})")
        for ep in dd_episodes(r["curve_days"], r["curve_eq"])[:6]:
            peak = date.fromordinal(int(ep["peak"])).isoformat()
            trough = date.fromordinal(int(ep["trough"])).isoformat()
            flag = "  (ongoing)" if ep.get("ongoing") else ""
            print(f"  {ep['depth'] * 100:>6.2f}%  peak {peak} -> trough {trough}{flag}")


if __name__ == "__main__":
    main()
