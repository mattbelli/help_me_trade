"""Pre-earnings drift after blowout — event-driven setup builder."""

from __future__ import annotations

import argparse
import bisect
import json
import logging
import statistics
from collections.abc import Callable
from pathlib import Path

from db.base import connect, now_iso
from research.bars import DEFAULT_BARS_DB_PATH, RESEARCH_DATA_DIR, cached_symbols
from research.earnings import DEFAULT_EARNINGS_DB_PATH, earnings_dates
from signals.trade_context import DatedBar

logger = logging.getLogger(__name__)

DEFAULT_EARNINGS_STRATEGY_DB_PATH = RESEARCH_DATA_DIR / "earnings_labeled.db"

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
    blowout_reaction_pct REAL,
    next_earnings_date TEXT,
    source TEXT,
    created_at TEXT
)
"""

_INSERT_STRATEGY_SETUP = """
INSERT INTO strategy_setups (
    symbol, entry_date, features_json, label, blended_return_pct, outcome,
    holding_days, same_day_ambiguous, max_holding, stop_pct, first_target_pct,
    runner_target_pct, blowout_reaction_pct, next_earnings_date, source, created_at
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""


def _ensure_schema(conn) -> None:
    conn.execute(_CREATE_STRATEGY_SETUPS)
    conn.commit()


def _load_all_bars(
    symbol: str,
    *,
    source: str,
    db_path: Path,
) -> list[DatedBar]:
    sym = symbol.strip().upper()
    conn = connect(db_path)
    try:
        from research.bars import _ensure_schema as _ensure_bars_schema

        _ensure_bars_schema(conn)
        rows = conn.execute(
            """
            SELECT date, o, h, l, c, v
            FROM daily_bars
            WHERE symbol = ? AND source = ?
            ORDER BY date ASC
            """,
            (sym, source),
        ).fetchall()
    finally:
        conn.close()
    return [
        {
            "d": row["date"],
            "o": row["o"],
            "h": row["h"],
            "l": row["l"],
            "c": row["c"],
            "v": row["v"],
        }
        for row in rows
    ]


def _idx_of(bar_dates: list[str], earnings_date: str) -> int | None:
    """Index of the first bar with date >= earnings_date."""
    idx = bisect.bisect_left(bar_dates, earnings_date)
    if idx >= len(bar_dates):
        return None
    return idx


def _blowout_reaction(closes: list[float], j: int) -> float | None:
    if j - 1 < 0 or j + 1 >= len(closes):
        return None
    prev_close = closes[j - 1]
    if prev_close <= 0:
        return None
    return closes[j + 1] / prev_close - 1.0


def _symbol_setups(
    symbol: str,
    bars: list[DatedBar],
    earnings: list[str],
    *,
    blowout_pct: float,
    entry_lead_td: int,
    exit_lead_td: int,
    require_blowout: bool,
    cost_pct: float,
    min_gap_td: int,
    max_gap_td: int,
    source: str,
    created_at: str,
) -> list[tuple]:
    if len(bars) < 3 or len(earnings) < 2:
        return []

    sym = symbol.strip().upper()
    bar_dates = [bar["d"] for bar in bars]
    closes = [float(bar["c"]) for bar in bars]
    rows: list[tuple] = []

    for i in range(len(earnings) - 1):
        e_cur = earnings[i]
        e_next = earnings[i + 1]
        j = _idx_of(bar_dates, e_cur)
        if j is None:
            continue

        reaction = _blowout_reaction(closes, j)
        if reaction is None:
            continue
        if require_blowout and reaction * 100.0 < blowout_pct:
            continue

        jn = _idx_of(bar_dates, e_next)
        if jn is None:
            continue

        gap_td = jn - j
        if gap_td < min_gap_td or gap_td > max_gap_td:
            continue

        entry_idx = jn - entry_lead_td
        exit_idx = jn - exit_lead_td
        if entry_idx <= j or entry_idx < 0 or exit_idx <= entry_idx or exit_idx >= len(bars):
            continue

        entry_close = closes[entry_idx]
        exit_close = closes[exit_idx]
        if entry_close <= 0:
            continue

        gross_ret_pct = (exit_close / entry_close - 1.0) * 100.0
        net_ret_pct = gross_ret_pct - cost_pct
        holding_days = exit_idx - entry_idx
        label = 1 if net_ret_pct > 0 else 0
        outcome = "win" if label else "loss"
        reaction_pct = reaction * 100.0

        features = {
            "blowout_reaction_pct": reaction_pct,
            "next_earnings_date": e_next,
            "prior_earnings_date": e_cur,
            "gap_trading_days": gap_td,
        }

        rows.append(
            (
                sym,
                bars[entry_idx]["d"],
                json.dumps(features),
                label,
                net_ret_pct,
                outcome,
                holding_days,
                0,
                holding_days,
                0.0,
                0.0,
                0.0,
                reaction_pct,
                e_next,
                source,
                created_at,
            )
        )

    return rows


def _compute_summary(
    rows: list[dict],
    *,
    blowout_pct: float,
    entry_lead_td: int,
    exit_lead_td: int,
    require_blowout: bool,
    cost_pct: float,
    min_gap_td: int,
    max_gap_td: int,
    source: str,
) -> dict:
    n_setups = len(rows)
    if n_setups == 0:
        summary = {
            "n_setups": 0,
            "n_symbols": 0,
            "win_rate": 0.0,
            "avg_return": 0.0,
            "median_return": 0.0,
            "blowout_pct": blowout_pct,
            "entry_lead_td": entry_lead_td,
            "exit_lead_td": exit_lead_td,
            "require_blowout": require_blowout,
            "cost_pct": cost_pct,
            "min_gap_td": min_gap_td,
            "max_gap_td": max_gap_td,
            "source": source,
        }
        _log_summary(summary)
        return summary

    labels = [row["label"] for row in rows]
    returns = [row["blended_return_pct"] for row in rows]
    symbols = {row["symbol"] for row in rows}

    summary = {
        "n_setups": n_setups,
        "n_symbols": len(symbols),
        "win_rate": sum(labels) / n_setups,
        "avg_return": statistics.mean(returns),
        "median_return": statistics.median(returns),
        "blowout_pct": blowout_pct,
        "entry_lead_td": entry_lead_td,
        "exit_lead_td": exit_lead_td,
        "require_blowout": require_blowout,
        "cost_pct": cost_pct,
        "min_gap_td": min_gap_td,
        "max_gap_td": max_gap_td,
        "source": source,
    }
    _log_summary(summary)
    return summary


def _log_summary(summary: dict) -> None:
    win_pct = summary["win_rate"] * 100.0
    blowout_line = (
        f"blowout>={summary['blowout_pct']:.1f}%"
        if summary["require_blowout"]
        else "all quarters (no blowout filter)"
    )
    logger.info(
        "EARNINGS DRIFT (%s): %s | entry %d td before / exit %d td before next earnings\n"
        "setups: %s across %s symbols | win%% %.1f%% | avg EV/trade %+.2f%% | median %+.1f%%\n"
        "gap %d-%d td | cost applied: %.2f%% | source=%s",
        blowout_line,
        summary["source"],
        summary["entry_lead_td"],
        summary["exit_lead_td"],
        f"{summary['n_setups']:,}",
        summary["n_symbols"],
        win_pct,
        summary["avg_return"],
        summary["median_return"],
        summary["min_gap_td"],
        summary["max_gap_td"],
        summary["cost_pct"],
        summary["source"],
    )


def _insert_rows(conn, rows: list[tuple]) -> None:
    for row in rows:
        conn.execute(_INSERT_STRATEGY_SETUP, row)


def build_earnings_dataset(
    symbols: list[str],
    *,
    blowout_pct: float = 5.0,
    entry_lead_td: int = 10,
    exit_lead_td: int = 1,
    require_blowout: bool = True,
    cost_pct: float = 0.20,
    min_gap_td: int = 40,
    max_gap_td: int = 90,
    source: str = "norgate",
    bars_db_path: Path = DEFAULT_BARS_DB_PATH,
    earnings_db_path: Path = DEFAULT_EARNINGS_DB_PATH,
    out_db_path: Path = DEFAULT_EARNINGS_STRATEGY_DB_PATH,
    replace: bool = True,
    earnings_dates_fn: Callable[[str], list[str]] | None = None,
) -> dict:
    """Build earnings-event setups and persist to strategy_setups."""
    work_symbols = [s.strip().upper() for s in symbols if s.strip()]
    lookup = earnings_dates_fn or (
        lambda sym: earnings_dates(sym, cache_db=earnings_db_path)
    )

    conn = connect(out_db_path)
    try:
        _ensure_schema(conn)
        if replace:
            conn.execute("DELETE FROM strategy_setups WHERE source = ?", (source,))
            conn.commit()

        created_at = now_iso()
        run_stats: list[dict] = []

        for symbol in work_symbols:
            bars = _load_all_bars(symbol, source=source, db_path=bars_db_path)
            earnings = lookup(symbol)
            rows = _symbol_setups(
                symbol,
                bars,
                earnings,
                blowout_pct=blowout_pct,
                entry_lead_td=entry_lead_td,
                exit_lead_td=exit_lead_td,
                require_blowout=require_blowout,
                cost_pct=cost_pct,
                min_gap_td=min_gap_td,
                max_gap_td=max_gap_td,
                source=source,
                created_at=created_at,
            )
            _insert_rows(conn, rows)
            conn.commit()
            run_stats.extend(
                {
                    "symbol": row[0],
                    "label": row[3],
                    "blended_return_pct": row[4],
                    "holding_days": row[6],
                }
                for row in rows
            )
            logger.info("  %s: %d setups", symbol, len(rows))

        return _compute_summary(
            run_stats,
            blowout_pct=blowout_pct,
            entry_lead_td=entry_lead_td,
            exit_lead_td=exit_lead_td,
            require_blowout=require_blowout,
            cost_pct=cost_pct,
            min_gap_td=min_gap_td,
            max_gap_td=max_gap_td,
            source=source,
        )
    finally:
        conn.close()


def _print_summary(summary: dict) -> None:
    print("=== Earnings drift dataset ===")
    print(f"source:           {summary['source']}")
    print(f"require_blowout:  {summary['require_blowout']}")
    print(f"blowout_pct:      {summary['blowout_pct']:.1f}%")
    print(f"entry_lead_td:    {summary['entry_lead_td']}")
    print(f"exit_lead_td:     {summary['exit_lead_td']}")
    print(f"gap_td:           {summary['min_gap_td']}-{summary['max_gap_td']}")
    print(f"cost_pct:         {summary['cost_pct']:.2f}%")
    print(f"n_setups:         {summary['n_setups']:,}")
    print(f"n_symbols:        {summary['n_symbols']:,}")
    print(f"win_rate:         {summary['win_rate'] * 100:.1f}%")
    print(f"avg_return:       {summary['avg_return']:+.2f}%")
    print(f"median_return:    {summary['median_return']:+.2f}%")


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build pre-earnings drift setups after blowout quarters"
    )
    parser.add_argument(
        "--symbols",
        help="Comma-separated equity symbols (e.g. AAPL,MSFT)",
    )
    parser.add_argument(
        "--symbols-from-cache",
        action="store_true",
        help="Use every symbol already cached for --source",
    )
    parser.add_argument(
        "--source",
        default="norgate",
        help="Bar/earnings source tag (default: norgate)",
    )
    parser.add_argument(
        "--blowout-pct",
        type=float,
        default=5.0,
        help="Min 2-day post-earnings reaction %% to qualify (default: 5.0)",
    )
    parser.add_argument(
        "--entry-lead-td",
        type=int,
        default=10,
        help="Trading days before next earnings to enter (default: 10)",
    )
    parser.add_argument(
        "--exit-lead-td",
        type=int,
        default=1,
        help="Trading days before next earnings to exit (default: 1)",
    )
    parser.add_argument(
        "--require-blowout",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Require prior-quarter blowout (default: true; --no-require-blowout for control)",
    )
    parser.add_argument(
        "--cost-pct",
        type=float,
        default=0.20,
        help="Round-trip cost deducted from return (default: 0.20)",
    )
    parser.add_argument(
        "--min-gap-td",
        type=int,
        default=40,
        help="Min trading days between consecutive earnings (default: 40)",
    )
    parser.add_argument(
        "--max-gap-td",
        type=int,
        default=90,
        help="Max trading days between consecutive earnings (default: 90)",
    )
    parser.add_argument(
        "--bars-db",
        type=Path,
        default=DEFAULT_BARS_DB_PATH,
        help="Daily bars DB path",
    )
    parser.add_argument(
        "--earnings-db",
        type=Path,
        default=DEFAULT_EARNINGS_DB_PATH,
        help="Earnings dates cache DB path",
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=DEFAULT_EARNINGS_STRATEGY_DB_PATH,
        help="Output strategy_setups DB path",
    )
    parser.add_argument(
        "--append",
        action="store_true",
        help="Append rows for this source instead of replacing prior rows",
    )
    args = parser.parse_args(argv)
    if not args.symbols and not args.symbols_from_cache:
        parser.error("provide --symbols or --symbols-from-cache")
    return args


def main(argv: list[str] | None = None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    args = _parse_args(argv)
    if args.symbols_from_cache:
        symbols = cached_symbols(args.source, db_path=args.bars_db)
        logger.info("Loaded %d symbols from cache (source=%s)", len(symbols), args.source)
    else:
        symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]

    summary = build_earnings_dataset(
        symbols,
        blowout_pct=args.blowout_pct,
        entry_lead_td=args.entry_lead_td,
        exit_lead_td=args.exit_lead_td,
        require_blowout=args.require_blowout,
        cost_pct=args.cost_pct,
        min_gap_td=args.min_gap_td,
        max_gap_td=args.max_gap_td,
        source=args.source,
        bars_db_path=args.bars_db,
        earnings_db_path=args.earnings_db,
        out_db_path=args.db,
        replace=not args.append,
    )
    _print_summary(summary)


if __name__ == "__main__":
    main()
