"""Per-(symbol, timeframe) TTL cache for the watchlist poll ONLY.

The frontend polls /api/watchlist/market every few seconds and each uncached
poll fanned out one Schwab pricehistory call per symbol per timeframe (~480
req/min on a 24-symbol watchlist) — the actual source of the Akamai WAF 429/403
episodes. Daily bars move once a day and RVOL only changes per 5-minute bucket,
so short TTLs cost nothing functionally. Scoped to the watchlist poll on
purpose: RiskEngine / execution paths keep their own always-fresh fetches.

Lives here rather than in api/main.py so the poll's caching rules are readable
on their own; `market_session` is imported as a module-level name so tests can
patch the clock on THIS module.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone

from brokers.alpaca import StockBar
from brokers.market_clock import market_session
from trading.market_hours import _ET

BARS_TTL_S = {"1Day": 300.0, "5Min": 90.0}
# A stale-by-shape override may force a refetch at most once a minute per
# symbol — bounds the halted/no-today-bar retry rate (the retry-storm shape).
STALE_RECHECK_S = 60.0

bars_cache: dict[tuple[str, str], tuple[float, list]] = {}


def daily_series_stale(bars: list) -> bool:
    """True when a 1Day series is missing TODAY's developing bar during the
    regular session. TTL alone is blind to the open boundary: an entry cached
    at 09:29 serves until 09:34, and pre-open the series legitimately ends at
    YESTERDAY — so at the open every %chg it feeds shows yesterday's move as
    current (seen live 07-22 09:33 ET: SPY displayed +0.83, tape read −0.14)."""
    if not bars or market_session() != "regular":
        return False
    try:
        last = datetime.fromisoformat(str(bars[-1].time).replace("Z", "+00:00"))
        if last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return False
    return last.astimezone(_ET).date() < datetime.now(_ET).date()


async def cached_bars_batch(
    provider, symbols: list[str], *, timeframe: str = "1Day", days: int = 12
) -> dict[str, list[StockBar]]:
    now = time.time()
    ttl = BARS_TTL_S.get(timeframe, 90.0)
    out: dict[str, list[StockBar]] = {}
    missing: list[str] = []
    for sym in symbols:
        hit = bars_cache.get((sym, timeframe))
        if hit is not None and now - hit[0] < ttl:
            if (
                timeframe == "1Day"
                and now - hit[0] > STALE_RECHECK_S
                and daily_series_stale(hit[1])
            ):
                missing.append(sym)
            else:
                out[sym] = hit[1]
        else:
            missing.append(sym)
    if missing:
        fetched = await provider.aget_stock_bars_batch(missing, timeframe=timeframe, days=days)
        for sym in missing:
            bars = fetched.get(sym) or []
            # Cache empty results too — an unfetchable symbol otherwise
            # re-fetches on every poll (the retry-storm shape).
            bars_cache[(sym, timeframe)] = (now, bars)
            out[sym] = bars
    return out
