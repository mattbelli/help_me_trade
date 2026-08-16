"""Chart bars: ``/api/bars`` + its short-TTL cache + the prewarm loop.

Extracted from ``api/main`` (strangler round 2). ``_BARS_CACHE`` /
``_get_bars_cached`` are shared read-side with ``api/scan_routes`` and
``api/fusion_jobs``; ``_bars_prewarm_loop`` is started by ``api/main``'s lifespan.
Registered by ``api/main`` via ``app.include_router``.
"""

from __future__ import annotations

import asyncio
import logging
import time

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from api.alpaca_guard import _require_alpaca
from api.jobs import mark_alive
from brokers.alpaca_tier import data_tier
from brokers.errors import BrokerError
from brokers.factory import get_broker, get_market_data_provider
from brokers.market_clock import market_session as _market_session
from signals.universe import all_watchlist_symbols as _all_watchlist_symbols

logger = logging.getLogger(__name__)
router = APIRouter()


_BARS_PREWARM_INTERVAL = 15.0


# Short-TTL cache for chart bars so flipping back to a symbol/timeframe is instant
# instead of waiting on a fresh Schwab per-symbol round-trip (the live price rides the
# WS stream on the frontend, so a few-minutes-stale historical window is invisible).
_BARS_CACHE: dict[tuple, tuple[float, dict]] = {}
# In-flight fetch tasks by cache key — concurrent identical requests await the
# same task instead of multiplying paginated upstream walks.
_BARS_INFLIGHT: dict[tuple, "asyncio.Task[dict | None]"] = {}
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
# Serve-stale-while-revalidate window for INTERACTIVE requests: an expired entry no
# older than this past its TTL is returned immediately while the refresh task runs in
# the background and lands in cache for the frontend's next poll (the chart walks
# forward on live WS ticks meanwhile). Bounded so a series untouched for hours still
# blocks for fresh data instead of silently painting a stale session. The prewarm
# loop never serves stale — it must await fetches to pace itself (#308: the 10-40s
# chart loads were interactive requests queued behind a saturated refresh path).
_BARS_SERVE_STALE_MAX = 600.0

# Incremental refresh: when a warm cached series is refreshed (by the prewarm loop
# or an expired interactive miss), re-fetch only this small trailing window and merge
# it over the cached bars instead of re-walking the whole `days` range. The
# still-forming last bar is overwritten and any new bars appended. The window is per
# timeframe, generous enough to bridge a weekend/holiday gap so the overlap always
# re-captures the last cached bar. This is what keeps a full prewarm cycle cheap
# enough to stay well under the TTLs it refreshes (the storm invariant — see the
# chart-bars-prewarm-storms memory).
_BARS_INCREMENTAL_DAYS = {
    "1Min": 2,
    "5Min": 2,
    "15Min": 4,
    "1Hour": 5,
    "4Hour": 7,
    "1Day": 7,
    "1Week": 30,
    "1Month": 90,
}
# Timeframes Schwab builds by aggregating 1Day/30Min in-process — their bucket
# stamps/contents depend on the fetch window, so futures skip incremental for these
# (see the guard in _fetch_bars_incremental).
_FUTURES_AGGREGATED_TIMEFRAMES = {"1Hour", "4Hour", "1Week", "1Month"}
# Incremental merge preserves old history verbatim, so a split adjustment (Alpaca
# fetches with adjustment=split) would leave the pre-split price scale on the
# retained bars forever. Force a full re-fetch (re-baseline) at least this often so
# splits and any upstream history revision propagate. Cheap: a continuously-warm
# series pays one full walk per hour and serves incremental the rest of the time.
_BARS_FULL_REBASELINE = 3600.0
# monotonic timestamp of the last FULL fetch per cache key — gates re-baseline.
_BARS_FULL_AT: dict[tuple, float] = {}

# Timeframes with no intraday session (daily/weekly/monthly) — pre/post-market data
# is meaningless for them, so extended-hours defaults OFF. Everything else is intraday
# and defaults ON so the chart shows pre/post candles (the founder's headline ask).
_NON_INTRADAY_TIMEFRAMES = frozenset({"1Day", "1Week", "1Month"})


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


# Recency of chart-bar requests, used to keep the cache warm (prewarm loop below).
# spec = (timeframe, days) the frontend actually requests — capturing it avoids
# hardcoding the frontend's per-timeframe `days` here (they must match the cache key).
_BARS_SEEN_SYMBOLS: dict[str, float] = {}  # symbol -> last-requested monotonic
# (symbol, timeframe, days) -> last-requested monotonic. Keyed by the full
# COMBO, not (timeframe, days) alone: a global spec key made the prewarm warm
# every seen timeframe for EVERY watchlist symbol — 40 symbols × one long
# intraday spec = a 10+-minute cycle, longer than the TTLs it refreshes, i.e.
# a permanent slow-motion refetch storm (07-22 round 2, watchdog-caught).
_BARS_SEEN_SPECS: dict[tuple[str, str, int], float] = {}
_BARS_SEEN_TTL = 30 * 60.0  # forget a symbol/spec 30 min after last request
_BARS_PREWARM_REFRESH_LEAD = 25.0  # refetch when <25s of cache TTL remains (never go cold mid-use)
_BARS_PREWARM_MAX_SYMBOLS = 40  # cap Schwab load for big watchlists
_BARS_PREWARM_PACE = 0.25  # sleep between actual fetches (s)
_BARS_PREWARM_DEFAULT_SPECS: set[tuple[str, int]] = {
    ("1Day", 1825)
}  # the default chart view, warm from boot
# Broadcast the K most-recently-used distinct specs (the timeframes the user is
# actively working in) so switching symbols — or switching to a timeframe on a symbol
# not yet viewed there — is served warm, not cold.
_BARS_HOT_SPECS_MAX = 2
# ...but only to the N most-recently-viewed symbols, NOT the whole watchlist. The full
# cross-product (40 symbols × K specs + defaults) exceeded the 160-entry cap on EVERY
# cycle from PR #102 (07-22) onward — a permanently saturated queue hammering 180-260
# upstream calls/min all day, behind which interactive chart loads queued for 10-40s
# (#308, the real "chart won't load" mechanism; measured 270% backend CPU). A symbol
# outside the top-N cold-loads its first view of a hot timeframe (sub-second to a few
# seconds) and is warm from then on; serve-stale covers every repeat view.
_BARS_HOT_SPECS_SYMBOLS = 12
# Hard backstop on (symbol, spec) entries warmed per cycle — bounds the cross-product
# for large/dynamic watchlists (future users: 200-300 names). Trimmed from the tail with
# default-1Day pushed first, so the baseline view is never sacrificed to a hot-spec
# broadcast. Judge a full cold pass stays < the shortest TTL it maintains via /health
# bars_prewarm lag_s (chart-bars-prewarm-storms).
_BARS_PREWARM_MAX_ENTRIES = 160


@router.get("/api/bars")
async def api_bars(
    symbol: str,
    timeframe: str = "1Day",
    days: int = 180,
    extended: bool | None = None,
):
    if days < 1 or days > 3650:
        return JSONResponse({"error": "days must be 1–3650"}, status_code=400)

    sym = symbol.strip().upper()
    # Explicit override wins; otherwise intraday → ext ON, daily+ → ext OFF. The chart
    # sends the flag per its ETH toggle so the pre/post candle set is unambiguous.
    ext = extended if extended is not None else _bars_extended_default(timeframe)
    now = time.monotonic()
    # Record what's actually viewed so the prewarm loop keeps it warm. Real requests
    # only — the loop calls _get_bars_cached directly so it can't self-refresh recency.
    _BARS_SEEN_SYMBOLS[sym] = now
    _BARS_SEEN_SPECS[(sym, timeframe, int(days))] = now

    response = await _get_bars_cached(sym, timeframe, int(days), ext, stale_ok=True)
    if response is None:
        return JSONResponse({"error": "failed to load bars"}, status_code=502)
    return response


async def _get_bars_cached(
    sym: str,
    timeframe: str,
    days: int,
    extended: bool | None = None,
    *,
    stale_ok: bool = False,
) -> dict | None:
    """Cache-read → coalesced fetch → cache-write for chart bars.

    Returns the response dict, or None on total failure. Shared by the /api/bars
    endpoint and the prewarm loop — the loop must NOT record request-recency (else a
    prewarmed symbol would keep refreshing its own recency and never age out), so the
    fetch core lives here, separate from the endpoint's recency bookkeeping.

    Concurrent identical requests (double-mounted widget, prewarm racing an
    interactive load) share ONE upstream fetch: a cold long-range intraday series
    is a 10–30-page sequential pagination walk at Alpaca, so N callers must never
    multiply it. The fetch runs as its own task, deliberately decoupled from the
    caller — a frontend timeout/disconnect no longer abandons a half-done walk;
    the result lands in cache for the retry.
    """
    ext = extended if extended is not None else _bars_extended_default(timeframe)
    # ext is part of the cache key: an ext-inclusive and an RTH-only series for the
    # same (symbol, timeframe, days) are different payloads and must not alias.
    cache_key = (sym, timeframe, int(days), ext)
    cached = _BARS_CACHE.get(cache_key)
    if cached is not None and cached[0] > time.monotonic():
        return cached[1]
    task = _BARS_INFLIGHT.get(cache_key)
    if task is None:
        task = asyncio.create_task(_fetch_bars(sym, timeframe, int(days), ext, cache_key))
        _BARS_INFLIGHT[cache_key] = task
        task.add_done_callback(lambda _t, k=cache_key: _BARS_INFLIGHT.pop(k, None))
    # Serve-stale-while-revalidate: a recently-expired payload paints the chart NOW;
    # the fetch task just created (or already running) lands fresh data for the next
    # poll. Only interactive callers opt in — the prewarm loop awaits so its pacing
    # holds and fetch concurrency stays bounded.
    if (
        stale_ok
        and cached is not None
        and cached[0] > time.monotonic() - _BARS_SERVE_STALE_MAX
    ):
        return cached[1]
    return await task


def _stale_bars_or_none(cache_key: tuple) -> dict | None:
    """Stale-if-error: an expired cached series beats an empty chart (expired
    entries stay in _BARS_CACHE until overwritten or evicted)."""
    entry = _BARS_CACHE.get(cache_key)
    return entry[1] if entry is not None else None


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
    sym: str, timeframe: str, bar_dicts: list[dict], ext: bool
) -> dict:
    """Sanitize + wrap bar dicts into the /api/bars response shape."""
    from brokers.bars_hygiene import sanitize_bar_dicts

    response = {
        "symbol": sym,
        "timeframe": timeframe,
        "bars": sanitize_bar_dicts(sym, bar_dicts),
    }
    if ext and timeframe not in _NON_INTRADAY_TIMEFRAMES and _overnight_delayed_flag():
        response["overnight_delayed"] = True
    return response


def _store_bars(cache_key: tuple, timeframe: str, response: dict, *, full: bool) -> None:
    """Write a bars response into the cache with its TTL. ``full`` records a fresh
    re-baseline timestamp (gates incremental vs full on the next refresh)."""
    now = time.monotonic()
    _BARS_CACHE[cache_key] = (now + _BARS_CACHE_TTL.get(timeframe, 120.0), response)
    if full:
        _BARS_FULL_AT[cache_key] = now
    if len(_BARS_CACHE) > 256:
        for k in [k for k, (exp, _) in list(_BARS_CACHE.items()) if exp <= now]:
            _BARS_CACHE.pop(k, None)
            _BARS_FULL_AT.pop(k, None)


async def _fetch_bars(
    sym: str, timeframe: str, days: int, ext: bool, cache_key: tuple
) -> dict | None:
    """Refresh a cached series: incremental (a small trailing window merged over the
    cached bars) when a fresh-enough full baseline exists, else a full range walk.
    Incremental is what makes the prewarm loop cheap enough to warm more than the
    default view without outrunning the TTLs it maintains (chart-bars-prewarm-storms)."""
    existing = _BARS_CACHE.get(cache_key)
    full_at = _BARS_FULL_AT.get(cache_key)
    if (
        existing is not None
        and existing[1].get("bars")
        and full_at is not None
        and (time.monotonic() - full_at) < _BARS_FULL_REBASELINE
    ):
        merged = await _fetch_bars_incremental(
            sym, timeframe, days, ext, cache_key, existing[1]
        )
        if merged is not None:
            return merged
        # incremental unavailable/failed — fall through to a full re-fetch
    return await _fetch_bars_full(sym, timeframe, days, ext, cache_key)


async def _fetch_bars_incremental(
    sym: str, timeframe: str, days: int, ext: bool, cache_key: tuple, cached: dict
) -> dict | None:
    """Merge a small trailing re-fetch over the cached series. Returns the merged
    response, or None to signal the caller to fall back to a full walk (no saving
    possible, a provider error, or an empty window)."""
    inc_days = _BARS_INCREMENTAL_DAYS.get(timeframe, 7)
    if inc_days >= days:
        return None  # window already covers the whole range — no saving, do full
    # Futures aggregated timeframes are built in-process from 1Day/30Min groups whose
    # bucket stamps depend on the fetch window (weekly/monthly buckets stamp
    # group[0].time = first day IN the window; hourly buckets aggregate only in-window
    # 30Min bars) — a truncated window duplicates or downgrades the boundary bucket
    # when merged over a full-walk baseline. Raw timeframes merge cleanly.
    if timeframe in _FUTURES_AGGREGATED_TIMEFRAMES:
        from brokers.symbol_class import classify_symbol

        if classify_symbol(sym) == "future":
            return None
    try:
        provider = get_market_data_provider()
        recent = await provider.aget_stock_bars(
            sym, timeframe=timeframe, days=inc_days, extended_hours=ext
        )
    except Exception:
        return None  # any hiccup → full re-fetch (which owns the resilience fallbacks)
    if not recent:
        return None
    recent_dicts = _stockbars_to_dicts(recent)
    cutoff = recent_dicts[0]["time"]
    # Cached bars strictly before the window + the freshly fetched window. Both are
    # ascending same-provider ISO times, so the concat stays sorted and the still-
    # forming boundary bar is replaced (not duplicated) by its fresh copy.
    kept = [b for b in cached.get("bars", []) if b["time"] < cutoff]
    response = _build_bars_response(sym, timeframe, kept + recent_dicts, ext)
    _store_bars(cache_key, timeframe, response, full=False)
    return response


async def _fetch_bars_full(
    sym: str, timeframe: str, days: int, ext: bool, cache_key: tuple
) -> dict | None:
    try:
        provider = get_market_data_provider()
    except BrokerError:
        return _stale_bars_or_none(cache_key)

    try:
        bars = await provider.aget_stock_bars(
            sym, timeframe=timeframe, days=days, extended_hours=ext
        )
    except Exception as exc:
        # Any provider failure (BrokerError, Schwab outage, httpx error) falls back
        # to Alpaca so charts never hard-break — resilience is the point of the path.
        from brokers.symbol_class import classify_symbol

        if classify_symbol(sym) == "future":
            # Alpaca has no futures data — the fallback hop can only fail. Serve the
            # last-good series through a Schwab WAF cooldown instead of an empty chart.
            logger.warning("/api/bars futures provider failed (%s), serving stale", exc)
            return _stale_bars_or_none(cache_key)
        logger.warning("/api/bars provider failed (%s), falling back to Alpaca", exc)
        broker = _require_alpaca()
        if isinstance(broker, JSONResponse):
            return _stale_bars_or_none(cache_key)
        try:
            bars = await broker.aget_stock_bars(sym, timeframe=timeframe, days=days)
        except BrokerError:
            return _stale_bars_or_none(cache_key)

    response = _build_bars_response(sym, timeframe, _stockbars_to_dicts(bars), ext)
    _store_bars(cache_key, timeframe, response, full=True)
    return response


async def _bars_prewarm_once() -> None:
    now = time.monotonic()
    # Symbols likely to be switched to: the whole watchlist (always) + the most-recently
    # viewed non-watchlist symbols, up to the cap (watchlist is never evicted).
    watch = list(_all_watchlist_symbols())
    recent = sorted(
        (
            s
            for s, ts in _BARS_SEEN_SYMBOLS.items()
            if now - ts < _BARS_SEEN_TTL and s not in set(watch)
        ),
        key=lambda s: _BARS_SEEN_SYMBOLS[s],
        reverse=True,
    )
    extra = max(0, _BARS_PREWARM_MAX_SYMBOLS - len(watch))
    symbols = watch + recent[:extra]
    allowed = set(symbols)

    # The timeframes the user is ACTUALLY working in: the most-recently-viewed distinct
    # (timeframe, days) specs across any symbol, capped to a small K (default specs
    # excluded — they're always broadcast). Collapsed to the latest view per spec.
    spec_last_seen: dict[tuple[str, int], float] = {}
    for (_sym, timeframe, days), ts in _BARS_SEEN_SPECS.items():
        spec = (timeframe, int(days))
        if now - ts >= _BARS_SEEN_TTL or spec in _BARS_PREWARM_DEFAULT_SPECS:
            continue
        if ts > spec_last_seen.get(spec, 0.0):
            spec_last_seen[spec] = ts
    hot_specs = sorted(spec_last_seen, key=lambda s: spec_last_seen[s], reverse=True)[
        :_BARS_HOT_SPECS_MAX
    ]
    # Broadcast order for hot specs = symbols by view-recency (NOT alphabetical) so that
    # under the entry cap the symbols the user is actively switching between keep their
    # hot-spec warmth — never a blind SPY/TSLA clip (flow-sub-cap-alphabetical-clip).
    symbols_by_recency = sorted(
        symbols, key=lambda s: _BARS_SEEN_SYMBOLS.get(s, 0.0), reverse=True
    )

    # Warm queue, priority order: (1) the default 1Day view for every symbol — the
    # baseline everyone needs and the cheapest; (2) the hot specs broadcast across the
    # top-N most-recently-viewed symbols (the full-watchlist cross-product saturated
    # the queue permanently — see _BARS_HOT_SPECS_SYMBOLS); (3) any other exact combo
    # actually viewed on an allowed symbol (one-off long-range views). The entry cap
    # trims from the tail, so 1Day coverage is never dropped.
    queue: list[tuple[str, str, int]] = []
    seen_q: set[tuple[str, str, int]] = set()

    def _push(sym: str, timeframe: str, days: int) -> None:
        item = (sym, timeframe, int(days))
        if item not in seen_q:
            seen_q.add(item)
            queue.append(item)

    for timeframe, days in _BARS_PREWARM_DEFAULT_SPECS:
        for sym in symbols:
            _push(sym, timeframe, days)
    for timeframe, days in hot_specs:
        for sym in symbols_by_recency[:_BARS_HOT_SPECS_SYMBOLS]:
            _push(sym, timeframe, days)
    for (sym, timeframe, days), ts in _BARS_SEEN_SPECS.items():
        if now - ts < _BARS_SEEN_TTL and sym in allowed:
            _push(sym, timeframe, days)

    if len(queue) > _BARS_PREWARM_MAX_ENTRIES:
        cut = queue[_BARS_PREWARM_MAX_ENTRIES:]
        queue = queue[:_BARS_PREWARM_MAX_ENTRIES]
        # 1Day baseline entries sit first in the queue, so they're only cut once
        # the watchlist itself exceeds the cap (200+ names) — say so honestly
        # instead of claiming coverage that a big list wouldn't have.
        dropped_default = sum(
            1 for _s, tf, d in cut if (tf, d) in _BARS_PREWARM_DEFAULT_SPECS
        )
        logger.info(
            "bars prewarm: queue capped at %d entries (dropped %d this cycle, "
            "of which %d are 1Day-baseline symbols left cold)",
            _BARS_PREWARM_MAX_ENTRIES,
            len(cut),
            dropped_default,
        )

    for sym, timeframe, days in queue:
        # Heartbeat per queue item, not per cycle: a cold full warm (post-restart)
        # legitimately runs minutes, and an end-of-cycle-only mark would
        # false-stale the watchdog every restart. Progress = alive; a genuinely
        # wedged fetch still stops the marks.
        mark_alive("bars_prewarm")
        # MUST mirror _get_bars_cached's 4-tuple cache key (incl. the ext
        # default). A 3-tuple here never matched, so this skip-check was
        # dead and the loop refetched EVERY symbol × spec EVERY 15s — a
        # standing request storm that starved interactive chart loads and
        # fed the Schwab WAF 429 cycle (found 07-22, founder-reported).
        key = (sym, timeframe, int(days), _bars_extended_default(timeframe))
        entry = _BARS_CACHE.get(key)
        if entry is not None and entry[0] > time.monotonic() + _BARS_PREWARM_REFRESH_LEAD:
            continue  # still warm enough — skip
        try:
            # Shared fetch core — refetches (cache miss/expiring) + writes _BARS_CACHE
            # with the exact same key/shape, WITHOUT recording request-recency.
            await _get_bars_cached(sym, timeframe, int(days))
        except Exception:
            logger.debug("bars prewarm failed for %s %s", sym, timeframe, exc_info=True)
        await asyncio.sleep(_BARS_PREWARM_PACE)


async def _bars_prewarm_loop() -> None:
    while True:
        try:
            await _bars_prewarm_once()
        except Exception:
            logger.exception("Bars prewarm loop iteration failed")
        else:
            mark_alive("bars_prewarm")
        await asyncio.sleep(_BARS_PREWARM_INTERVAL)
