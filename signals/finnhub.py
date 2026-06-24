"""Finnhub market-data client — earnings calendar (recent + upcoming).

Read-only market data; NOT a broker/trading connection. The earnings calendar
endpoint returns every company reporting in a date range, so a single call covers
the whole universe's quarter. Free tier is 60 req/min — a daily refresh uses one.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta

import httpx

from config.settings import finnhub_api_key

logger = logging.getLogger(__name__)

FINNHUB_BASE = "https://finnhub.io/api/v1"
_TIMEOUT = 30.0


class FinnhubError(RuntimeError):
    pass


def _parse_calendar(payload: dict) -> list[dict[str, str]]:
    raw = payload.get("earningsCalendar") if isinstance(payload, dict) else None
    rows: list[dict[str, str]] = []
    for entry in raw or []:
        if not isinstance(entry, dict):
            continue
        symbol = str(entry.get("symbol", "")).strip().upper()
        date = str(entry.get("date", "")).strip()
        if not symbol or not date:
            continue
        rows.append({"symbol": symbol, "date": date, "hour": str(entry.get("hour") or "")})
    return rows


def fetch_earnings_calendar(
    from_date: str,
    to_date: str,
    *,
    token: str | None = None,
    timeout: float = _TIMEOUT,
) -> list[dict[str, str]]:
    """Earnings reporting between from_date..to_date (YYYY-MM-DD), all companies.

    Returns [{symbol, date, hour}]. Raises FinnhubError when the key is missing or
    the request fails — callers (the scheduled refresh) log and skip, never crash.
    """
    token = token or finnhub_api_key()
    if not token:
        raise FinnhubError("FINNHUB_API_KEY not set")
    try:
        resp = httpx.get(
            f"{FINNHUB_BASE}/calendar/earnings",
            params={"from": from_date, "to": to_date, "token": token},
            timeout=timeout,
        )
        resp.raise_for_status()
        return _parse_calendar(resp.json())
    except httpx.HTTPError as exc:
        raise FinnhubError(f"earnings calendar request failed: {exc}") from exc


async def afetch_earnings_calendar(
    from_date: str,
    to_date: str,
    *,
    token: str | None = None,
    timeout: float = _TIMEOUT,
) -> list[dict[str, str]]:
    """Async variant for the scheduled refresh loop."""
    token = token or finnhub_api_key()
    if not token:
        raise FinnhubError("FINNHUB_API_KEY not set")
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(
                f"{FINNHUB_BASE}/calendar/earnings",
                params={"from": from_date, "to": to_date, "token": token},
            )
            resp.raise_for_status()
            return _parse_calendar(resp.json())
    except httpx.HTTPError as exc:
        raise FinnhubError(f"earnings calendar request failed: {exc}") from exc


async def afetch_past_earnings_dates(
    symbol: str,
    *,
    lookback_days: int = 730,
    token: str | None = None,
    timeout: float = _TIMEOUT,
) -> list[str]:
    """Return past earnings dates for a single symbol, most-recent-first.

    Queries Finnhub's earnings calendar from ~lookback_days ago to today,
    filters to events on or before today, and returns YYYY-MM-DD strings.
    Fail-soft: returns [] on missing key, non-200, or any other error.
    """
    token = token or finnhub_api_key()
    if not token:
        logger.warning("FINNHUB_API_KEY not set — skipping past earnings fetch")
        return []

    today = date.today()
    from_date = (today - timedelta(days=lookback_days)).isoformat()
    to_date = today.isoformat()
    sym = symbol.strip().upper()

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(
                f"{FINNHUB_BASE}/calendar/earnings",
                params={"symbol": sym, "from": from_date, "to": to_date, "token": token},
            )
            resp.raise_for_status()
            rows = _parse_calendar(resp.json())
    except Exception as exc:  # noqa: BLE001 — fail-soft for endpoint
        logger.warning("Finnhub past earnings fetch for %s failed: %s", sym, exc)
        return []

    past = sorted(
        {r["date"] for r in rows if r["symbol"] == sym and r["date"] <= to_date},
        reverse=True,
    )
    return past


def finnhub_is_configured() -> bool:
    return bool(finnhub_api_key())
