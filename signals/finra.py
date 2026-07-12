"""FINRA consolidated short interest — free, anonymous, published twice monthly.

FINRA's query API serves the consolidated (all-exchange) short interest dataset
without authentication. Data is inherently ~2 weeks stale: positions settle on
the 15th and month-end and publish about nine business days later. That lag is
the data's real precision — surface the settlement date, never pretend it's
live.
"""

from __future__ import annotations

import csv
import io
import logging
import time
from datetime import date, timedelta
from typing import Any

import httpx

logger = logging.getLogger(__name__)

FINRA_SHORT_INTEREST_URL = (
    "https://api.finra.org/data/group/otcMarket/name/consolidatedShortInterest"
)
_TIMEOUT = 20.0

# One number per symbol per ~2 weeks — cache a day, retry misses next call.
_CACHE_TTL_SECONDS = 86_400.0
_cache: dict[str, tuple[float, dict[str, Any]]] = {}


def _parse_rows(text: str) -> list[dict[str, str]]:
    reader = csv.DictReader(io.StringIO(text))
    return [row for row in reader if row.get("settlementDate")]


def _to_float(value: str | None) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


async def afetch_short_interest(symbol: str) -> dict[str, Any]:
    """Latest consolidated short interest for a symbol.

    Returns ``{}`` on any failure (fail-soft, like the finnhub helpers), else:
    ``short_shares``, ``days_to_cover``, ``change_pct`` (vs prior period),
    ``avg_daily_volume``, ``settlement_date``.
    """
    sym = symbol.strip().upper()
    if not sym:
        return {}

    entry = _cache.get(sym)
    if entry is not None and time.monotonic() - entry[0] <= _CACHE_TTL_SECONDS:
        return entry[1]

    since = (date.today() - timedelta(days=75)).isoformat()
    body = {
        "limit": 10,
        "compareFilters": [
            {"fieldName": "symbolCode", "fieldValue": sym, "compareType": "EQUAL"},
            {"fieldName": "settlementDate", "fieldValue": since, "compareType": "GTE"},
        ],
    }
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            res = await client.post(FINRA_SHORT_INTEREST_URL, json=body)
            res.raise_for_status()
            rows = _parse_rows(res.text)
    except Exception:
        logger.warning("FINRA short interest fetch failed for %s", sym, exc_info=True)
        return {}

    if not rows:
        return {}
    latest = max(rows, key=lambda r: r["settlementDate"])
    result = {
        "short_shares": _to_float(latest.get("currentShortPositionQuantity")),
        "days_to_cover": _to_float(latest.get("daysToCoverQuantity")),
        "change_pct": _to_float(latest.get("changePercent")),
        "avg_daily_volume": _to_float(latest.get("averageDailyVolumeQuantity")),
        "settlement_date": latest.get("settlementDate"),
    }
    if result["short_shares"] is None:
        return {}
    _cache[sym] = (time.monotonic(), result)
    return result
