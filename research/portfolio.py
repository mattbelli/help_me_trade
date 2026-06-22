"""Portfolio equity-curve backtester — event-driven sim with benchmark comparison."""

from __future__ import annotations

import argparse
import logging
import math
from datetime import date
from pathlib import Path

import numpy as np

from db.base import connect
from research.bars import DEFAULT_BARS_DB_PATH
from research.strategy_dataset import DEFAULT_STRATEGY_DB_PATH

logger = logging.getLogger(__name__)

# holding_days is treated as a calendar-day offset from entry_date.toordinal() to exit
# ordinal (not trading days). Positions realize on the next entry event when
# exit_ordinal <= entry_ordinal, or at end-of-backtest for any remainder.


def _target_notional(
    equity: float,
    *,
    sizing: str,
    per_trade_fraction: float,
    stop_pct: float,
) -> float:
    if sizing == "fixed":
        return per_trade_fraction * equity
    if sizing == "risk":
        stop_frac = stop_pct / 100.0
        if stop_frac <= 0:
            return 0.0
        return (per_trade_fraction / stop_frac) * equity
    raise ValueError(f"unknown sizing mode: {sizing!r}")


def _max_drawdown(equities: np.ndarray) -> float:
    if len(equities) == 0:
        return 0.0
    peak = equities[0]
    max_dd = 0.0
    for eq in equities:
        if eq > peak:
            peak = eq
        if peak > 0:
            max_dd = max(max_dd, (peak - eq) / peak)
    return max_dd


def _daily_returns(equities: np.ndarray) -> np.ndarray:
    if len(equities) < 2:
        return np.array([], dtype=float)
    prev = equities[:-1]
    cur = equities[1:]
    mask = prev > 0
    rets = np.zeros(len(prev), dtype=float)
    rets[mask] = (cur[mask] - prev[mask]) / prev[mask]
    return rets


def _sharpe(daily_rets: np.ndarray, *, periods_per_year: int) -> float:
    if len(daily_rets) == 0:
        return 0.0
    std = float(np.std(daily_rets, ddof=0))
    if std == 0.0:
        return 0.0
    return float(np.mean(daily_rets) / std * math.sqrt(periods_per_year))


def _sortino(daily_rets: np.ndarray, *, periods_per_year: int) -> float:
    if len(daily_rets) == 0:
        return 0.0
    downside = np.minimum(daily_rets, 0.0)
    downside_dev = float(np.sqrt(np.mean(downside**2)))
    if downside_dev == 0.0:
        return 0.0
    return float(np.mean(daily_rets) / downside_dev * math.sqrt(periods_per_year))


def _build_daily_series(
    *,
    start_equity: float,
    first_ord: int,
    last_ord: int,
    taken_positions: list[tuple[int, int, float, float]],
) -> tuple[list[tuple[str, float]], float]:
    """Replay entry/exit events into a daily equity and invested series."""
    if last_ord < first_ord:
        return [], 0.0

    events_by_ord: dict[int, list[tuple]] = {}
    for entry_ord, exit_ord, notional, realized_pnl in taken_positions:
        events_by_ord.setdefault(exit_ord, []).append(("exit", notional, realized_pnl))
        events_by_ord.setdefault(entry_ord, []).append(("entry", notional))

    def _event_order(event: tuple) -> int:
        return 0 if event[0] == "exit" else 1

    equity = start_equity
    deployed = 0.0
    daily_equity: list[tuple[str, float]] = []
    invested_sum = 0.0

    for ordn in range(first_ord, last_ord + 1):
        for kind, *args in sorted(events_by_ord.get(ordn, ()), key=_event_order):
            if kind == "exit":
                notional, realized_pnl = args
                deployed -= notional
                equity += realized_pnl
            else:
                (notional,) = args
                deployed += notional

        dt = date.fromordinal(ordn).isoformat()
        daily_equity.append((dt, equity))
        if equity > 0:
            invested_sum += deployed / equity

    n_days = last_ord - first_ord + 1
    avg_pct_invested = invested_sum / n_days if n_days > 0 else 0.0
    return daily_equity, avg_pct_invested


def backtest_portfolio(
    trades: list[tuple],
    *,
    start_equity: float = 100_000.0,
    max_concurrent: int = 10,
    per_trade_fraction: float = 0.10,
    max_gross_exposure: float = 1.0,
    sizing: str = "fixed",
    stop_pct: float = 3.0,
    cost_pct: float = 0.30,
    periods_per_year: int = 252,
) -> dict:
    """Event-driven portfolio sim. Returns metrics + the daily equity curve."""
    parsed: list[tuple[int, int, float, str]] = []
    for entry_date, holding_days, return_pct in trades:
        dt = date.fromisoformat(str(entry_date))
        parsed.append((dt.toordinal(), int(holding_days), float(return_pct), dt.isoformat()))
    parsed.sort(key=lambda t: t[0])

    empty = {
        "start_equity": start_equity,
        "final_equity": start_equity,
        "total_return_pct": 0.0,
        "years": 0.0,
        "cagr": 0.0,
        "max_drawdown": 0.0,
        "sharpe": 0.0,
        "sortino": 0.0,
        "n_taken": 0,
        "n_skipped": 0,
        "pct_signals_taken": 0.0,
        "avg_pct_invested": 0.0,
        "daily_equity": [],
    }
    if not parsed:
        return empty

    equity = start_equity
    deployed = 0.0
    openpos: list[tuple[int, float, float]] = []
    n_taken = 0
    n_skipped = 0
    taken_positions: list[tuple[int, int, float, float]] = []
    max_exit_ord = parsed[0][0]

    for entry_ord, holding_days, return_pct, _entry_iso in parsed:
        keep: list[tuple[int, float, float]] = []
        for exit_ord, notional, ret in openpos:
            if exit_ord <= entry_ord:
                equity += notional * (ret - cost_pct) / 100.0
                deployed -= notional
            else:
                keep.append((exit_ord, notional, ret))
        openpos = keep

        target = _target_notional(
            equity,
            sizing=sizing,
            per_trade_fraction=per_trade_fraction,
            stop_pct=stop_pct,
        )
        if (
            len(openpos) < max_concurrent
            and deployed + target <= max_gross_exposure * equity + 1e-9
            and equity > 0
        ):
            exit_ord = entry_ord + holding_days
            openpos.append((exit_ord, target, return_pct))
            deployed += target
            n_taken += 1
            max_exit_ord = max(max_exit_ord, exit_ord)
            realized_pnl = target * (return_pct - cost_pct) / 100.0
            taken_positions.append((entry_ord, exit_ord, target, realized_pnl))
        else:
            n_skipped += 1

    for exit_ord, notional, ret in openpos:
        equity += notional * (ret - cost_pct) / 100.0
        deployed -= notional
        max_exit_ord = max(max_exit_ord, exit_ord)

    first_ord = parsed[0][0]
    daily_equity, avg_pct_invested = _build_daily_series(
        start_equity=start_equity,
        first_ord=first_ord,
        last_ord=max_exit_ord,
        taken_positions=taken_positions,
    )

    eq_arr = np.array([eq for _, eq in daily_equity], dtype=float)
    max_dd = _max_drawdown(eq_arr)
    daily_rets = _daily_returns(eq_arr)
    sharpe = _sharpe(daily_rets, periods_per_year=periods_per_year)
    sortino = _sortino(daily_rets, periods_per_year=periods_per_year)

    years = (max_exit_ord - first_ord) / 365.25
    total_return_pct = (equity / start_equity - 1.0) * 100.0 if start_equity > 0 else 0.0
    if years > 0 and equity > 0 and start_equity > 0:
        cagr = (equity / start_equity) ** (1.0 / years) - 1.0
    else:
        cagr = 0.0

    n_signals = n_taken + n_skipped
    pct_taken = (n_taken / n_signals * 100.0) if n_signals > 0 else 0.0

    return {
        "start_equity": start_equity,
        "final_equity": equity,
        "total_return_pct": total_return_pct,
        "years": years,
        "cagr": cagr,
        "max_drawdown": max_dd,
        "sharpe": sharpe,
        "sortino": sortino,
        "n_taken": n_taken,
        "n_skipped": n_skipped,
        "pct_signals_taken": pct_taken,
        "avg_pct_invested": avg_pct_invested,
        "daily_equity": daily_equity,
    }


def buy_and_hold_benchmark(
    symbol: str,
    start: str,
    end: str,
    *,
    source: str = "norgate",
    bars_db_path: Path = DEFAULT_BARS_DB_PATH,
    start_equity: float = 100_000.0,
) -> dict:
    """Total-return buy-and-hold over [start, end] from daily_bars."""
    sym = symbol.strip().upper()
    conn = connect(bars_db_path)
    try:
        rows = conn.execute(
            """
            SELECT date, c
            FROM daily_bars
            WHERE symbol = ? AND source = ? AND date >= ? AND date <= ?
            ORDER BY date ASC
            """,
            (sym, source, start, end),
        ).fetchall()
    finally:
        conn.close()

    if len(rows) < 2:
        return {
            "symbol": sym,
            "start": start,
            "end": end,
            "total_return_pct": 0.0,
            "cagr": 0.0,
            "final_equity": start_equity,
            "max_drawdown": 0.0,
        }

    closes = np.array([float(row["c"]) for row in rows], dtype=float)
    first_close = closes[0]
    last_close = closes[-1]
    total_return_pct = (last_close / first_close - 1.0) * 100.0
    final_equity = start_equity * (last_close / first_close)

    equities = start_equity * (closes / first_close)
    max_dd = _max_drawdown(equities)

    start_ord = date.fromisoformat(rows[0]["date"]).toordinal()
    end_ord = date.fromisoformat(rows[-1]["date"]).toordinal()
    years = (end_ord - start_ord) / 365.25
    if years > 0 and final_equity > 0:
        cagr = (final_equity / start_equity) ** (1.0 / years) - 1.0
    else:
        cagr = 0.0

    return {
        "symbol": sym,
        "start": rows[0]["date"],
        "end": rows[-1]["date"],
        "total_return_pct": total_return_pct,
        "cagr": cagr,
        "final_equity": final_equity,
        "max_drawdown": max_dd,
    }


def load_strategy_trades(
    *,
    db_path: Path = DEFAULT_STRATEGY_DB_PATH,
    source: str = "norgate",
    years: int | None = None,
) -> list[tuple[str, int, float]]:
    conn = connect(db_path)
    try:
        rows = conn.execute(
            """
            SELECT entry_date, holding_days, blended_return_pct
            FROM strategy_setups
            WHERE source = ?
            ORDER BY entry_date ASC
            """,
            (source,),
        ).fetchall()
    finally:
        conn.close()

    trades = [(row["entry_date"], int(row["holding_days"]), float(row["blended_return_pct"])) for row in rows]
    if years is not None and trades:
        last_dt = date.fromisoformat(trades[-1][0])
        cutoff = date(last_dt.year - years, last_dt.month, last_dt.day)
        trades = [t for t in trades if date.fromisoformat(t[0]) >= cutoff]
    return trades


def _format_pct(value: float) -> str:
    return f"{value * 100:+.2f}%"


def _format_money(value: float) -> str:
    return f"${value:,.0f}"


def print_report(result: dict, benchmark: dict) -> None:
    print("Strategy portfolio backtest")
    print("-" * 40)
    print(f"  Start equity:       {_format_money(result['start_equity'])}")
    print(f"  Final equity:       {_format_money(result['final_equity'])}")
    print(f"  Total return:       {result['total_return_pct']:+.2f}%")
    print(f"  CAGR:               {_format_pct(result['cagr'])}")
    print(f"  Max drawdown:       {result['max_drawdown'] * 100:.2f}%")
    print(f"  Sharpe:             {result['sharpe']:.2f}")
    print(f"  Sortino:            {result['sortino']:.2f}")
    print(f"  Signals taken:      {result['n_taken']} / {result['n_taken'] + result['n_skipped']}"
          f" ({result['pct_signals_taken']:.1f}%)")
    print(f"  Avg % invested:     {result['avg_pct_invested'] * 100:.1f}%")
    print()
    print(f"Benchmark buy-and-hold ({benchmark['symbol']})")
    print("-" * 40)
    print(f"  Period:             {benchmark['start']} .. {benchmark['end']}")
    print(f"  Final equity:       {_format_money(benchmark['final_equity'])}")
    print(f"  Total return:       {benchmark['total_return_pct']:+.2f}%")
    print(f"  CAGR:               {_format_pct(benchmark['cagr'])}")
    print(f"  Max drawdown:       {benchmark['max_drawdown'] * 100:.2f}%")
    print()
    cagr_gap = result["cagr"] - benchmark["cagr"]
    equity_gap = result["final_equity"] - benchmark["final_equity"]
    print("Strategy vs benchmark")
    print("-" * 40)
    print(f"  CAGR gap:           {_format_pct(cagr_gap)}")
    print(f"  Final equity gap:   {_format_money(equity_gap)}")


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Portfolio equity-curve backtester")
    parser.add_argument("--db", type=Path, default=DEFAULT_STRATEGY_DB_PATH, help="Strategy labeled DB")
    parser.add_argument("--source", default="norgate", help="Bar/source tag in strategy_setups")
    parser.add_argument("--start-equity", type=float, default=100_000.0, help="Starting equity")
    parser.add_argument("--max-concurrent", type=int, default=10, help="Max simultaneous positions")
    parser.add_argument("--per-trade-fraction", type=float, default=0.10, help="Sizing fraction")
    parser.add_argument("--max-gross", type=float, default=1.0, help="Max gross exposure / equity")
    parser.add_argument("--sizing", choices=("fixed", "risk"), default="fixed", help="Position sizing")
    parser.add_argument("--stop-pct", type=float, default=3.0, help="Stop %% for risk sizing")
    parser.add_argument("--cost-pct", type=float, default=0.30, help="Round-trip cost per trade %%")
    parser.add_argument("--years", type=int, default=None, help="Restrict to last N years of signals")
    parser.add_argument("--benchmark", default="SPY", help="Benchmark symbol")
    parser.add_argument(
        "--bars-db",
        type=Path,
        default=DEFAULT_BARS_DB_PATH,
        help="Daily bars DB for benchmark",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    args = _parse_args(argv)

    trades = load_strategy_trades(db_path=args.db, source=args.source, years=args.years)
    if not trades:
        logger.warning("No trades found for source=%s", args.source)
        return

    result = backtest_portfolio(
        trades,
        start_equity=args.start_equity,
        max_concurrent=args.max_concurrent,
        per_trade_fraction=args.per_trade_fraction,
        max_gross_exposure=args.max_gross,
        sizing=args.sizing,
        stop_pct=args.stop_pct,
        cost_pct=args.cost_pct,
    )

    daily = result["daily_equity"]
    start = daily[0][0] if daily else trades[0][0]
    end = daily[-1][0] if daily else trades[-1][0]
    benchmark = buy_and_hold_benchmark(
        args.benchmark,
        start,
        end,
        source=args.source,
        bars_db_path=args.bars_db,
        start_equity=args.start_equity,
    )
    print_report(result, benchmark)


if __name__ == "__main__":
    main()
