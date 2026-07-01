"""Finnhub market-data client — earnings calendar (recent + upcoming).

Read-only market data; NOT a broker/trading connection. The earnings calendar
endpoint returns every company reporting in a date range, so a single call covers
the whole universe's quarter. Free tier is 60 req/min — a daily refresh uses one.
"""

from __future__ import annotations

import logging
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any

import httpx

from config.settings import finnhub_api_key

logger = logging.getLogger(__name__)

FINNHUB_BASE = "https://finnhub.io/api/v1"
_TIMEOUT = 30.0

# --- Tiny in-process TTL cache (respect the free-tier rate limit) --------------
# Profile + metrics change slowly (~daily); company news is refreshed every few
# minutes. Keyed by (kind, symbol); only successful, non-empty results are cached
# so a missing key or transient failure is retried on the next call.
_CACHE_TTL_SECONDS = {"profile": 86_400.0, "metrics": 86_400.0, "news": 900.0}
_company_cache: dict[tuple[str, str], tuple[float, Any]] = {}


def _cache_get(kind: str, symbol: str) -> Any | None:
    entry = _company_cache.get((kind, symbol))
    if entry is None:
        return None
    stored_at, value = entry
    if time.monotonic() - stored_at > _CACHE_TTL_SECONDS[kind]:
        _company_cache.pop((kind, symbol), None)
        return None
    return value


def _cache_set(kind: str, symbol: str, value: Any) -> None:
    _company_cache[(kind, symbol)] = (time.monotonic(), value)


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


# --- Company-info fetchers (profile · fundamentals · news) ---------------------
# All fail-soft: an unset key or a failed request returns a safe empty value and
# never raises, so a missing Finnhub key just omits these sections in the UI.

# Curated subset of ``/stock/profile2`` — the fields the company-info modal shows.
_PROFILE_KEYS = (
    "name",
    "ticker",
    "exchange",
    "finnhubIndustry",
    "marketCapitalization",
    "logo",
    "weburl",
    "ipo",
    "shareOutstanding",
    "country",
    "currency",
)

# Curated fundamentals pulled from ``/stock/metric``'s ``.metric`` block. Each
# entry lists the Finnhub field name(s) to try, first-non-null wins.
_METRIC_FIELDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("pe_ttm", ("peTTM", "peBasicExclExtraTTM")),
    ("eps_ttm", ("epsTTM", "epsBasicExclExtraItemsTTM")),
    ("week_52_high", ("52WeekHigh",)),
    ("week_52_low", ("52WeekLow",)),
    ("beta", ("beta",)),
    ("div_yield", ("dividendYieldIndicatedAnnual", "currentDividendYieldTTM")),
    ("gross_margin", ("grossMarginTTM",)),
    ("net_margin", ("netProfitMarginTTM",)),
    ("market_cap", ("marketCapitalization",)),
    ("ps_ttm", ("psTTM",)),
    ("roe_ttm", ("roeTTM",)),
    ("revenue_growth_yoy", ("revenueGrowthTTMYoy",)),
)


async def afetch_company_profile(
    symbol: str,
    *,
    token: str | None = None,
    timeout: float = _TIMEOUT,
) -> dict[str, Any]:
    """Company profile from ``/stock/profile2`` (name, exchange, industry, logo…).

    Returns a trimmed dict (``_PROFILE_KEYS``) or ``{}`` on missing key / any error.
    Cached ~24h per symbol.
    """
    if not token and not finnhub_is_configured():
        return {}
    token = token or finnhub_api_key()
    sym = symbol.strip().upper()

    cached = _cache_get("profile", sym)
    if cached is not None:
        return cached

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(
                f"{FINNHUB_BASE}/stock/profile2",
                params={"symbol": sym, "token": token},
            )
            resp.raise_for_status()
            raw = resp.json()
    except Exception as exc:  # noqa: BLE001 — fail-soft for endpoint
        logger.warning("Finnhub company profile fetch for %s failed: %s", sym, exc)
        return {}

    if not isinstance(raw, dict):
        return {}
    profile = {k: raw[k] for k in _PROFILE_KEYS if raw.get(k) not in (None, "")}
    if profile:
        _cache_set("profile", sym, profile)
    return profile


async def afetch_company_metrics(
    symbol: str,
    *,
    token: str | None = None,
    timeout: float = _TIMEOUT,
) -> dict[str, Any]:
    """Curated fundamentals from ``/stock/metric?metric=all`` → flat ``{key: value}``.

    Pulls P/E, 52-week high/low, beta, dividend yield, margins, market cap, etc.
    Returns ``{}`` on missing key / any error. Cached ~24h per symbol.
    """
    if not token and not finnhub_is_configured():
        return {}
    token = token or finnhub_api_key()
    sym = symbol.strip().upper()

    cached = _cache_get("metrics", sym)
    if cached is not None:
        return cached

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(
                f"{FINNHUB_BASE}/stock/metric",
                params={"symbol": sym, "metric": "all", "token": token},
            )
            resp.raise_for_status()
            raw = resp.json()
    except Exception as exc:  # noqa: BLE001 — fail-soft for endpoint
        logger.warning("Finnhub company metrics fetch for %s failed: %s", sym, exc)
        return {}

    metric = raw.get("metric") if isinstance(raw, dict) else None
    if not isinstance(metric, dict):
        return {}

    metrics: dict[str, Any] = {}
    for out_key, source_keys in _METRIC_FIELDS:
        for src in source_keys:
            val = metric.get(src)
            if val is not None:
                metrics[out_key] = val
                break
    if metrics:
        _cache_set("metrics", sym, metrics)
    return metrics


async def afetch_company_news(
    symbol: str,
    *,
    days: int = 7,
    limit: int = 15,
    token: str | None = None,
    timeout: float = _TIMEOUT,
) -> list[dict[str, Any]]:
    """Recent company news from ``/company-news`` (last ``days``), newest-first.

    Returns up to ``limit`` items of ``{headline, source, url, datetime (ISO),
    summary, image}`` or ``[]`` on missing key / any error. Cached ~15min per symbol.
    """
    if not token and not finnhub_is_configured():
        return []
    token = token or finnhub_api_key()
    sym = symbol.strip().upper()

    cached = _cache_get("news", sym)
    if cached is not None:
        return cached[:limit]

    today = date.today()
    from_date = (today - timedelta(days=days)).isoformat()
    to_date = today.isoformat()

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(
                f"{FINNHUB_BASE}/company-news",
                params={"symbol": sym, "from": from_date, "to": to_date, "token": token},
            )
            resp.raise_for_status()
            raw = resp.json()
    except Exception as exc:  # noqa: BLE001 — fail-soft for endpoint
        logger.warning("Finnhub company news fetch for %s failed: %s", sym, exc)
        return []

    if not isinstance(raw, list):
        return []

    items: list[dict[str, Any]] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        headline = str(entry.get("headline") or "").strip()
        url = str(entry.get("url") or "").strip()
        if not headline or not url:
            continue
        epoch = entry.get("datetime")
        dt_iso: str | None = None
        if isinstance(epoch, (int, float)) and epoch > 0:
            try:
                dt_iso = datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat()
            except (OverflowError, OSError, ValueError):
                dt_iso = None
        items.append(
            {
                "headline": headline,
                "source": str(entry.get("source") or "").strip(),
                "url": url,
                "datetime": dt_iso,
                "summary": str(entry.get("summary") or "").strip(),
                "image": str(entry.get("image") or "").strip(),
            }
        )

    items.sort(key=lambda e: e["datetime"] or "", reverse=True)
    if items:
        _cache_set("news", sym, items)
    return items[:limit]


def finnhub_is_configured() -> bool:
    return bool(finnhub_api_key())
