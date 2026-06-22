"""Daily bar cache — source-agnostic SQLite store for offline research."""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date, datetime, timezone
from pathlib import Path

from brokers.alpaca import StockBar
from brokers.errors import BrokerError
from db.base import ROOT, connect
from signals.trade_context import DatedBar, _stockbar_to_dated

logger = logging.getLogger(__name__)

RESEARCH_DATA_DIR = ROOT / "data" / "research"
DEFAULT_BARS_DB_PATH = RESEARCH_DATA_DIR / "bars.db"

_CREATE_DAILY_BARS = """
CREATE TABLE IF NOT EXISTS daily_bars (
    symbol TEXT NOT NULL,
    date TEXT NOT NULL,
    o REAL NOT NULL,
    h REAL NOT NULL,
    l REAL NOT NULL,
    c REAL NOT NULL,
    v REAL NOT NULL,
    source TEXT NOT NULL,
    PRIMARY KEY (symbol, date, source)
)
"""
_CREATE_DAILY_BARS_INDEX = """
CREATE INDEX IF NOT EXISTS idx_daily_bars_symbol_date
    ON daily_bars (symbol, date)
"""


def _ensure_schema(conn) -> None:
    conn.execute(_CREATE_DAILY_BARS)
    conn.execute(_CREATE_DAILY_BARS_INDEX)
    conn.commit()


def _daily_candles_to_bars(payload: dict) -> list[DatedBar]:
    """Convert a Schwab daily price-history JSON payload to DatedBars."""
    candles = payload.get("candles") or []
    by_date: dict[str, DatedBar] = {}
    for candle in candles:
        d = (
            datetime.fromtimestamp(candle["datetime"] / 1000, tz=timezone.utc)
            .date()
            .isoformat()
        )
        by_date[d] = {
            "d": d,
            "o": float(candle["open"]),
            "h": float(candle["high"]),
            "l": float(candle["low"]),
            "c": float(candle["close"]),
            "v": float(candle["volume"]),
        }
    return sorted(by_date.values(), key=lambda bar: bar["d"])


def _fetch_schwab_daily(symbol: str, start: date, end: date) -> list[DatedBar]:
    """Fetch daily bars from Schwab (e.g. deep /ES futures history)."""
    from brokers.schwab_auth import create_client

    client = create_client(interactive=False)
    start_dt = datetime(start.year, start.month, start.day, tzinfo=timezone.utc)
    end_dt = datetime(end.year, end.month, end.day, 23, 59, 59, tzinfo=timezone.utc)
    resp = client.get_price_history_every_day(
        symbol, start_datetime=start_dt, end_datetime=end_dt
    )
    if resp.status_code != 200:
        logger.warning(
            "%s: Schwab daily fetch HTTP %s for %s..%s",
            symbol,
            resp.status_code,
            start.isoformat(),
            end.isoformat(),
        )
        return []
    data = resp.json()
    if data.get("empty") or not data.get("candles"):
        logger.warning(
            "%s: Schwab daily fetch empty for %s..%s",
            symbol,
            start.isoformat(),
            end.isoformat(),
        )
        return []
    return _daily_candles_to_bars(data)


def _fetch_alpaca(symbol: str, start: date, end: date) -> list[DatedBar]:
    """Fetch daily bars from Alpaca (dev/testing adapter; Norgate path plugs in via cache_bars)."""
    from brokers.factory import get_broker

    broker = get_broker("alpaca")
    start_dt = datetime(start.year, start.month, start.day, tzinfo=timezone.utc)
    end_dt = datetime(end.year, end.month, end.day, 23, 59, 59, tzinfo=timezone.utc)
    stock_bars: list[StockBar] = broker.get_stock_bars_range(
        symbol, timeframe="1Day", start=start_dt, end=end_dt
    )
    dated = [_stockbar_to_dated(bar) for bar in stock_bars]
    dated.sort(key=lambda b: b["d"])
    return dated


def cache_bars(
    symbol: str,
    bars: list[DatedBar],
    *,
    source: str = "alpaca",
    db_path: Path = DEFAULT_BARS_DB_PATH,
) -> None:
    """Upsert daily bars (used by Alpaca fetch and future Norgate loaders)."""
    sym = symbol.strip().upper()
    if not sym or not bars:
        return
    conn = connect(db_path)
    try:
        _ensure_schema(conn)
        conn.executemany(
            """
            INSERT OR REPLACE INTO daily_bars
                (symbol, date, o, h, l, c, v, source)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    sym,
                    bar["d"],
                    bar["o"],
                    bar["h"],
                    bar["l"],
                    bar["c"],
                    bar["v"],
                    source,
                )
                for bar in bars
            ],
        )
        conn.commit()
    finally:
        conn.close()


def _load_cached(
    symbol: str,
    start: date,
    end: date,
    source: str,
    db_path: Path,
) -> list[DatedBar]:
    conn = connect(db_path)
    try:
        _ensure_schema(conn)
        rows = conn.execute(
            """
            SELECT date, o, h, l, c, v
            FROM daily_bars
            WHERE symbol = ? AND source = ? AND date >= ? AND date <= ?
            ORDER BY date ASC
            """,
            (symbol, source, start.isoformat(), end.isoformat()),
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


def _needs_fetch(cached: list[DatedBar], start: date, end: date) -> bool:
    if not cached:
        return True
    first = date.fromisoformat(cached[0]["d"])
    last = date.fromisoformat(cached[-1]["d"])
    return first > start or last < end


def cached_symbols(
    source: str = "alpaca",
    *,
    db_path: Path = DEFAULT_BARS_DB_PATH,
) -> list[str]:
    """Return the distinct symbols cached for a given source, sorted."""
    conn = connect(db_path)
    try:
        _ensure_schema(conn)
        rows = conn.execute(
            "SELECT DISTINCT symbol FROM daily_bars WHERE source = ? ORDER BY symbol ASC",
            (source,),
        ).fetchall()
    finally:
        conn.close()
    return [row["symbol"] for row in rows]


def get_daily_bars(
    symbol: str,
    start: date,
    end: date,
    *,
    source: str = "alpaca",
    db_path: Path = DEFAULT_BARS_DB_PATH,
) -> list[DatedBar]:
    """Return cached daily bars in [start, end], fetching and upserting gaps from the source."""
    sym = symbol.strip().upper()
    cached = _load_cached(sym, start, end, source, db_path)
    if _needs_fetch(cached, start, end):
        if source == "alpaca":
            logger.info("Fetching %s bars %s..%s (source=%s)", sym, start, end, source)
            fetched = _fetch_alpaca(sym, start, end)
            cache_bars(sym, fetched, source=source, db_path=db_path)
            cached = _load_cached(sym, start, end, source, db_path)
        elif source == "schwab":
            logger.info("Fetching %s bars %s..%s (source=%s)", sym, start, end, source)
            fetched = _fetch_schwab_daily(sym, start, end)
            cache_bars(sym, fetched, source=source, db_path=db_path)
            cached = _load_cached(sym, start, end, source, db_path)
        else:
            # Never auto-fetch from another source to fill a gap (e.g. Norgate):
            # it would tag survivor-biased data as survivorship-free and silently
            # poison the dataset. Norgate bars are pre-loaded via research.norgate_loader.
            logger.warning(
                "%s: cache gap for source=%s (%s..%s); not auto-fetching. "
                "Pre-load this source's bars first.",
                sym,
                source,
                start,
                end,
            )
    return cached


def _parse_cli_date(text: str) -> date:
    return datetime.strptime(text, "%Y-%m-%d").date()


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Pre-pull and cache daily bars for offline research."
    )
    parser.add_argument("--symbol", required=True, help="Symbol (e.g. /ES)")
    parser.add_argument("--start", required=True, help="Start date YYYY-MM-DD")
    parser.add_argument("--end", required=True, help="End date YYYY-MM-DD")
    parser.add_argument(
        "--source", default="alpaca", help="Bar source (default: alpaca)"
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    args = _parse_args(argv)
    start = _parse_cli_date(args.start)
    end = _parse_cli_date(args.end)
    try:
        bars = get_daily_bars(args.symbol, start, end, source=args.source)
    except BrokerError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        print("Run `python -m scripts.schwab_login` first.", file=sys.stderr)
        return 1
    first_d = bars[0]["d"] if bars else None
    last_d = bars[-1]["d"] if bars else None
    print(
        f"symbol={args.symbol} source={args.source} count={len(bars)} "
        f"first={first_d} last={last_d}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

