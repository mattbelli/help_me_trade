"""Multi-strategy portfolio combiner — merge N strategy setup tables into shared capital."""

from __future__ import annotations

import argparse
import logging
from datetime import date
from pathlib import Path

from db.base import connect
from research.bars import DEFAULT_BARS_DB_PATH
from research.portfolio import backtest_portfolio, buy_and_hold_benchmark

logger = logging.getLogger(__name__)

DEFAULT_WINDOWS: dict[str, tuple[str | None, str | None]] = {
    "full": (None, None),
    "TRAIN": ("2011-01-01", "2020-12-31"),
    "TEST": ("2021-01-01", "2026-12-31"),
}


def load_strategy_trades(
    db_path: str | Path,
    *,
    source: str = "norgate",
    cost_pct: float = 0.0,
    start: str | None = None,
    end: str | None = None,
) -> list[tuple[str, int, float]]:
    """Return [(entry_date, holding_days, net_return)] from strategy_setups for one DB."""
    conn = connect(Path(db_path))
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

    trades: list[tuple[str, int, float]] = []
    for row in rows:
        entry_date = row["entry_date"]
        if start is not None and entry_date < start:
            continue
        if end is not None and entry_date > end:
            continue
        net_return = float(row["blended_return_pct"]) - cost_pct
        trades.append((entry_date, int(row["holding_days"]), net_return))
    return trades


def _filter_trades(
    trades: list[tuple[str, int, float]],
    start: str | None,
    end: str | None,
) -> list[tuple[str, int, float]]:
    if start is None and end is None:
        return list(trades)
    filtered: list[tuple[str, int, float]] = []
    for entry_date, holding_days, ret in trades:
        if start is not None and entry_date < start:
            continue
        if end is not None and entry_date > end:
            continue
        filtered.append((entry_date, holding_days, ret))
    return filtered


def _trade_span(trades: list[tuple[str, int, float]]) -> tuple[str, str]:
    if not trades:
        return "2000-01-01", "2000-01-01"
    min_entry = min(t[0] for t in trades)
    max_exit_ord = max(date.fromisoformat(t[0]).toordinal() + int(t[1]) for t in trades)
    return min_entry, date.fromordinal(max_exit_ord).isoformat()


def _recent_two_year_window(trades: list[tuple[str, int, float]]) -> tuple[str | None, str | None]:
    if not trades:
        return None, None
    max_entry = max(t[0] for t in trades)
    max_dt = date.fromisoformat(max_entry)
    start_ord = max_dt.toordinal() - int(2 * 365.25)
    return date.fromordinal(start_ord).isoformat(), max_entry


def _resolve_windows(
    all_trades: list[tuple[str, int, float]],
    windows: dict[str, tuple[str | None, str | None]] | None,
) -> dict[str, tuple[str | None, str | None]]:
    if windows is not None:
        return dict(windows)
    resolved = dict(DEFAULT_WINDOWS)
    resolved["recent-2y"] = _recent_two_year_window(all_trades)
    return resolved


def _backtest_kwargs(
    *,
    max_concurrent: int,
    per_trade_fraction: float,
    sizing: str,
    start_equity: float,
) -> dict:
    return {
        "start_equity": start_equity,
        "max_concurrent": max_concurrent,
        "per_trade_fraction": per_trade_fraction,
        "sizing": sizing,
        "cost_pct": 0.0,
    }


def _format_pct(value: float) -> str:
    return f"{value * 100:+.2f}%"


def _format_money(value: float) -> str:
    return f"${value:,.0f}"


def _log_window_table(
    window_name: str,
    window_results: dict,
    bench_cagr: float,
) -> None:
    header = (
        f"{'label':<12} {'final':>12} {'CAGR':>8} {'maxDD':>7} "
        f"{'Sharpe':>7} {'invested':>9} {'n':>5} {'edge':>8}"
    )
    logger.info("Window: %s", window_name)
    logger.info(header)
    for label, metrics in window_results.items():
        if label == "benchmark":
            bench = metrics
            logger.info(
                "%-12s %12s %8s %7s %7s %9s %5s %8s",
                "benchmark",
                _format_money(bench["final_equity"]),
                _format_pct(bench["cagr"]),
                f"{bench['max_drawdown'] * 100:.1f}%",
                "-",
                "-",
                "-",
                "-",
            )
            continue
        edge = metrics["cagr"] - bench_cagr
        edge_str = _format_pct(edge) if label == "COMBINED" else "-"
        logger.info(
            "%-12s %12s %8s %7s %7.2f %9s %5d %8s",
            label,
            _format_money(metrics["final_equity"]),
            _format_pct(metrics["cagr"]),
            f"{metrics['max_drawdown'] * 100:.1f}%",
            metrics["sharpe"],
            f"{metrics['avg_pct_invested'] * 100:.1f}%",
            metrics["n_taken"],
            edge_str,
        )


def combine_and_report(
    sources: list[tuple[str, str | Path, float]],
    *,
    max_concurrent: int = 20,
    per_trade_fraction: float = 0.05,
    sizing: str = "fixed",
    benchmark: str = "SPY",
    windows: dict[str, tuple[str | None, str | None]] | None = None,
    start_equity: float = 100_000.0,
    bars_db_path: Path = DEFAULT_BARS_DB_PATH,
    bar_source: str = "norgate",
) -> dict:
    """Merge strategy sources and report per-window metrics for each source, COMBINED, and benchmark."""
    loaded: dict[str, list[tuple[str, int, float]]] = {}
    for label, db_path, cost_pct in sources:
        loaded[label] = load_strategy_trades(
            db_path,
            source=bar_source,
            cost_pct=cost_pct,
        )

    all_trades = [t for trades in loaded.values() for t in trades]
    window_defs = _resolve_windows(all_trades, windows)
    bt_kwargs = _backtest_kwargs(
        max_concurrent=max_concurrent,
        per_trade_fraction=per_trade_fraction,
        sizing=sizing,
        start_equity=start_equity,
    )

    report: dict = {}
    for window_name, (win_start, win_end) in window_defs.items():
        window_results: dict = {}
        merged: list[tuple[str, int, float]] = []

        for label, trades in loaded.items():
            filtered = _filter_trades(trades, win_start, win_end)
            merged.extend(filtered)
            window_results[label] = backtest_portfolio(filtered, **bt_kwargs)

        window_results["COMBINED"] = backtest_portfolio(merged, **bt_kwargs)

        if win_start is not None and win_end is not None:
            bench_start, bench_end = win_start, win_end
        else:
            bench_start, bench_end = _trade_span(merged)

        window_results["benchmark"] = buy_and_hold_benchmark(
            benchmark,
            bench_start,
            bench_end,
            source=bar_source,
            bars_db_path=bars_db_path,
            start_equity=start_equity,
        )

        report[window_name] = window_results
        _log_window_table(
            window_name,
            window_results,
            window_results["benchmark"]["cagr"],
        )

    return report


def _parse_source_arg(arg: str) -> tuple[str, Path, float]:
    parts = arg.rsplit(":", 2)
    if len(parts) != 3:
        raise ValueError(f"expected LABEL:DBPATH:COST, got {arg!r}")
    label, path_str, cost_str = parts
    return label, Path(path_str), float(cost_str)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Combine multiple strategy setup DBs into one shared-capital portfolio",
    )
    parser.add_argument(
        "--source",
        action="append",
        dest="sources",
        metavar="LABEL:DBPATH:COST",
        help="Strategy source (repeatable), e.g. dip:data/research/strategy_labeled.db:0.20",
    )
    parser.add_argument("--max-concurrent", type=int, default=20, help="Max simultaneous positions")
    parser.add_argument("--per-trade-fraction", type=float, default=0.05, help="Sizing fraction")
    parser.add_argument("--benchmark", default="SPY", help="Benchmark symbol")
    parser.add_argument("--start-equity", type=float, default=100_000.0, help="Starting equity")
    parser.add_argument(
        "--bars-db",
        type=Path,
        default=DEFAULT_BARS_DB_PATH,
        help="Daily bars DB for benchmark",
    )
    parser.add_argument("--bar-source", default="norgate", help="Bar/source tag in daily_bars")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    args = _parse_args(argv)

    if not args.sources:
        logger.error("At least one --source LABEL:DBPATH:COST is required")
        raise SystemExit(2)

    parsed_sources = [_parse_source_arg(s) for s in args.sources]
    combine_and_report(
        parsed_sources,
        max_concurrent=args.max_concurrent,
        per_trade_fraction=args.per_trade_fraction,
        benchmark=args.benchmark,
        start_equity=args.start_equity,
        bars_db_path=args.bars_db,
        bar_source=args.bar_source,
    )


if __name__ == "__main__":
    main()
