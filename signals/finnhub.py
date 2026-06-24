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


async def afetch_eps_surprises(
    symbol: str,
    *,
    limit: int = 8,
    token: str | None = None,
    timeout: float = _TIMEOUT,
) -> list[dict]:
    """Return per-quarter EPS surprise data for a single symbol, most-recent-first.

    Queries Finnhub's ``/stock/earnings`` endpoint which returns historical EPS
    actuals, estimates, and surprise percentages — all available on the free tier.

    Returns a list of dicts (up to ``limit``, most-recent-first):
        {
            "period": "YYYY-MM-DD",
            "year": int,
            "quarter": int,
            "eps_estimate": float | None,
            "eps_actual": float | None,
            "surprise_pct": float | None,
        }

    Fail-soft: returns ``[]`` on missing key, HTTP error, or any exception.
    """
    token = token or finnhub_api_key()
    if not token:
        logger.warning("FINNHUB_API_KEY not set — skipping EPS surprise fetch")
        return []

    sym = symbol.strip().upper()

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(
                f"{FINNHUB_BASE}/stock/earnings",
                params={"symbol": sym, "token": token},
            )
            resp.raise_for_status()
            raw = resp.json()
    except Exception as exc:  # noqa: BLE001 — fail-soft for endpoint
        logger.warning("Finnhub EPS surprise fetch for %s failed: %s", sym, exc)
        return []

    if not isinstance(raw, list):
        logger.warning("Finnhub EPS surprise for %s: unexpected payload type %s", sym, type(raw))
        return []

    results: list[dict] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        try:
            period = str(entry.get("period") or "").strip()
            year_raw = entry.get("year")
            quarter_raw = entry.get("quarter")
            estimate_raw = entry.get("estimate")
            actual_raw = entry.get("actual")
            surprise_pct_raw = entry.get("surprisePercent")

            year = int(year_raw) if year_raw is not None else None
            quarter = int(quarter_raw) if quarter_raw is not None else None
            eps_estimate = float(estimate_raw) if estimate_raw is not None else None
            eps_actual = float(actual_raw) if actual_raw is not None else None
            surprise_pct = float(surprise_pct_raw) if surprise_pct_raw is not None else None

            if not period or year is None or quarter is None:
                continue

            results.append(
                {
                    "period": period,
                    "year": year,
                    "quarter": quarter,
                    "eps_estimate": eps_estimate,
                    "eps_actual": eps_actual,
                    "surprise_pct": surprise_pct,
                }
            )
        except (TypeError, ValueError, KeyError):
            continue

    # Most-recent-first by period end (don't rely on Finnhub's ordering).
    results.sort(key=lambda e: e["period"], reverse=True)
    return results[:limit]


def finnhub_is_configured() -> bool:
    return bool(finnhub_api_key())
