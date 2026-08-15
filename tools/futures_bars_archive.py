"""Archive Schwab futures bars into data/futures_bars.db before retention drops them.

Schwab keeps ~45 days of 1-min futures bars and ~180 days of 30-min (measured on
/ES 2026-08-14); daily reaches years. Run daily (cron) so minute history
accumulates instead of evaporating. Idempotent: INSERT OR REPLACE on
(symbol, timeframe, ts).

Usage: .venv/bin/python tools/futures_bars_archive.py [--full]
  default: 1Min last 7d, 30Min last 14d, 1Day last 30d  (daily top-up)
  --full:  1Min last 50d, 30Min last 200d, 1Day last 15y (first run / gap repair)
"""

from __future__ import annotations

import argparse
import asyncio
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

from brokers.schwab_market_data import SchwabMarketData

SYMBOLS = ["/ES", "/NQ"]
DB_PATH = Path(__file__).resolve().parent.parent / "data" / "futures_bars.db"
REQUEST_SPACING_S = 1.0  # on top of the module's own WAF gate — never burst Schwab


def _open_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """CREATE TABLE IF NOT EXISTS bars (
            symbol TEXT NOT NULL,
            timeframe TEXT NOT NULL,
            ts TEXT NOT NULL,
            open REAL, high REAL, low REAL, close REAL, volume REAL,
            PRIMARY KEY (symbol, timeframe, ts)
        )"""
    )
    return conn


async def _pull(
    md: SchwabMarketData,
    conn: sqlite3.Connection,
    symbol: str,
    timeframe: str,
    days_back: int,
    window_days: int,
) -> int:
    now = datetime.now(UTC)
    total = 0
    start = now - timedelta(days=days_back)
    while start < now:
        end = min(start + timedelta(days=window_days), now)
        bars = await md.aget_stock_bars_range(
            symbol, timeframe=timeframe, start=start, end=end, extended_hours=True
        )
        conn.executemany(
            "INSERT OR REPLACE INTO bars VALUES (?,?,?,?,?,?,?,?)",
            [
                (symbol, timeframe, b.time, b.open, b.high, b.low, b.close,
                 getattr(b, "volume", None))
                for b in bars
            ],
        )
        conn.commit()
        total += len(bars)
        start = end
        await asyncio.sleep(REQUEST_SPACING_S)
    return total


async def main(full: bool) -> None:
    plan = (
        [("1Min", 50, 5), ("30Min", 200, 30), ("1Day", 15 * 365, 15 * 365)]
        if full
        else [("1Min", 7, 5), ("30Min", 14, 14), ("1Day", 30, 30)]
    )
    md = SchwabMarketData.from_settings()
    conn = _open_db()
    for symbol in SYMBOLS:
        for timeframe, days_back, window in plan:
            n = await _pull(md, conn, symbol, timeframe, days_back, window)
            print(f"{symbol} {timeframe}: upserted {n} bars")
    for symbol, timeframe, n, lo, hi in conn.execute(
        "SELECT symbol, timeframe, COUNT(*), MIN(ts), MAX(ts) FROM bars"
        " GROUP BY symbol, timeframe"
    ):
        print(f"store: {symbol} {timeframe}: {n} bars  {lo} .. {hi}")
    conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--full", action="store_true", help="deep first-run backfill")
    args = parser.parse_args()
    asyncio.run(main(args.full))
