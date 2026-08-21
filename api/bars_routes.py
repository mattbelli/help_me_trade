"""Chart bars: ``/api/bars`` — the newest window first, older pages on demand.

Card #326. TradingView's datafeed contract: the chart asks for the newest N
bars (one or two upstream round trips), paints, and asks for "N more before
<time>" only when the user pans to the left edge. Nothing here knows how far
back a timeframe "should" go — the user's scrolling decides.

One bar store per (symbol, timeframe, ext): the newest window, kept fresh by
refetching the tail since its last bar (one small page) whenever it goes stale,
and re-baselined hourly (a fresh newest-N walk) so split adjustments and
upstream revisions propagate. Older pages are fetched on demand and returned,
never stored — they are immutable history the browser holds.

What this replaced (2026-08-17): a (symbol, timeframe, DAYS) cache whose cold
miss was a 14-page sequential walk (SLV 1Hour/180d: 5.6s measured), plus a
prewarm loop, a serve-stale window and an incremental-vs-full refresh path,
all built to hide that walk. ``_BARS_CACHE`` keeps its (expires, response)
value shape because ``api/fusion_jobs`` reads it for last prices;
``api/scan_routes`` fetches daily bars through ``_get_bars_cached``.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from api.alpaca_guard import _require_alpaca
from brokers.alpaca_tier import data_tier
from brokers.bar_builder import bar_session, parse_ts_ms, splice_vendor_tail
from brokers.errors import BrokerError
from brokers.factory import get_broker, get_market_data_provider
from brokers.market_clock import market_session as _market_session

logger = logging.getLogger(__name__)
router = APIRouter()

# The newest window a chart asks for by default, and the most any request may ask.
DEFAULT_LIMIT = 300
MAX_LIMIT = 5000

# (symbol, timeframe, ext) -> (tail fresh until [monotonic], response dict). The
# response's ``bars`` is the whole stored window, ascending; ``has_more`` says
# whether the vendor had older history when the window was fetched.
_BARS_CACHE: dict[tuple, tuple[float, dict]] = {}
# In-flight fetch tasks — concurrent identical requests await the same task
# instead of multiplying upstream fetches. Store refreshes key on the store key;
# older pages on (store key, before, limit).
_BARS_INFLIGHT: dict[tuple, "asyncio.Task[dict | None]"] = {}
# How long a stored tail is trusted before the next request refetches it. The
# forming bars ride the WS stream + the hub splice below, so this only paces how
# often COMPLETED bars are reconciled with the vendor.
_BARS_CACHE_TTL = {
    "1Min": 60.0,
    "5Min": 180.0,
    "15Min": 300.0,
    "1Hour": 600.0,
    "4Hour": 600.0,
    "1Day": 300.0,
    "1Week": 300.0,
    "1Month": 300.0,
}
# The tail refresh keeps old bars verbatim, so a split (Alpaca adjusts with
# adjustment=split) would leave the pre-split scale on them. Re-fetch the whole
# window fresh at least this often; also bounds how large a tail-grown store gets.
_BARS_FULL_REBASELINE = 3600.0
_BARS_FULL_AT: dict[tuple, float] = {}
_BARS_MAX_ENTRIES = 256
# Timeframes Schwab builds by aggregating 1Day/30Min in-process — bucket stamps
# depend on the fetch window, so futures re-fetch the whole window instead of
# merging a tail (a truncated window duplicates or downgrades the boundary bucket).
_FUTURES_AGGREGATED_TIMEFRAMES = {"1Hour", "4Hour", "1Week", "1Month"}

# Timeframes with no intraday session (daily/weekly/monthly) — pre/post-market data
# is meaningless for them, so extended-hours defaults OFF. Everything else is intraday
# and defaults ON so the chart shows pre/post candles (the founder's headline ask).
_NON_INTRADAY_TIMEFRAMES = frozenset({"1Day", "1Week", "1Month"})

_TIMEFRAME_MS = {
    "1Min": 60_000,
    "5Min": 300_000,
    "15Min": 900_000,
    "1Hour": 3_600_000,
    "4Hour": 14_400_000,
}


def _bars_extended_default(timeframe: str) -> bool:
    """Extended-hours default for a timeframe: ON for intraday, OFF for daily+."""
    return timeframe not in _NON_INTRADAY_TIMEFRAMES


def _overnight_delayed_flag() -> bool:
    """Free-tier overnight data is ~15m delayed / indicative — surface for UI badge."""
    if _market_session() != "overnight":
        return False
    try:
        broker = get_broker("alpaca")
    except BrokerError:
        return False
    return data_tier(broker) == "free"


def _is_future(sym: str) -> bool:
    from brokers.symbol_class import classify_symbol

    return classify_symbol(sym) == "future"


def _is_crypto(sym: str) -> bool:
    from brokers.symbol_class import classify_symbol

    return classify_symbol(sym) == "crypto"


@router.get("/api/bars")
async def api_bars(
    symbol: str,
    timeframe: str = "1Day",
    limit: int = DEFAULT_LIMIT,
    before: str | None = None,
    extended: bool | None = None,
):
    if limit < 1 or limit > MAX_LIMIT:
        return JSONResponse({"error": f"limit must be 1–{MAX_LIMIT}"}, status_code=400)
    sym = symbol.strip().upper()
    # Explicit override wins; otherwise intraday → ext ON, daily+ → ext OFF. The chart
    # sends the flag per its ETH toggle so the pre/post candle set is unambiguous.
    ext = extended if extended is not None else _bars_extended_default(timeframe)

    if before is not None:
        before_ms = parse_ts_ms(before)
        if before_ms is None:
            return JSONResponse({"error": "before must be RFC3339"}, status_code=400)
        page = await _get_older_bars(sym, timeframe, ext, before_ms, limit)
        if page is None:
            return JSONResponse({"error": "failed to load bars"}, status_code=502)
        return page

    response = await _get_bars_cached(sym, timeframe, ext, limit)
    if response is None:
        return JSONResponse({"error": "failed to load bars"}, status_code=502)
    return _splice_live_tail(sym, timeframe, ext, _window(response, limit))


def _window(response: dict, limit: int) -> dict:
    """The newest ``limit`` bars of a stored window (a new dict; the store is
    never mutated). ``has_more`` is true when older bars exist here or upstream."""
    bars = response.get("bars") or []
    if len(bars) <= limit:
        return response
    return {**response, "bars": bars[-limit:], "has_more": True}


def _splice_live_tail(sym: str, timeframe: str, ext: bool, response: dict) -> dict:
    """Extend the (cached, possibly minutes-stale) vendor series with the live
    hub's minute-built buckets past its tail (card #325). The vendor publishes
    a minute bar 60-80s after the minute closes and the cache adds up to a TTL,
    so without this a fresh chart load is 2-3 candles behind and the browser
    used to invent them. The store entry is never mutated (a new dict/list is
    returned), so the tail refresh keeps seeing pure vendor bars."""
    interval_ms = _TIMEFRAME_MS.get(timeframe)
    if interval_ms is None:
        return response
    from api.hub import market_hub

    ours = market_hub.bars.recent(sym)
    if not ours:
        return response
    if not ext and not _is_future(sym):
        # RTH-only view: hide pre/post/overnight minutes, as the vendor does
        # for extended_hours=False (equities; futures carry no session).
        ours = [b for b in ours if bar_session(b.time) == "regular"]
        if not ours:
            return response
    bars = splice_vendor_tail(response.get("bars") or [], ours, interval_ms)
    if bars is response.get("bars"):
        return response
    return {**response, "bars": bars}


async def _get_bars_cached(
    sym: str, timeframe: str, extended: bool | None = None, limit: int = DEFAULT_LIMIT
) -> dict | None:
    """The stored newest window for (sym, timeframe, ext), tail-fresh. Returns
    the response dict (``bars`` may hold more OR fewer than ``limit`` — callers
    window it; a short window means the vendor's page walk stopped early and
    ``has_more`` says so), or None on total failure. Concurrent identical requests share one
    refresh task, deliberately decoupled from the caller so a frontend
    timeout/disconnect never abandons a fetch — the result lands in the store
    for the retry."""
    ext = extended if extended is not None else _bars_extended_default(timeframe)
    key = (sym, timeframe, ext)
    cached = _BARS_CACHE.get(key)
    if cached is not None and cached[0] > time.monotonic():
        return cached[1]
    task = _BARS_INFLIGHT.get(key)
    if task is None:
        task = asyncio.create_task(_refresh_store(sym, timeframe, ext, limit, key))
        _BARS_INFLIGHT[key] = task
        task.add_done_callback(lambda _t, k=key: _BARS_INFLIGHT.pop(k, None))
    return await task


async def _get_older_bars(
    sym: str, timeframe: str, ext: bool, before_ms: int, limit: int
) -> dict | None:
    """One page of history strictly before ``before_ms``: fetched on demand,
    returned, not stored (immutable history the browser keeps)."""
    key = (sym, timeframe, ext, before_ms, limit)
    task = _BARS_INFLIGHT.get(key)
    if task is None:
        task = asyncio.create_task(_fetch_older(sym, timeframe, ext, before_ms, limit))
        _BARS_INFLIGHT[key] = task
        task.add_done_callback(lambda _t, k=key: _BARS_INFLIGHT.pop(k, None))
    return await task


async def _fetch_older(
    sym: str, timeframe: str, ext: bool, before_ms: int, limit: int
) -> dict | None:
    before = datetime.fromtimestamp(before_ms / 1000, tz=UTC)
    try:
        provider = get_market_data_provider()
        bars, has_more = await provider.aget_stock_bars_latest(
            sym, timeframe=timeframe, limit=limit, before=before, extended_hours=ext
        )
    except Exception:
        logger.warning("/api/bars older page failed for %s %s", sym, timeframe, exc_info=True)
        return None
    return _build_bars_response(sym, timeframe, _stockbars_to_dicts(bars), ext, has_more)


def _stockbars_to_dicts(bars: list) -> list[dict]:
    """Chart-response bar dicts from provider StockBar objects (pre-sanitize)."""
    return [
        {
            "time": b.time,
            "open": b.open,
            "high": b.high,
            "low": b.low,
            "close": b.close,
            "volume": b.volume,
        }
        for b in bars
    ]


def _build_bars_response(
    sym: str, timeframe: str, bar_dicts: list[dict], ext: bool, has_more: bool
) -> dict:
    """Sanitize + wrap bar dicts into the /api/bars response shape."""
    from brokers.bars_hygiene import sanitize_bar_dicts

    response = {
        "symbol": sym,
        "timeframe": timeframe,
        "bars": sanitize_bar_dicts(sym, bar_dicts),
        "has_more": has_more,
    }
    # Crypto trades 24/7 with no session tiers — the overnight-delay badge is an
    # equity/Blue-Ocean concept and would be a lie on a BTC/USD chart.
    if (
        ext
        and timeframe not in _NON_INTRADAY_TIMEFRAMES
        and not _is_crypto(sym)
        and _overnight_delayed_flag()
    ):
        response["overnight_delayed"] = True
    return response


def _store_bars(key: tuple, timeframe: str, response: dict, *, full: bool) -> None:
    now = time.monotonic()
    _BARS_CACHE[key] = (now + _BARS_CACHE_TTL.get(timeframe, 120.0), response)
    if full:
        _BARS_FULL_AT[key] = now
    if len(_BARS_CACHE) > _BARS_MAX_ENTRIES:
        for k in [k for k, (exp, _) in list(_BARS_CACHE.items()) if exp <= now]:
            _BARS_CACHE.pop(k, None)
            _BARS_FULL_AT.pop(k, None)


def _stale_bars_or_none(key: tuple) -> dict | None:
    """Stale-if-error: an expired stored window beats an empty chart (expired
    entries stay in _BARS_CACHE until overwritten or evicted)."""
    entry = _BARS_CACHE.get(key)
    return entry[1] if entry is not None else None


async def _refresh_store(
    sym: str, timeframe: str, ext: bool, limit: int, key: tuple
) -> dict | None:
    """Bring the store's newest window up to date: refetch the tail since its
    last bar over a fresh-enough baseline, else fetch the newest ``limit``."""
    existing = _BARS_CACHE.get(key)
    full_at = _BARS_FULL_AT.get(key)
    if (
        existing is not None
        and existing[1].get("bars")
        and full_at is not None
        and (time.monotonic() - full_at) < _BARS_FULL_REBASELINE
        and not (timeframe in _FUTURES_AGGREGATED_TIMEFRAMES and _is_future(sym))
    ):
        merged = await _refresh_tail(sym, timeframe, ext, key, existing[1])
        if merged is not None:
            return merged
        # tail refresh unavailable/failed — fall through to a full newest-N fetch
    return await _fetch_newest(sym, timeframe, ext, limit, key)


async def _refresh_tail(
    sym: str, timeframe: str, ext: bool, key: tuple, cached: dict
) -> dict | None:
    """Refetch from the last stored bar (inclusive — it may have been forming)
    to now and merge over the store. None → caller falls back to a full fetch."""
    last_ms = parse_ts_ms(cached["bars"][-1].get("time"))
    if last_ms is None:
        return None
    start = datetime.fromtimestamp(last_ms / 1000, tz=UTC)
    try:
        provider = get_market_data_provider()
        recent = await provider.aget_stock_bars_range(
            sym,
            timeframe=timeframe,
            start=start,
            end=datetime.now(UTC) + timedelta(minutes=1),
            extended_hours=ext,
        )
    except Exception:
        return None  # the full fetch owns the resilience fallbacks
    if not recent:
        return None
    recent_dicts = _stockbars_to_dicts(recent)
    cutoff = recent_dicts[0]["time"]
    # Both ascending same-provider ISO times, so the concat stays sorted and the
    # boundary bar is replaced (not duplicated) by its fresh copy.
    kept = [b for b in cached["bars"] if b["time"] < cutoff]
    response = _build_bars_response(
        sym, timeframe, kept + recent_dicts, ext, bool(cached.get("has_more", True))
    )
    _store_bars(key, timeframe, response, full=False)
    return response


async def _fetch_newest(
    sym: str, timeframe: str, ext: bool, limit: int, key: tuple
) -> dict | None:
    try:
        provider = get_market_data_provider()
    except BrokerError:
        return _stale_bars_or_none(key)
    try:
        bars, has_more = await provider.aget_stock_bars_latest(
            sym, timeframe=timeframe, limit=limit, extended_hours=ext
        )
    except Exception as exc:
        # Any provider failure (BrokerError, Schwab outage, httpx error) falls back
        # to Alpaca so charts never hard-break — resilience is the point of the path.
        if _is_future(sym):
            # Alpaca has no futures data — the fallback hop can only fail. Serve the
            # last-good series through a Schwab WAF cooldown instead of an empty chart.
            logger.warning("/api/bars futures provider failed (%s), serving stale", exc)
            return _stale_bars_or_none(key)
        logger.warning("/api/bars provider failed (%s), falling back to Alpaca", exc)
        broker = _require_alpaca()
        if isinstance(broker, JSONResponse):
            return _stale_bars_or_none(key)
        try:
            bars, has_more = await broker.aget_stock_bars_latest(
                sym, timeframe=timeframe, limit=limit, extended_hours=ext
            )
        except Exception:
            return _stale_bars_or_none(key)
    response = _build_bars_response(sym, timeframe, _stockbars_to_dicts(bars), ext, has_more)
    _store_bars(key, timeframe, response, full=True)
    return response
