"""Past earnings announcement dates from SEC EDGAR 8-K Item 2.02.

Free, keyless, no account: an 8-K carrying Item 2.02 (Results of Operations) is the
legally-required earnings release, so its acceptance timestamp IS the announcement.
That makes it the only past-earnings source that doesn't gate the feature on a
brokerage account or a per-user API key.

PAST dates only — an 8-K is filed after the fact. The FORWARD calendar stays with
Alpha Vantage; earnings_drift needs both (the past date to score the prior blowout
reaction, the future one to time entry).

Why not the alternatives: Finnhub's free /calendar/earnings serves only a rolling
~30-day window (a 2-year range returns nothing), so it can never supply a prior
date. Finnhub /stock/earnings has dates, but its `period` is the fiscal period END
(AAPL 2026-03-31), not the announce date (2026-04-30) — ~30 days off, which would
score the blowout against unrelated bars.

Deep history lives in the older `filings.files[]` shards; research/earnings.py walks
those for backtests. The live calendar only needs recent events, so this stays at one
request per symbol.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, time

import httpx

from research.fundamentals import (
    COMPANY_TICKERS_URL,
    DEFAULT_USER_AGENT,
    SUBMISSIONS_URL,
    _base_ticker,
)

try:  # py>=3.9 stdlib; guarded so an odd env degrades to "no hour" not a crash
    from zoneinfo import ZoneInfo

    _ET: ZoneInfo | None = ZoneInfo("America/New_York")
except Exception:  # pragma: no cover - defensive
    _ET = None

logger = logging.getLogger(__name__)

EARNINGS_ITEM = "2.02"  # Results of Operations and Financial Condition
SOURCE = "edgar"

_MARKET_OPEN = time(9, 30)
_MARKET_CLOSE = time(16, 0)
# SEC fair use is 10 req/s. Stay well under: ~7-8/s -> a 503-symbol sweep is ~65s,
# and this runs once a day.
_REQUEST_INTERVAL_SEC = 0.13
_TIMEOUT = 30.0
# Recent events are all the live calendar needs; a typical filer's `recent` block
# holds years of 8-Ks anyway.
DEFAULT_LIMIT_PER_SYMBOL = 8


def session_hour(acceptance: str | None) -> str | None:
    """`bmo` / `amc` / `dmh` from an 8-K acceptanceDateTime, or None if unparseable.

    The stamps are UTC ("2026-04-30T20:30:41.000Z"). Converting to Eastern FIRST is
    load-bearing: compared raw, every before-open filer reads as intraday (JPM files
    at 10:30Z = 06:30 ET = bmo).
    """
    if not acceptance or _ET is None:
        return None
    try:
        dt = datetime.fromisoformat(str(acceptance).strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    t = dt.astimezone(_ET).time()
    if t < _MARKET_OPEN:
        return "bmo"
    if t >= _MARKET_CLOSE:
        return "amc"
    return "dmh"


def parse_recent_earnings(
    recent: dict,
    *,
    limit: int = DEFAULT_LIMIT_PER_SYMBOL,
) -> list[dict[str, str | None]]:
    """8-K Item 2.02 events from a `filings.recent` block, newest first.

    EDGAR returns these arrays newest-first and column-aligned, so index i is one
    filing across every array.
    """
    forms = recent.get("form") or []
    filing_dates = recent.get("filingDate") or []
    items_list = recent.get("items") or []
    accepted = recent.get("acceptanceDateTime") or []

    out: list[dict[str, str | None]] = []
    for i, form in enumerate(forms):
        if form != "8-K":
            continue
        if EARNINGS_ITEM not in (items_list[i] if i < len(items_list) else "" or ""):
            continue
        if i >= len(filing_dates):
            continue
        out.append(
            {
                "date": str(filing_dates[i]),
                "hour": session_hour(accepted[i] if i < len(accepted) else None),
            }
        )
        if len(out) >= limit:
            break
    return out


async def _aload_ticker_cik_map(client: httpx.AsyncClient) -> dict[str, str]:
    resp = await client.get(COMPANY_TICKERS_URL)
    resp.raise_for_status()
    mapping: dict[str, str] = {}
    for entry in resp.json().values():
        try:
            mapping[str(entry["ticker"]).strip().upper()] = str(entry["cik_str"]).strip()
        except (KeyError, TypeError, ValueError):
            continue
    return mapping


async def afetch_edgar_earnings(
    symbols: list[str],
    *,
    limit_per_symbol: int = DEFAULT_LIMIT_PER_SYMBOL,
    user_agent: str = DEFAULT_USER_AGENT,
) -> list[dict[str, str | None]]:
    """Recent past earnings dates for `symbols` -> [{symbol, date, hour, source}].

    Fail-soft per symbol and overall: a dead ticker map or a bad symbol yields fewer
    rows, never an exception — the caller upserts whatever arrived.
    """
    if not symbols:
        return []
    rows: list[dict[str, str | None]] = []
    headers = {"User-Agent": user_agent}
    async with httpx.AsyncClient(
        timeout=_TIMEOUT, headers=headers, follow_redirects=True
    ) as client:
        try:
            ticker_cik = await _aload_ticker_cik_map(client)
        except Exception:
            logger.warning(
                "EDGAR ticker->CIK map fetch failed — skipping EDGAR earnings this pass",
                exc_info=True,
            )
            return []

        empty = 0
        for sym in symbols:
            cik = ticker_cik.get(_base_ticker(sym))
            if not cik:
                empty += 1
                continue
            await asyncio.sleep(_REQUEST_INTERVAL_SEC)
            try:
                resp = await client.get(SUBMISSIONS_URL.format(cik=int(cik)))
                resp.raise_for_status()
                recent = (resp.json().get("filings") or {}).get("recent") or {}
            except Exception as exc:
                logger.debug(
                    "EDGAR submissions fetch failed for %s (CIK %s): %s", sym, cik, exc
                )
                continue
            found = parse_recent_earnings(recent, limit=limit_per_symbol)
            if not found:
                empty += 1
                continue
            upper = sym.strip().upper()
            rows.extend({"symbol": upper, "source": SOURCE, **r} for r in found)

    if empty:
        # Expected causes: an unmapped/foreign ticker, or a reorganisation that moved
        # the archive to a predecessor CIK while company_tickers.json points at the
        # successor (XOM -> "ExxonMobil Holdings Corp", zero 8-Ks, empty formerNames).
        # Live self-heals once the successor files its next 8-K; logged so a
        # persistent gap is visible rather than silently absent.
        logger.info(
            "EDGAR earnings: %d/%d symbols yielded no 8-K Item %s "
            "(unmapped ticker or a reorg moved the archive to a predecessor CIK)",
            empty,
            len(symbols),
            EARNINGS_ITEM,
        )
    logger.info("EDGAR earnings: %d dates across %d symbols", len(rows), len(symbols))
    return rows
