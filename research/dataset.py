"""Assemble labeled setup tables from cached daily bars."""

from __future__ import annotations

import argparse
import json
import logging
from datetime import date, timedelta
from pathlib import Path

from db.base import connect, now_iso
from research.bars import DEFAULT_BARS_DB_PATH, get_daily_bars
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


def _ensure_labeled_schema(conn) -> None:
    conn.execute(_CREATE_LABELED_SETUPS)
    conn.commit()


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
        created_at = now_iso()
        total_rows = 0
        wins = 0
        losses = 0
        timeouts = 0

        for symbol in symbols:
            sym = symbol.strip().upper()
            logger.info("Building labeled dataset for %s", sym)
            bars = get_daily_bars(
                sym,
                warmup_start,
                end,
                source=source,
                db_path=bars_db_path,
            )
            if len(bars) < MIN_BARS_FOR_CONTEXT + max_holding + 1:
                logger.warning("Insufficient bars for %s (%d)", sym, len(bars))
                continue

            symbol_rows = 0
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
                    wins += 1
                elif outcome == "loss":
                    losses += 1
                else:
                    timeouts += 1

                conn.execute(
                    """
                    INSERT INTO labeled_setups (
                        symbol, entry_date, features_json, label, outcome,
                        return_pct, holding_days, max_holding,
                        upper_atr_mult, lower_atr_mult, source, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
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
                    ),
                )
                symbol_rows += 1
                total_rows += 1

            conn.commit()
            logger.info("  %s: %d rows", sym, symbol_rows)

    finally:
        conn.close()

    return {
        "symbols": [s.strip().upper() for s in symbols],
        "rows": total_rows,
        "wins": wins,
        "losses": losses,
        "timeouts": timeouts,
        "date_range": {"start": start.isoformat(), "end": end.isoformat()},
    }


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build labeled setup dataset")
    parser.add_argument(
        "--symbols",
        required=True,
        help="Comma-separated equity symbols (e.g. AAPL,MSFT,NVDA)",
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
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    args = _parse_args(argv)
    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
    end = date.today()
    start = end - timedelta(days=args.years * 365)
    summary = build_labeled_dataset(
        symbols,
        start,
        end,
        entry_stride=args.stride,
    )
    logger.info("Dataset build complete: %s", summary)


if __name__ == "__main__":
    main()
