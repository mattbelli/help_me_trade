#!/usr/bin/env python3
"""Underlying trade tape for the classification lab — S_t at exchange timestamps.

`References/CLASSIFICATION_LAB_PREREG_2026-07-16.md` method 2 sources the underlying
from "historical equity trades REST (exchange timestamps on both sides — capture-lag
sync noise does not apply offline)". This fetches exactly that: Alpaca
``/v2/stocks/{symbol}/trades`` over the two gauge windows plus their shoulders, written
as ``data/research/underlying_trades/<date>.jsonl.gz`` in the ``{ts_ms, sym, price}``
schema tools/classification_lab_scorer.py reads.

    uv run python tools/underlying_trades_fetch.py --date 2026-07-20
    uv run python tools/underlying_trades_fetch.py --date 2026-07-20 --symbols SPY,QQQ

Read-only market data — no orders, nothing under trading/. Index roots (SPX/SPXW) have
no equity tape at all; the prereg's SPY x10 proxy is applied by the scorer, so this tool
just fetches SPY and skips the index roots.
"""

from __future__ import annotations

import argparse
import gzip
import json
import logging
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from config.settings import alpaca_config  # noqa: E402
from signals.options_flow.constants import GAUGE_UNDERLYINGS  # noqa: E402
from signals.options_flow.opening_pressure import WINDOWS  # noqa: E402

logger = logging.getLogger("underlying_trades_fetch")

_ET = ZoneInfo("America/New_York")
OUT_DIR = ROOT / "data" / "research" / "underlying_trades"

# The scorer reads S at each print's timestamp, and prints are captured with 5-minute
# shoulders, so the tape must cover the shoulders too or the window's first prints have
# no S_{t-1}.
SHOULDER_MINUTES = 5
PAGE_LIMIT = 10_000
# Alpaca paginates /trades exactly like /bars: follow ``next_page_token`` or you silently
# keep only page 1 (BUILD_LOG 2026-06-23 — the stale-candle bug). Cap + warn so a silent
# truncation can never recur.
MAX_PAGES = 200

# Index roots have no equity tape; SPY carries them via the registered x10 proxy.
INDEX_ROOTS = frozenset({"SPX", "SPXW"})
DEFAULT_SYMBOLS = tuple(sorted((set(GAUGE_UNDERLYINGS) - INDEX_ROOTS) | {"SPY"}))


def window_ranges(day: date) -> list[tuple[datetime, datetime]]:
    """The gauge windows widened by the capture shoulders, as ET datetimes."""
    out: list[tuple[datetime, datetime]] = []
    for start, length in WINDOWS.values():
        base = datetime(day.year, day.month, day.day, tzinfo=_ET)
        out.append(
            (
                base + timedelta(minutes=start - SHOULDER_MINUTES),
                base + timedelta(minutes=start + length + SHOULDER_MINUTES),
            )
        )
    return sorted(out)


def parse_trade_ts_ms(value: str) -> int | None:
    """RFC3339 with up to nanosecond precision — fromisoformat caps at microseconds."""
    text = str(value).strip().replace("Z", "+00:00")
    if "." in text:
        head, rest = text.split(".", 1)
        digits = ""
        while rest and rest[0].isdigit():
            digits, rest = digits + rest[0], rest[1:]
        text = f"{head}.{digits[:6]:0<6}{rest}"
    try:
        return int(datetime.fromisoformat(text).timestamp() * 1000)
    except ValueError:
        return None


def fetch_trades(
    client: httpx.Client,
    data_base_url: str,
    symbol: str,
    start: datetime,
    end: datetime,
    feed: str | None = None,
) -> list[dict]:
    """Every trade for one symbol over one window, following ``next_page_token``."""
    params: dict[str, str | int] = {
        "start": start.astimezone(ZoneInfo("UTC")).isoformat().replace("+00:00", "Z"),
        "end": end.astimezone(ZoneInfo("UTC")).isoformat().replace("+00:00", "Z"),
        "limit": PAGE_LIMIT,
        "sort": "asc",
    }
    if feed:
        params["feed"] = feed
    rows: list[dict] = []
    page_token: str | None = None
    for _ in range(MAX_PAGES):
        request_params = dict(params)
        if page_token:
            request_params["page_token"] = page_token
        response = client.get(f"{data_base_url}/v2/stocks/{symbol}/trades", params=request_params)
        if response.status_code != 200:
            raise RuntimeError(
                f"GET /v2/stocks/{symbol}/trades failed ({response.status_code}): "
                f"{response.text[:200]}"
            )
        payload = response.json()
        for trade in payload.get("trades") or []:
            ts_ms = parse_trade_ts_ms(trade.get("t"))
            price = trade.get("p")
            if ts_ms is None or not price or float(price) <= 0:
                continue
            rows.append({"ts_ms": ts_ms, "sym": symbol, "price": float(price)})
        page_token = payload.get("next_page_token")
        if not page_token:
            break
    else:
        logger.warning(
            "trade pagination capped at %d pages for %s %s — tape may be TRUNCATED",
            MAX_PAGES,
            symbol,
            params["start"],
        )
    return rows


def fetch_day(
    client: httpx.Client,
    data_base_url: str,
    day: date,
    symbols: list[str],
    feed: str | None = None,
) -> tuple[list[dict], dict[str, int]]:
    # Accumulates the whole day in memory before writing — peaks at a few hundred MB on a
    # busy session across the full symbol list, which is fine for a one-shot research run.
    rows: list[dict] = []
    counts: dict[str, int] = {}
    for symbol in symbols:
        got = 0
        for start, end in window_ranges(day):
            fetched = fetch_trades(client, data_base_url, symbol, start, end, feed)
            rows.extend(fetched)
            got += len(fetched)
        counts[symbol] = got
        if got == 0:
            logger.error("no trades for %s on %s — method 2 has no S for this name", symbol, day)
    rows.sort(key=lambda r: (r["ts_ms"], r["sym"]))
    return rows, counts


def write_capture(rows: list[dict], day: date, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{day.isoformat()}.jsonl.gz"
    tmp = path.with_suffix(".gz.part")
    with gzip.open(tmp, "wt") as fh:
        for row in rows:
            fh.write(json.dumps(row) + "\n")
    tmp.replace(path)
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--date", required=True, help="session to fetch (ISO)")
    parser.add_argument(
        "--symbols",
        default=",".join(DEFAULT_SYMBOLS),
        help="comma-separated equity symbols (default: the gauge universe + SPY, index roots skipped)",
    )
    parser.add_argument("--out-dir", type=Path, default=OUT_DIR)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    day = date.fromisoformat(args.date)
    symbols = [
        s.strip().upper()
        for s in args.symbols.split(",")
        if s.strip() and s.strip().upper() not in INDEX_ROOTS
    ]
    cfg = alpaca_config()
    headers = {
        "APCA-API-KEY-ID": cfg["api_key_id"],
        "APCA-API-SECRET-KEY": cfg["api_secret_key"],
    }
    feed = cfg["data_feed"] if cfg["data_feed"] != "auto" else None
    with httpx.Client(headers=headers, timeout=30.0) as client:
        rows, counts = fetch_day(client, cfg["data_base_url"], day, symbols, feed)
    path = write_capture(rows, day, args.out_dir)
    logger.info("wrote %d trades to %s", len(rows), path)
    for symbol, count in sorted(counts.items()):
        logger.info("  %-6s %d", symbol, count)
    return 0 if all(counts.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
