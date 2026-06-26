from __future__ import annotations

import csv
import io
import logging

import httpx

from config.settings import alphavantage_api_key

logger = logging.getLogger(__name__)

ALPHAVANTAGE_BASE = "https://www.alphavantage.co/query"
_TIMEOUT = 30.0


class AlphaVantageError(Exception):
    pass


def alphavantage_is_configured() -> bool:
    return bool(alphavantage_api_key())


def _map_hour(time_of_day: str) -> str:
    t = (time_of_day or "").strip().lower()
    if t == "pre-market":
        return "bmo"
    if t == "post-market":
        return "amc"
    return ""


async def afetch_earnings_calendar(
    horizon: str = "6month",
    *,
    token: str | None = None,
    timeout: float = _TIMEOUT,
) -> list[dict[str, str]]:
    """Whole forward earnings calendar in one call. Returns [{symbol, date, hour}].

    Fail-soft: returns [] (logs a warning) on a missing key, a rate-limit/error body
    (AV returns a non-CSV JSON-ish note when throttled), or any network error.
    """
    token = token or alphavantage_api_key()
    if not token:
        logger.warning("ALPHAVANTAGE_API_KEY not set — skipping AV earnings calendar")
        return []
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(
                ALPHAVANTAGE_BASE,
                params={
                    "function": "EARNINGS_CALENDAR",
                    "horizon": horizon,
                    "apikey": token,
                },
            )
            resp.raise_for_status()
            text = resp.text
    except Exception:
        logger.warning("AlphaVantage earnings calendar request failed", exc_info=True)
        return []

    # A valid response is CSV starting with the known header; a throttle/error is a
    # JSON-ish note ("Information"/"Note"/"Error Message") — treat that as empty.
    if not text.lstrip().lower().startswith("symbol,"):
        logger.warning(
            "AlphaVantage earnings calendar returned a non-CSV body (rate-limit/error): %s",
            text[:200],
        )
        return []

    rows: list[dict[str, str]] = []
    reader = csv.DictReader(io.StringIO(text))
    for entry in reader:
        symbol = str(entry.get("symbol", "")).strip().upper()
        date = str(entry.get("reportDate", "")).strip()
        if not symbol or not date:
            continue
        rows.append(
            {
                "symbol": symbol,
                "date": date,
                "hour": _map_hour(entry.get("timeOfTheDay", "")),
            }
        )
    return rows
