"""Databento /ES loader — deep 1-min OHLCV into the intraday cache, with pre-flight cost.

Databento (CME GLBX.MDP3) serves /ES back to 2010 with true orderflow schemas Schwab
lacks (tbbo/trades/mbp/mbo). This module pulls the cheap ohlcv-1m schema into the same
SQLite minute cache as the Schwab data, tagged source="databento" so the two never mix.

Cost is usage-based; `estimate_cost` is a FREE metadata query — always estimate before
pulling. Run: python -m research.databento_loader --estimate   (then --pull to download)
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

from db.base import ROOT
from research.intraday_bars import (
    DEFAULT_INTRADAY_DB_PATH,
    IntradayBar,
    cache_minute_bars,
)

load_dotenv(ROOT / ".env")

DATASET = "GLBX.MDP3"
DEFAULT_SYMBOL = "ES.c.0"  # continuous front-month
DEFAULT_STORE_SYMBOL = "/ES"
INCEPTION = "2010-06-06"


def _client():
    import databento as db

    key = os.environ.get("DATABENTO_API_KEY", "").strip()
    if not key:
        raise RuntimeError("DATABENTO_API_KEY missing from .env")
    return db.Historical(key)


def estimate_cost(
    *,
    symbol: str = DEFAULT_SYMBOL,
    schema: str = "ohlcv-1m",
    start: str = INCEPTION,
    end: str | None = None,
    stype_in: str = "continuous",
) -> tuple[float, int]:
    """Return (estimated USD cost, billable bytes). FREE metadata query — no download."""
    client = _client()
    end = end or datetime.now(timezone.utc).date().isoformat()
    kw = dict(
        dataset=DATASET,
        symbols=[symbol],
        schema=schema,
        start=start,
        end=end,
        stype_in=stype_in,
    )
    cost = float(client.metadata.get_cost(mode="historical-streaming", **kw))
    size = int(client.metadata.get_billable_size(**kw))
    return cost, size


def _records_to_bars(df) -> list[IntradayBar]:
    """Convert a databento ohlcv-1m DataFrame (ts_event index, float prices) to IntradayBars."""
    bars: list[IntradayBar] = []
    for ts, row in df.iterrows():
        pts = ts.to_pydatetime()
        if pts.tzinfo is None:
            pts = pts.replace(tzinfo=timezone.utc)
        else:
            pts = pts.astimezone(timezone.utc)
        bars.append(
            {
                "t": pts.isoformat(),
                "o": float(row["open"]),
                "h": float(row["high"]),
                "l": float(row["low"]),
                "c": float(row["close"]),
                "v": float(row["volume"]),
            }
        )
    return bars


def pull_ohlcv_1m(
    *,
    symbol: str = DEFAULT_SYMBOL,
    start: str = INCEPTION,
    end: str | None = None,
    store_symbol: str = DEFAULT_STORE_SYMBOL,
    source: str = "databento",
    db_path: Path = DEFAULT_INTRADAY_DB_PATH,
) -> int:
    """Download ohlcv-1m and cache it. SPENDS CREDITS. Returns rows cached."""
    client = _client()
    end = end or datetime.now(timezone.utc).date().isoformat()
    data = client.timeseries.get_range(
        dataset=DATASET,
        symbols=[symbol],
        schema="ohlcv-1m",
        start=start,
        end=end,
        stype_in="continuous",
    )
    df = data.to_df(price_type="float")
    if df.empty:
        return 0
    bars = _records_to_bars(df)
    cache_minute_bars(store_symbol, bars, source=source, db_path=db_path)
    return len(bars)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Databento /ES 1-min loader (estimate / pull).")
    p.add_argument("--symbol", default=DEFAULT_SYMBOL, help="Databento symbol (default ES.c.0)")
    p.add_argument("--start", default=INCEPTION, help="Start YYYY-MM-DD")
    p.add_argument("--end", default=None, help="End YYYY-MM-DD (default: today)")
    p.add_argument("--store-symbol", default=DEFAULT_STORE_SYMBOL)
    p.add_argument("--estimate", action="store_true", help="Print cost estimate only (free)")
    p.add_argument("--pull", action="store_true", help="Download + cache (SPENDS CREDITS)")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    cost, size = estimate_cost(symbol=args.symbol, start=args.start, end=args.end)
    print(f"estimate: {args.symbol} ohlcv-1m {args.start}..{args.end or 'today'}")
    print(f"  cost=${cost:.4f}  billable={size / 1e6:.2f} MB")
    if not args.pull:
        print("(estimate only — pass --pull to download)")
        return 0
    n = pull_ohlcv_1m(
        symbol=args.symbol,
        start=args.start,
        end=args.end,
        store_symbol=args.store_symbol,
    )
    print(f"cached {n} bars under symbol={args.store_symbol} source=databento")
    return 0


if __name__ == "__main__":
    sys.exit(main())
