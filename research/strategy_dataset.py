"""Assemble strategy-labeled setup rows from entry filter + exit simulation."""

from __future__ import annotations

import argparse
import json
import logging
import os
import statistics
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import date, timedelta
from pathlib import Path

from db.base import connect, now_iso
from research.bars import DEFAULT_BARS_DB_PATH, cached_symbols, get_daily_bars
from research.features import compute_features
from research.strategy_features import compute_market_context, is_strategy_entry
from research.strategy_sim import simulate_exit
from signals.trade_context import MIN_BARS_FOR_CONTEXT, _find_bar_index

logger = logging.getLogger(__name__)

RESEARCH_DATA_DIR = DEFAULT_BARS_DB_PATH.parent
DEFAULT_STRATEGY_DB_PATH = RESEARCH_DATA_DIR / "strategy_labeled.db"

_CREATE_STRATEGY_SETUPS = """
CREATE TABLE IF NOT EXISTS strategy_setups (
    id INTEGER PRIMARY KEY,
    symbol TEXT,
    entry_date TEXT,
    features_json TEXT,
    label INTEGER,
    blended_return_pct REAL,
    outcome TEXT,
    holding_days INTEGER,
    same_day_ambiguous INTEGER,
    max_holding INTEGER,
    stop_pct REAL,
    first_target_pct REAL,
    runner_target_pct REAL,
    source TEXT,
    created_at TEXT
)
"""

_INSERT_STRATEGY_SETUP = """
INSERT INTO strategy_setups (
    symbol, entry_date, features_json, label, blended_return_pct, outcome,
    holding_days, same_day_ambiguous, max_holding, stop_pct, first_target_pct,
    runner_target_pct, source, created_at
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""

_WORKER_SPY_BARS: list | None = None


def _ensure_strategy_schema(conn) -> None:
    conn.execute(_CREATE_STRATEGY_SETUPS)
    conn.commit()


def _init_strategy_worker(spy_bars: list) -> None:
    global _WORKER_SPY_BARS
    _WORKER_SPY_BARS = spy_bars


def _strategy_symbol_rows(task: tuple) -> tuple[str, list[tuple], dict]:
    """Label one symbol; return (symbol, rows, counts). Pure CPU + bars-db reads."""
    (
        symbol,
        start,
        end,
        warmup_start,
        source,
        bars_db_path,
        stop_pct,
        first_target_pct,
        first_exit_fraction,
        runner_target_pct,
        breakeven_after_first,
        max_holding,
        healthy_required,
        oversold_3d_max,
        mkt_down_3d_max,
        require_capitulation,
        cap_day_max,
        cap_vol_min,
        cost_pct,
        created_at,
    ) = task

    sym = symbol.strip().upper()
    spy_bars = _WORKER_SPY_BARS
    if spy_bars is None:
        raise RuntimeError("strategy worker not initialized with SPY bars")

    bars = get_daily_bars(
        sym,
        warmup_start,
        end,
        source=source,
        db_path=bars_db_path,
    )
    counts = {"rows": 0}
    if len(bars) < MIN_BARS_FOR_CONTEXT + max_holding + 1:
        logger.warning("Insufficient bars for %s (%d)", sym, len(bars))
        return sym, [], counts

    rows: list[tuple] = []
    min_entry_idx = MIN_BARS_FOR_CONTEXT - 1
    max_entry_idx = len(bars) - 1 - max_holding

    for idx in range(min_entry_idx, max_entry_idx + 1):
        entry_date = date.fromisoformat(bars[idx]["d"])
        if entry_date < start or entry_date > end:
            continue

        spy_idx = _find_bar_index(spy_bars, entry_date)
        if spy_idx is None:
            continue

        if not is_strategy_entry(
            bars,
            idx,
            spy_bars=spy_bars,
            spy_idx=spy_idx,
            healthy_required=healthy_required,
            oversold_3d_max=oversold_3d_max,
            mkt_down_3d_max=mkt_down_3d_max,
            require_capitulation=require_capitulation,
            cap_day_max=cap_day_max,
            cap_vol_min=cap_vol_min,
        ):
            continue

        feats = compute_features(bars, idx, benchmark_bars=spy_bars)
        mkt = compute_market_context(spy_bars, spy_idx)
        if feats is None or mkt is None:
            continue

        features = {**feats, **mkt}

        sim = simulate_exit(
            bars,
            idx,
            stop_pct=stop_pct,
            first_target_pct=first_target_pct,
            first_exit_fraction=first_exit_fraction,
            runner_target_pct=runner_target_pct,
            breakeven_after_first=breakeven_after_first,
            max_holding=max_holding,
        )
        if sim is None:
            continue

        net = sim["blended_return_pct"] - cost_pct
        label = 1 if net > 0 else 0

        rows.append(
            (
                sym,
                bars[idx]["d"],
                json.dumps(features),
                label,
                net,
                sim["outcome"],
                sim["holding_days"],
                1 if sim["same_day_ambiguous"] else 0,
                max_holding,
                stop_pct,
                first_target_pct,
                runner_target_pct,
                source,
                created_at,
            )
        )
        counts["rows"] += 1

    return sym, rows, counts


def _insert_strategy_rows(conn, rows: list[tuple]) -> None:
    for row in rows:
        conn.execute(_INSERT_STRATEGY_SETUP, row)


def _strategy_tasks(
    symbols: list[str],
    *,
    start: date,
    end: date,
    warmup_start: date,
    source: str,
    bars_db_path: Path,
    stop_pct: float,
    first_target_pct: float,
    first_exit_fraction: float,
    runner_target_pct: float,
    breakeven_after_first: bool,
    max_holding: int,
    healthy_required: bool,
    oversold_3d_max: float,
    mkt_down_3d_max: float,
    require_capitulation: bool,
    cap_day_max: float,
    cap_vol_min: float,
    cost_pct: float,
    created_at: str,
) -> list[tuple]:
    return [
        (
            symbol,
            start,
            end,
            warmup_start,
            source,
            bars_db_path,
            stop_pct,
            first_target_pct,
            first_exit_fraction,
            runner_target_pct,
            breakeven_after_first,
            max_holding,
            healthy_required,
            oversold_3d_max,
            mkt_down_3d_max,
            require_capitulation,
            cap_day_max,
            cap_vol_min,
            cost_pct,
            created_at,
        )
        for symbol in symbols
    ]


def _exit_desc(
    *,
    stop_pct: float,
    first_target_pct: float,
    first_exit_fraction: float,
    runner_target_pct: float,
    breakeven_after_first: bool,
    max_holding: int,
) -> str:
    be = "breakeven" if breakeven_after_first else "trail-stop"
    half = f"{first_exit_fraction:.0%} at +{first_target_pct:g}%"
    return (
        f"stop -{stop_pct:g}% / +{first_target_pct:g}% {half} / {be} / "
        f"+{runner_target_pct:g}% runner / {max_holding}d cap"
    )


def _entry_desc(
    *,
    healthy_required: bool,
    oversold_3d_max: float,
    mkt_down_3d_max: float,
    require_capitulation: bool,
) -> str:
    parts: list[str] = []
    if healthy_required:
        parts.append("healthy")
    parts.append(f"oversold({oversold_3d_max:g}%/3d)")
    parts.append(f"mkt({mkt_down_3d_max:g}%/3d)")
    if require_capitulation:
        parts.append("capitulation")
    return "+".join(parts)


def _outcome_pct_line(outcome_counts: dict[str, int], n_setups: int) -> str:
    if n_setups == 0:
        return "outcomes: (none)"
    parts = []
    for name in sorted(outcome_counts, key=lambda k: (-outcome_counts[k], k)):
        pct = 100.0 * outcome_counts[name] / n_setups
        parts.append(f"{name} {pct:.0f}%")
    return "outcomes: " + " · ".join(parts)


def _stats_from_row_tuple(row: tuple) -> dict:
    return {
        "symbol": row[0],
        "label": row[3],
        "blended_return_pct": row[4],
        "outcome": row[5],
        "holding_days": row[6],
        "same_day_ambiguous": bool(row[7]),
    }


def _compute_summary_from_rows(
    rows: list[dict],
    *,
    start: date,
    end: date,
    stop_pct: float,
    first_target_pct: float,
    first_exit_fraction: float,
    runner_target_pct: float,
    breakeven_after_first: bool,
    max_holding: int,
    healthy_required: bool,
    oversold_3d_max: float,
    mkt_down_3d_max: float,
    require_capitulation: bool,
    cap_day_max: float,
    cap_vol_min: float,
    cost_pct: float,
) -> dict:
    n_setups = len(rows)
    if n_setups == 0:
        summary = {
            "n_setups": 0,
            "n_symbols_with_setups": 0,
            "win_rate": 0.0,
            "avg_return": 0.0,
            "median_return": 0.0,
            "std_return": 0.0,
            "outcome_counts": {},
            "same_day_ambiguous_pct": 0.0,
            "avg_holding_days": 0.0,
            "date_range": {"start": start.isoformat(), "end": end.isoformat()},
            "stop_pct": stop_pct,
            "first_target_pct": first_target_pct,
            "first_exit_fraction": first_exit_fraction,
            "runner_target_pct": runner_target_pct,
            "breakeven_after_first": breakeven_after_first,
            "max_holding": max_holding,
            "healthy_required": healthy_required,
            "oversold_3d_max": oversold_3d_max,
            "mkt_down_3d_max": mkt_down_3d_max,
            "require_capitulation": require_capitulation,
            "cap_day_max": cap_day_max,
            "cap_vol_min": cap_vol_min,
            "cost_pct": cost_pct,
        }
        _log_summary(summary)
        return summary

    labels = [row["label"] for row in rows]
    returns = [row["blended_return_pct"] for row in rows]
    holdings = [row["holding_days"] for row in rows]
    symbols = {row["symbol"] for row in rows}

    outcome_counts: dict[str, int] = {}
    ambiguous = 0
    for row in rows:
        outcome_counts[row["outcome"]] = outcome_counts.get(row["outcome"], 0) + 1
        if row["same_day_ambiguous"]:
            ambiguous += 1

    summary = {
        "n_setups": n_setups,
        "n_symbols_with_setups": len(symbols),
        "win_rate": sum(labels) / n_setups,
        "avg_return": statistics.mean(returns),
        "median_return": statistics.median(returns),
        "std_return": statistics.stdev(returns) if n_setups > 1 else 0.0,
        "outcome_counts": outcome_counts,
        "same_day_ambiguous_pct": ambiguous / n_setups,
        "avg_holding_days": statistics.mean(holdings),
        "date_range": {"start": start.isoformat(), "end": end.isoformat()},
        "stop_pct": stop_pct,
        "first_target_pct": first_target_pct,
        "first_exit_fraction": first_exit_fraction,
        "runner_target_pct": runner_target_pct,
        "breakeven_after_first": breakeven_after_first,
        "max_holding": max_holding,
        "healthy_required": healthy_required,
        "oversold_3d_max": oversold_3d_max,
        "mkt_down_3d_max": mkt_down_3d_max,
        "require_capitulation": require_capitulation,
        "cap_day_max": cap_day_max,
        "cap_vol_min": cap_vol_min,
        "cost_pct": cost_pct,
    }
    _log_summary(summary)
    return summary


def _log_summary(summary: dict) -> None:
    exit_line = _exit_desc(
        stop_pct=summary["stop_pct"],
        first_target_pct=summary["first_target_pct"],
        first_exit_fraction=summary["first_exit_fraction"],
        runner_target_pct=summary["runner_target_pct"],
        breakeven_after_first=summary["breakeven_after_first"],
        max_holding=summary["max_holding"],
    )
    entry_line = _entry_desc(
        healthy_required=summary["healthy_required"],
        oversold_3d_max=summary["oversold_3d_max"],
        mkt_down_3d_max=summary["mkt_down_3d_max"],
        require_capitulation=summary["require_capitulation"],
    )
    win_pct = summary["win_rate"] * 100
    amb_pct = summary["same_day_ambiguous_pct"] * 100
    logger.info(
        "STRATEGY: %s | entry: %s\n"
        "setups: %s across %s symbols | win%% %.1f%% | avg EV/trade %+.2f%% | "
        "median %+.1f%% | avg hold %.1fd\n"
        "%s\n"
        "same-day-ambiguous: %.1f%% of trades | cost applied: %.2f%%",
        exit_line,
        entry_line,
        f"{summary['n_setups']:,}",
        summary["n_symbols_with_setups"],
        win_pct,
        summary["avg_return"],
        summary["median_return"],
        summary["avg_holding_days"],
        _outcome_pct_line(summary["outcome_counts"], summary["n_setups"]),
        amb_pct,
        summary["cost_pct"],
    )


def build_strategy_dataset(
    symbols: list[str],
    start: date,
    end: date,
    *,
    benchmark: str = "SPY",
    source: str = "norgate",
    bars_db_path: Path = DEFAULT_BARS_DB_PATH,
    strategy_db_path: Path = DEFAULT_STRATEGY_DB_PATH,
    stop_pct: float = 3.0,
    first_target_pct: float = 3.0,
    first_exit_fraction: float = 0.5,
    runner_target_pct: float = 10.0,
    breakeven_after_first: bool = True,
    max_holding: int = 20,
    healthy_required: bool = True,
    oversold_3d_max: float = -5.0,
    mkt_down_3d_max: float = -1.5,
    require_capitulation: bool = False,
    cap_day_max: float = -2.0,
    cap_vol_min: float = 1.5,
    cost_pct: float = 0.0,
    jobs: int = 1,
    replace: bool = True,
) -> dict:
    """Build strategy-labeled rows and return headline expectancy summary."""
    benchmark_sym = benchmark.strip().upper()
    work_symbols = [s.strip().upper() for s in symbols if s.strip().upper() != benchmark_sym]

    warmup_start = start - timedelta(
        days=int(MIN_BARS_FOR_CONTEXT * 1.5) + max_holding + 30
    )
    spy_bars = get_daily_bars(
        benchmark_sym,
        warmup_start,
        end,
        source=source,
        db_path=bars_db_path,
    )

    conn = connect(strategy_db_path)
    try:
        _ensure_strategy_schema(conn)
        if replace:
            conn.execute("DELETE FROM strategy_setups WHERE source = ?", (source,))
            conn.commit()

        created_at = now_iso()
        tasks = _strategy_tasks(
            work_symbols,
            start=start,
            end=end,
            warmup_start=warmup_start,
            source=source,
            bars_db_path=bars_db_path,
            stop_pct=stop_pct,
            first_target_pct=first_target_pct,
            first_exit_fraction=first_exit_fraction,
            runner_target_pct=runner_target_pct,
            breakeven_after_first=breakeven_after_first,
            max_holding=max_holding,
            healthy_required=healthy_required,
            oversold_3d_max=oversold_3d_max,
            mkt_down_3d_max=mkt_down_3d_max,
            require_capitulation=require_capitulation,
            cap_day_max=cap_day_max,
            cap_vol_min=cap_vol_min,
            cost_pct=cost_pct,
            created_at=created_at,
        )

        global _WORKER_SPY_BARS
        _WORKER_SPY_BARS = spy_bars
        run_stats: list[dict] = []

        if jobs <= 1:
            for task in tasks:
                sym = task[0].strip().upper()
                logger.info("Building strategy dataset for %s", sym)
                _, rows, counts = _strategy_symbol_rows(task)
                _insert_strategy_rows(conn, rows)
                run_stats.extend(_stats_from_row_tuple(row) for row in rows)
                conn.commit()
                logger.info("  %s: %d setups", sym, counts["rows"])
        else:
            completed = 0
            with ProcessPoolExecutor(
                max_workers=jobs,
                initializer=_init_strategy_worker,
                initargs=(spy_bars,),
            ) as executor:
                futures = {
                    executor.submit(_strategy_symbol_rows, task): task[0].strip().upper()
                    for task in tasks
                }
                for future in as_completed(futures):
                    sym, rows, counts = future.result()
                    _insert_strategy_rows(conn, rows)
                    run_stats.extend(_stats_from_row_tuple(row) for row in rows)
                    conn.commit()
                    completed += 1
                    if completed % 100 == 0 or completed == len(futures):
                        logger.info(
                            "Progress: %d / %d symbols",
                            completed,
                            len(futures),
                        )
                    logger.info("  %s: %d setups", sym, counts["rows"])

        return _compute_summary_from_rows(
            run_stats,
            start=start,
            end=end,
            stop_pct=stop_pct,
            first_target_pct=first_target_pct,
            first_exit_fraction=first_exit_fraction,
            runner_target_pct=runner_target_pct,
            breakeven_after_first=breakeven_after_first,
            max_holding=max_holding,
            healthy_required=healthy_required,
            oversold_3d_max=oversold_3d_max,
            mkt_down_3d_max=mkt_down_3d_max,
            require_capitulation=require_capitulation,
            cap_day_max=cap_day_max,
            cap_vol_min=cap_vol_min,
            cost_pct=cost_pct,
        )
    finally:
        conn.close()


def _default_jobs() -> int:
    return max(1, (os.cpu_count() or 2) - 2)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build strategy-labeled setup dataset")
    parser.add_argument(
        "--symbols",
        help="Comma-separated equity symbols (e.g. AAPL,MSFT,NVDA)",
    )
    parser.add_argument(
        "--symbols-from-cache",
        action="store_true",
        help="Use every symbol already cached for --source",
    )
    parser.add_argument(
        "--source",
        default="norgate",
        help="Bar source to label (default: norgate)",
    )
    parser.add_argument(
        "--years",
        type=int,
        default=15,
        help="Years of history ending today (default: 15)",
    )
    parser.add_argument(
        "--append",
        action="store_true",
        help="Append rows for this source instead of replacing prior rows",
    )
    parser.add_argument(
        "--jobs",
        type=int,
        default=_default_jobs(),
        help=(
            "Parallel worker count for per-symbol labeling "
            f"(default: max(1, cpu_count-2) = {_default_jobs()})"
        ),
    )
    parser.add_argument("--max-holding", type=int, default=20, help="Max holding days")
    parser.add_argument("--stop-pct", type=float, default=3.0, help="Stop loss %%")
    parser.add_argument("--first-target-pct", type=float, default=3.0, help="First target %%")
    parser.add_argument(
        "--first-exit-fraction",
        type=float,
        default=0.5,
        help="Fraction exited at first target",
    )
    parser.add_argument(
        "--runner-target-pct",
        type=float,
        default=10.0,
        help="Runner target %%",
    )
    parser.add_argument(
        "--require-capitulation",
        action="store_true",
        help="Require capitulation day + volume spike",
    )
    parser.add_argument(
        "--oversold-3d-max",
        type=float,
        default=-5.0,
        help="Max 3-day return for oversold entry (default: -5.0)",
    )
    parser.add_argument(
        "--mkt-down-3d-max",
        type=float,
        default=-1.5,
        help="Max SPY 3-day return for entry (default: -1.5)",
    )
    parser.add_argument(
        "--cost-pct",
        type=float,
        default=0.0,
        help="Round-trip cost deducted from blended return (default: 0.0)",
    )
    args = parser.parse_args(argv)
    if not args.symbols and not args.symbols_from_cache:
        parser.error("provide --symbols or --symbols-from-cache")
    return args


def main(argv: list[str] | None = None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    args = _parse_args(argv)
    if args.symbols_from_cache:
        symbols = cached_symbols(args.source)
        logger.info("Loaded %d symbols from cache (source=%s)", len(symbols), args.source)
    else:
        symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
    end = date.today()
    start = end - timedelta(days=args.years * 365)
    build_strategy_dataset(
        symbols,
        start,
        end,
        source=args.source,
        replace=not args.append,
        jobs=args.jobs,
        max_holding=args.max_holding,
        stop_pct=args.stop_pct,
        first_target_pct=args.first_target_pct,
        first_exit_fraction=args.first_exit_fraction,
        runner_target_pct=args.runner_target_pct,
        require_capitulation=args.require_capitulation,
        oversold_3d_max=args.oversold_3d_max,
        mkt_down_3d_max=args.mkt_down_3d_max,
        cost_pct=args.cost_pct,
    )


if __name__ == "__main__":
    main()
