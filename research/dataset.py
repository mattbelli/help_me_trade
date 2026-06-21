"""Assemble labeled setup tables from cached daily bars."""

from __future__ import annotations

import argparse
import json
import logging
import os
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import date, timedelta
from pathlib import Path

from db.base import connect, now_iso
from research.bars import DEFAULT_BARS_DB_PATH, cached_symbols, get_daily_bars
from research.features import compute_features
from research.labeling import triple_barrier_label
from signals.trade_context import MIN_BARS_FOR_CONTEXT

logger = logging.getLogger(__name__)

RESEARCH_DATA_DIR = DEFAULT_BARS_DB_PATH.parent
DEFAULT_LABELED_DB_PATH = RESEARCH_DATA_DIR / "labeled.db"

_CREATE_LABELED_SETUPS = """
CREATE TABLE IF NOT EXISTS labeled_setups (
    id INTEGER PRIMARY KEY,
    symbol TEXT NOT NULL,
    entry_date TEXT NOT NULL,
    features_json TEXT NOT NULL,
    label INTEGER NOT NULL,
    outcome TEXT NOT NULL,
    return_pct REAL NOT NULL,
    holding_days INTEGER NOT NULL,
    max_holding INTEGER NOT NULL,
    upper_atr_mult REAL NOT NULL,
    lower_atr_mult REAL NOT NULL,
    source TEXT NOT NULL,
    created_at TEXT NOT NULL
)
"""

_INSERT_LABELED_SETUP = """
INSERT INTO labeled_setups (
    symbol, entry_date, features_json, label, outcome,
    return_pct, holding_days, max_holding,
    upper_atr_mult, lower_atr_mult, source, created_at
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""

_WORKER_BENCHMARK_BARS: list | None = None


def _ensure_labeled_schema(conn) -> None:
    conn.execute(_CREATE_LABELED_SETUPS)
    conn.commit()


def _init_label_worker(benchmark_bars: list) -> None:
    global _WORKER_BENCHMARK_BARS
    _WORKER_BENCHMARK_BARS = benchmark_bars


def _label_symbol_rows(task: tuple) -> tuple[str, list[tuple], dict]:
    """Label one symbol; return (symbol, rows, counts). Pure CPU + bars-db reads."""
    (
        symbol,
        start,
        end,
        warmup_start,
        source,
        bars_db_path,
        upper_atr_mult,
        lower_atr_mult,
        max_holding,
        entry_stride,
        created_at,
    ) = task

    sym = symbol.strip().upper()
    benchmark_bars = _WORKER_BENCHMARK_BARS
    if benchmark_bars is None:
        raise RuntimeError("label worker not initialized with benchmark bars")

    bars = get_daily_bars(
        sym,
        warmup_start,
        end,
        source=source,
        db_path=bars_db_path,
    )
    counts = {"rows": 0, "wins": 0, "losses": 0, "timeouts": 0}
    if len(bars) < MIN_BARS_FOR_CONTEXT + max_holding + 1:
        logger.warning("Insufficient bars for %s (%d)", sym, len(bars))
        return sym, [], counts

    rows: list[tuple] = []
    min_entry_idx = MIN_BARS_FOR_CONTEXT - 1
    max_entry_idx = len(bars) - 1 - max_holding

    for idx in range(min_entry_idx, max_entry_idx + 1, entry_stride):
        entry_date = date.fromisoformat(bars[idx]["d"])
        if entry_date < start or entry_date > end:
            continue

        features = compute_features(bars, idx, benchmark_bars=benchmark_bars)
        label_result = triple_barrier_label(
            bars,
            idx,
            upper_atr_mult=upper_atr_mult,
            lower_atr_mult=lower_atr_mult,
            max_holding=max_holding,
        )
        if features is None or label_result is None:
            continue

        outcome = label_result["outcome"]
        if outcome == "win":
            counts["wins"] += 1
        elif outcome == "loss":
            counts["losses"] += 1
        else:
            counts["timeouts"] += 1

        rows.append(
            (
                sym,
                bars[idx]["d"],
                json.dumps(features),
                label_result["label"],
                outcome,
                label_result["return_pct"],
                label_result["holding_days"],
                max_holding,
                upper_atr_mult,
                lower_atr_mult,
                source,
                created_at,
            )
        )
        counts["rows"] += 1

    return sym, rows, counts


def _insert_labeled_rows(conn, rows: list[tuple]) -> None:
    for row in rows:
        conn.execute(_INSERT_LABELED_SETUP, row)


def _accumulate_counts(totals: dict, counts: dict) -> None:
    totals["rows"] += counts["rows"]
    totals["wins"] += counts["wins"]
    totals["losses"] += counts["losses"]
    totals["timeouts"] += counts["timeouts"]


def _label_tasks(
    symbols: list[str],
    *,
    start: date,
    end: date,
    warmup_start: date,
    source: str,
    bars_db_path: Path,
    upper_atr_mult: float,
    lower_atr_mult: float,
    max_holding: int,
    entry_stride: int,
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
            upper_atr_mult,
            lower_atr_mult,
            max_holding,
            entry_stride,
            created_at,
        )
        for symbol in symbols
    ]


def build_labeled_dataset(
    symbols: list[str],
    start: date,
    end: date,
    *,
    benchmark: str = "SPY",
    upper_atr_mult: float = 2.0,
    lower_atr_mult: float = 2.0,
    max_holding: int = 10,
    entry_stride: int = 1,
    source: str = "alpaca",
    bars_db_path: Path = DEFAULT_BARS_DB_PATH,
    labeled_db_path: Path = DEFAULT_LABELED_DB_PATH,
    replace: bool = True,
    jobs: int = 1,
) -> dict:
    """Build labeled rows for each symbol and persist to labeled_setups."""
    # ~252 trading bars of warmup need ~1.5x calendar days (weekends/holidays),
    # so features for entries at `start` have full history (else they're skipped).
    warmup_start = start - timedelta(
        days=int(MIN_BARS_FOR_CONTEXT * 1.5) + max_holding + 30
    )
    benchmark_bars = get_daily_bars(
        benchmark,
        warmup_start,
        end,
        source=source,
        db_path=bars_db_path,
    )

    conn = connect(labeled_db_path)
    try:
        _ensure_labeled_schema(conn)
        # Re-running a full-universe build should replace that source's rows, not
        # silently append duplicates. Opt into append via replace=False / --append.
        if replace:
            conn.execute("DELETE FROM labeled_setups WHERE source = ?", (source,))
            conn.commit()

        created_at = now_iso()
        totals = {"rows": 0, "wins": 0, "losses": 0, "timeouts": 0}
        tasks = _label_tasks(
            symbols,
            start=start,
            end=end,
            warmup_start=warmup_start,
            source=source,
            bars_db_path=bars_db_path,
            upper_atr_mult=upper_atr_mult,
            lower_atr_mult=lower_atr_mult,
            max_holding=max_holding,
            entry_stride=entry_stride,
            created_at=created_at,
        )

        global _WORKER_BENCHMARK_BARS
        _WORKER_BENCHMARK_BARS = benchmark_bars

        if jobs <= 1:
            for task in tasks:
                sym = task[0].strip().upper()
                logger.info("Building labeled dataset for %s", sym)
                _, rows, counts = _label_symbol_rows(task)
                _insert_labeled_rows(conn, rows)
                conn.commit()
                _accumulate_counts(totals, counts)
                logger.info("  %s: %d rows", sym, counts["rows"])
        else:
            completed = 0
            with ProcessPoolExecutor(
                max_workers=jobs,
                initializer=_init_label_worker,
                initargs=(benchmark_bars,),
            ) as executor:
                futures = {
                    executor.submit(_label_symbol_rows, task): task[0].strip().upper()
                    for task in tasks
                }
                for future in as_completed(futures):
                    sym, rows, counts = future.result()
                    _insert_labeled_rows(conn, rows)
                    conn.commit()
                    _accumulate_counts(totals, counts)
                    completed += 1
                    if completed % 100 == 0 or completed == len(futures):
                        logger.info(
                            "Progress: %d / %d symbols (%d rows so far)",
                            completed,
                            len(futures),
                            totals["rows"],
                        )
                    logger.info("  %s: %d rows", sym, counts["rows"])

    finally:
        conn.close()

    return {
        "symbols": [s.strip().upper() for s in symbols],
        "rows": totals["rows"],
        "wins": totals["wins"],
        "losses": totals["losses"],
        "timeouts": totals["timeouts"],
        "date_range": {"start": start.isoformat(), "end": end.isoformat()},
    }


def _default_jobs() -> int:
    return max(1, (os.cpu_count() or 2) - 2)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build labeled setup dataset")
    parser.add_argument(
        "--symbols",
        help="Comma-separated equity symbols (e.g. AAPL,MSFT,NVDA)",
    )
    parser.add_argument(
        "--symbols-from-cache",
        action="store_true",
        help="Use every symbol already cached for --source (e.g. a Norgate universe)",
    )
    parser.add_argument(
        "--source",
        default="alpaca",
        help="Bar source to label (default: alpaca; use 'norgate' for the real run)",
    )
    parser.add_argument(
        "--years",
        type=int,
        default=5,
        help="Years of history ending today (default: 5)",
    )
    parser.add_argument(
        "--stride",
        type=int,
        default=5,
        help="Entry index stride for subsampling (default: 5)",
    )
    parser.add_argument(
        "--append",
        action="store_true",
        help=(
            "Append rows for this source instead of replacing prior rows "
            "(default: delete existing rows for --source before insert)"
        ),
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
    summary = build_labeled_dataset(
        symbols,
        start,
        end,
        entry_stride=args.stride,
        source=args.source,
        replace=not args.append,
        jobs=args.jobs,
    )
    logger.info("Dataset build complete: %s", summary)


if __name__ == "__main__":
    main()
