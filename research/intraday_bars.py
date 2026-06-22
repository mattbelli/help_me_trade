"""Intraday bar cache — source-agnostic SQLite store for offline research."""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

from brokers.errors import BrokerError
from db.base import connect
from research.bars import RESEARCH_DATA_DIR
from signals.indicators import Bar

logger = logging.getLogger(__name__)

DEFAULT_INTRADAY_DB_PATH = RESEARCH_DATA_DIR / "intraday_bars.db"

_CREATE_MINUTE_BARS = """
CREATE TABLE IF NOT EXISTS minute_bars (
    symbol TEXT NOT NULL,
    ts TEXT NOT NULL,
    o REAL NOT NULL,
    h REAL NOT NULL,
    l REAL NOT NULL,
    c REAL NOT NULL,
    v REAL NOT NULL,
    source TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    PRIMARY KEY (symbol, ts, source, timeframe)
)
"""
_CREATE_MINUTE_BARS_INDEX = """
CREATE INDEX IF NOT EXISTS idx_minute_bars_symbol_source_ts
    ON minute_bars (symbol, source, ts)
"""

_CHUNK_MAX_DAYS = 30


class IntradayBar(Bar):
    t: str  # ISO-8601 UTC timestamp


def _ensure_schema(conn) -> None:
    conn.execute(_CREATE_MINUTE_BARS)
    conn.execute(_CREATE_MINUTE_BARS_INDEX)
    conn.commit()


def _normalize_symbol(symbol: str) -> str:
    return symbol.strip()


def _parse_ts(ts: str) -> datetime:
    text = ts.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    return datetime.fromisoformat(text)


def _candles_to_bars(payload: dict) -> list[IntradayBar]:
    """Convert a Schwab price-history JSON payload to IntradayBars."""
    candles = payload.get("candles") or []
    bars: list[IntradayBar] = []
    for candle in candles:
        t = datetime.fromtimestamp(candle["datetime"] / 1000, tz=UTC).isoformat()
        bars.append(
            {
                "t": t,
                "o": float(candle["open"]),
                "h": float(candle["high"]),
                "l": float(candle["low"]),
                "c": float(candle["close"]),
                "v": float(candle["volume"]),
            }
        )
    return bars


def _split_time_windows(
    start: datetime,
    end: datetime,
    *,
    max_days: int = _CHUNK_MAX_DAYS,
) -> list[tuple[datetime, datetime]]:
    windows: list[tuple[datetime, datetime]] = []
    cursor = start
    delta = timedelta(days=max_days)
    while cursor <= end:
        window_end = min(cursor + delta, end)
        windows.append((cursor, window_end))
        if window_end >= end:
            break
        cursor = window_end
    return windows


def _fetch_schwab_window(
    symbol: str,
    start: datetime,
    end: datetime,
) -> list[IntradayBar]:
    from brokers.schwab_auth import create_client

    client = create_client(interactive=False)
    resp = client.get_price_history_every_minute(
        symbol, start_datetime=start, end_datetime=end
    )
    if resp.status_code != 200:
        logger.warning(
            "%s: Schwab minute fetch HTTP %s for %s..%s",
            symbol,
            resp.status_code,
            start.isoformat(),
            end.isoformat(),
        )
        return []
    data = resp.json()
    if data.get("empty") or not data.get("candles"):
        logger.warning(
            "%s: Schwab minute fetch empty for %s..%s",
            symbol,
            start.isoformat(),
            end.isoformat(),
        )
        return []
    bars = _candles_to_bars(data)
    bars.sort(key=lambda bar: bar["t"])
    return bars


def _fetch_schwab(symbol: str, start: datetime, end: datetime) -> list[IntradayBar]:
    """Fetch 1-minute bars from Schwab, chunking long ranges and deduping overlaps."""
    by_ts: dict[str, IntradayBar] = {}
    for window_start, window_end in _split_time_windows(start, end):
        for bar in _fetch_schwab_window(symbol, window_start, window_end):
            by_ts[bar["t"]] = bar
    return sorted(by_ts.values(), key=lambda bar: bar["t"])


def cache_minute_bars(
    symbol: str,
    bars: list[IntradayBar],
    *,
    source: str = "schwab",
    timeframe: str = "1min",
    db_path: Path = DEFAULT_INTRADAY_DB_PATH,
) -> None:
    """Upsert minute bars (used by Schwab fetch and future loaders)."""
    sym = _normalize_symbol(symbol)
    if not sym or not bars:
        return
    conn = connect(db_path)
    try:
        _ensure_schema(conn)
        conn.executemany(
            """
            INSERT OR REPLACE INTO minute_bars
                (symbol, ts, o, h, l, c, v, source, timeframe)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    sym,
                    bar["t"],
                    bar["o"],
                    bar["h"],
                    bar["l"],
                    bar["c"],
                    bar["v"],
                    source,
                    timeframe,
                )
                for bar in bars
            ],
        )
        conn.commit()
    finally:
        conn.close()


def _load_cached(
    symbol: str,
    start: datetime,
    end: datetime,
    source: str,
    timeframe: str,
    db_path: Path,
) -> list[IntradayBar]:
    conn = connect(db_path)
    try:
        _ensure_schema(conn)
        rows = conn.execute(
            """
            SELECT ts, o, h, l, c, v
            FROM minute_bars
            WHERE symbol = ? AND source = ? AND timeframe = ?
              AND ts >= ? AND ts <= ?
            ORDER BY ts ASC
            """,
            (
                symbol,
                source,
                timeframe,
                start.isoformat(),
                end.isoformat(),
            ),
        ).fetchall()
    finally:
        conn.close()
    return [
        {
            "t": row["ts"],
            "o": row["o"],
            "h": row["h"],
            "l": row["l"],
            "c": row["c"],
            "v": row["v"],
        }
        for row in rows
    ]


def _needs_fetch(cached: list[IntradayBar], start: datetime, end: datetime) -> bool:
    if not cached:
        return True
    first = _parse_ts(cached[0]["t"])
    last = _parse_ts(cached[-1]["t"])
    return first > start or last < end


def cached_minute_symbols(
    source: str = "schwab",
    *,
    timeframe: str = "1min",
    db_path: Path = DEFAULT_INTRADAY_DB_PATH,
) -> list[str]:
    """Return the distinct symbols cached for a given source, sorted."""
    conn = connect(db_path)
    try:
        _ensure_schema(conn)
        rows = conn.execute(
            """
            SELECT DISTINCT symbol
            FROM minute_bars
            WHERE source = ? AND timeframe = ?
            ORDER BY symbol ASC
            """,
            (source, timeframe),
        ).fetchall()
    finally:
        conn.close()
    return [row["symbol"] for row in rows]


def get_minute_bars(
    symbol: str,
    start: datetime,
    end: datetime,
    *,
    source: str = "schwab",
    timeframe: str = "1min",
    db_path: Path = DEFAULT_INTRADAY_DB_PATH,
) -> list[IntradayBar]:
    """Return cached minute bars in [start, end], fetching and upserting gaps from the source."""
    sym = _normalize_symbol(symbol)
    cached = _load_cached(sym, start, end, source, timeframe, db_path)
    if _needs_fetch(cached, start, end):
        if source != "schwab":
            logger.warning(
                "%s: cache gap for source=%s (%s..%s); not auto-fetching. "
                "Pre-load this source's bars first.",
                sym,
                source,
                start.isoformat(),
                end.isoformat(),
            )
            return cached
        logger.info(
            "Fetching %s minute bars %s..%s (source=%s)",
            sym,
            start.isoformat(),
            end.isoformat(),
            source,
        )
        fetched = _fetch_schwab(sym, start, end)
        cache_minute_bars(
            sym, fetched, source=source, timeframe=timeframe, db_path=db_path
        )
        cached = _load_cached(sym, start, end, source, timeframe, db_path)
    return cached


def _parse_cli_date(text: str, *, end_of_day: bool = False) -> datetime:
    day = datetime.strptime(text, "%Y-%m-%d").date()
    if end_of_day:
        return datetime(day.year, day.month, day.day, 23, 59, 59, tzinfo=UTC)
    return datetime(day.year, day.month, day.day, tzinfo=UTC)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Pre-pull and cache 1-minute bars for offline research."
    )
    parser.add_argument("--symbol", required=True, help="Symbol (e.g. /ES)")
    parser.add_argument(
        "--start", required=True, help="Start date YYYY-MM-DD (00:00 UTC)"
    )
    parser.add_argument(
        "--end", required=True, help="End date YYYY-MM-DD (23:59:59 UTC)"
    )
    parser.add_argument("--source", default="schwab", help="Bar source (default: schwab)")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    args = _parse_args(argv)
    start = _parse_cli_date(args.start)
    end = _parse_cli_date(args.end, end_of_day=True)
    try:
        bars = get_minute_bars(args.symbol, start, end, source=args.source)
    except BrokerError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        print("Run `python -m scripts.schwab_login` first.", file=sys.stderr)
        return 1
    first_ts = bars[0]["t"] if bars else None
    last_ts = bars[-1]["t"] if bars else None
    print(
        f"symbol={args.symbol} source={args.source} count={len(bars)} "
        f"first={first_ts} last={last_ts}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
