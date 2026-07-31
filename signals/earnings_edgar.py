"""Past earnings announcement dates from SEC EDGAR 8-K Item 2.02.

Free, keyless, no account: an 8-K carrying Item 2.02 (Results of Operations) is the
legally-required earnings release, so its acceptance timestamp IS the announcement.
That makes it the only past-earnings source that doesn't gate the feature on a
brokerage account or a per-user API key.

PAST dates only from the filings themselves — an 8-K is filed after the fact. The
CONFIRMED forward calendar stays with Alpha Vantage; earnings_drift needs both (the
past date to score the prior blowout reaction, the future one to time entry).

What this module adds on top of the raw dates is a CADENCE PROJECTION: a filer
repeats itself, so last filing + the name's own median inter-filing gap is a usable
"next earnings ≈" for the thousands of corpus names no vendor calendar covers. It
is an estimate and is stored as one (`estimated = 1`) — never a confirmed date.
The projection deliberately uses DATES ONLY: the bmo/amc hour derived from an
acceptance timestamp is known-biased, and a projected hour would be a guess about
a guess.

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
import statistics
from datetime import date as _date
from datetime import datetime, time, timedelta

import httpx

from research.fundamentals import (
    COMPANY_TICKERS_URL,
    DEFAULT_USER_AGENT,
    SUBMISSIONS_URL,
    _base_ticker,
)

# Registered fund share classes (ETFs/mutual funds) — a separate SEC ticker file
# from the operating-company one, and the only way to tell an ETF apart from a
# ticker we simply failed to resolve.
FUND_TICKERS_URL = "https://www.sec.gov/files/company_tickers_mf.json"

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

ESTIMATE_SOURCE = "edgar_estimate"

# Cadence-projection constants.
# A quarterly filer's gap; used only when a name has too little history to have a
# median of its own (one filing, or a fresh IPO).
FALLBACK_GAP_DAYS = 91
# Two Item-2.02 8-Ks this close are one event filed twice (release + supplemental
# or an amended exhibit), not two quarters. Counting the pair would drag the median
# gap toward zero and project "earnings tomorrow" forever.
MIN_EVENT_SEPARATION_DAYS = 30
# How much silence retires a filer. Measured against the name's OWN cadence rather
# than a flat day count, so an annual filer is not written off at month seven:
# past this, the company has plausibly stopped reporting (acquired, delisted, gone
# dark) and projecting a date would be fabrication, so we report "unknown" instead.
MAX_MISSED_CYCLES = 2
STALE_SLACK_DAYS = 30


def _event_dates(dates: list[str]) -> list[_date]:
    """Sorted unique filing dates, collapsing same-event duplicates."""
    parsed: list[_date] = []
    for d in dates:
        try:
            parsed.append(_date.fromisoformat(str(d).strip()[:10]))
        except ValueError:
            continue
    events: list[_date] = []
    for d in sorted(set(parsed)):
        if not events or (d - events[-1]).days >= MIN_EVENT_SEPARATION_DAYS:
            events.append(d)
    return events


def median_gap_days(dates: list[str]) -> int | None:
    """Median days between a name's consecutive earnings filings (None if < 2)."""
    events = _event_dates(dates)
    if len(events) < 2:
        return None
    gaps = [(b - a).days for a, b in zip(events, events[1:], strict=False)]
    return int(round(statistics.median(gaps)))


def estimate_next_earnings(
    dates: list[str], *, today: str
) -> tuple[str, int] | None:
    """(projected next date, gap used) from filing cadence — None when unprojectable.

    Roll forward from the last filing in whole cadence steps so the answer is
    always a future date: a name whose last filing was one cycle ago projects to
    the cycle after it, not to a date already in the past. Returns None when the
    history is too stale to stand behind (see MAX_MISSED_CYCLES).
    """
    events = _event_dates(dates)
    if not events:
        return None
    gap = median_gap_days(dates) or FALLBACK_GAP_DAYS
    last = events[-1]
    today_d = _date.fromisoformat(today)
    if (today_d - last).days > MAX_MISSED_CYCLES * gap + STALE_SLACK_DAYS:
        return None
    nxt = last + timedelta(days=gap)
    while nxt <= today_d:
        nxt += timedelta(days=gap)
    return nxt.isoformat(), gap


def session_hour(acceptance: str | None) -> str | None:
    """`bmo` / `amc` / `dmh` from an OFFSET-BEARING acceptance timestamp.

    ⚠️ Returns None for `data.sec.gov/submissions/CIK*.json`'s
    `acceptanceDateTime`, and that is the fix, not a regression. That field ends
    in `Z` but the value is only sometimes UTC — measured 2026-07-30 on 18
    earnings 8-Ks across six mega-caps against EDGAR's own ACCEPTANCE-DATETIME
    header: 8 carry an Eastern value with a spurious `Z`, 10 are genuine UTC,
    and nothing in the record says which you hold. This function used to assume
    UTC and convert, which labelled AMZN's 16:06 ET release `dmh` (12:06) — the
    founder caught it because the stock popped after 4pm, not at lunch.

    The two sources that ARE trustworthy both state their offset:
    `signals.earnings_exhibit.parse_current_feed` (the atom feed's `<updated>`,
    verified DST-aware and agreeing with the native header on both filer kinds),
    and the native `<ACCEPTANCE-DATETIME>` in `<accession>-index-headers.html`.
    Feed either of those in and the hour is real.
    """
    if not acceptance or _ET is None:
        return None
    text = str(acceptance).strip()
    if text.upper().endswith("Z"):
        # Rejected by shape, because `fromisoformat` happily accepts `Z` as UTC
        # and that is exactly the lie. A genuinely-UTC producer writes `+00:00`.
        return None
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        return None
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


async def _aload_fund_tickers(client: httpx.AsyncClient) -> dict[str, str]:
    """{ticker: CIK} for registered fund share classes (ETFs, mutual funds).

    A '40 Act fund files N-CSR / N-PORT and never an 8-K Item 2.02, so a hit here
    settles the question without spending a submissions request: the name HAS no
    earnings, which is a fact about it rather than a hole in our data.

    Load-bearing because the operating-company map cannot answer for these at all
    — measured 2026-07-29 against the flow corpus: 3,870 of 5,939 names resolve as
    operating companies, 1,866 resolve ONLY here (ARKG, FBT, every leveraged
    single-stock ETF), and 203 resolve in neither.
    """
    try:
        resp = await client.get(FUND_TICKERS_URL)
        resp.raise_for_status()
        payload = resp.json()
        fields = payload["fields"]
        sym_i, cik_i = fields.index("symbol"), fields.index("cik")
    except Exception:
        # Fail-soft: without it funds just fall through to a submissions fetch,
        # which reaches the same verdict at the cost of a request.
        logger.warning("EDGAR fund ticker map fetch failed", exc_info=True)
        return {}
    mapping: dict[str, str] = {}
    for row in payload.get("data") or []:
        try:
            mapping[str(row[sym_i]).strip().upper()] = str(row[cik_i]).strip()
        except (IndexError, TypeError, ValueError):
            continue
    return mapping


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


def classify_coverage(
    cik: str | None,
    dates: list[str],
    *,
    today: str,
) -> dict[str, object]:
    """Per-symbol coverage verdict from one EDGAR lookup.

    The three outcomes are kept apart on purpose (they are three different
    sentences on a card): `dated` — we can project a next date; `non_filer` — we
    resolved the company and it files no Item 2.02 at all, so "no earnings" is the
    CORRECT answer and the name never needs fetching again soon (ETFs, trusts);
    `unknown` — we could not resolve the ticker, or its history is too stale to
    project from, which is an admission and not a fact about the company.
    """
    state = "unknown"
    next_date: str | None = None
    gap: int | None = None
    events = _event_dates(dates)
    if cik and not events:
        state = "non_filer"
    elif events:
        projected = estimate_next_earnings(dates, today=today)
        if projected:
            state = "dated"
            next_date, gap = projected
        else:
            gap = median_gap_days(dates)
    return {
        "state": state,
        "n_filings": len(events),
        "last_filing_date": events[-1].isoformat() if events else None,
        "median_gap_days": gap,
        "next_estimated_date": next_date,
    }


async def afetch_edgar_filings(
    symbols: list[str],
    *,
    limit_per_symbol: int = DEFAULT_LIMIT_PER_SYMBOL,
    user_agent: str = DEFAULT_USER_AGENT,
) -> dict[str, dict[str, object]]:
    """Per-symbol EDGAR lookup -> {SYMBOL: {cik, events}} for symbols we reached.

    A symbol is ABSENT from the result when the lookup itself failed (network,
    5xx) — the caller must not read that as "no filings" and record a verdict off
    it. A resolved-but-earningsless company IS present, with an empty `events`
    list: that is the non-filer signal. Fund share classes are answered from the
    ticker map alone (see `_aload_fund_tickers`) — no request per ETF.
    """
    if not symbols:
        return {}
    out: dict[str, dict[str, object]] = {}
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
            return {}
        fund_cik = await _aload_fund_tickers(client)

        for sym in symbols:
            upper = sym.strip().upper()
            base = _base_ticker(sym)
            cik = ticker_cik.get(base)
            if not cik:
                fund = fund_cik.get(base)
                # A fund files no earnings by construction — settled, no request.
                # Unmapped by BOTH: we reached EDGAR and learned nothing.
                out[upper] = {"cik": fund, "events": []}
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
            out[upper] = {
                "cik": cik,
                "events": parse_recent_earnings(recent, limit=limit_per_symbol),
            }
    return out


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
    fetched = await afetch_edgar_filings(
        symbols, limit_per_symbol=limit_per_symbol, user_agent=user_agent
    )
    rows: list[dict[str, str | None]] = []
    empty = 0
    for sym in symbols:
        found = (fetched.get(sym.strip().upper()) or {}).get("events") or []
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
