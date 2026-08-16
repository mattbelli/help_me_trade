"""Watchlist routes: list CRUD, the 20-s ``/api/watchlist/market`` poll (quotes +
RVOL + baselines + abnormality per row), and ``/api/watchlist/aggregate``.

Extracted from ``api/main`` (strangler round 2). Uses the live ``market_hub``
(``api/hub``) for extended-hours quote enrichment and
``api/earnings_jobs._kick_earnings_alert_catchup`` so a symbol added mid-day gets
its earnings alert now, not at the next tick. Registered by ``api/main`` via
``app.include_router``.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import date, datetime, timezone

from fastapi import APIRouter, Body
from fastapi.responses import JSONResponse

from api import watchlist_bars_cache
from api.earnings_jobs import _kick_earnings_alert_catchup
from api.hub import market_hub
from api.scanner_routes import _abnorm_field
from brokers.alpaca import StockBar
from brokers.errors import BrokerError
from brokers.factory import get_market_data_provider
from brokers.market_clock import market_session as _market_session
from config.settings import (
    add_watchlist_symbol,
    create_watchlist,
    delete_watchlist,
    get_watchlist,
    get_watchlists_state,
    remove_watchlist_symbol,
    save_watchlist,
    set_active_watchlist,
    update_watchlist,
)
from db.earnings_calendar import last_earnings_date, next_earnings_date
from signals import indicators
from signals.volume_profile import build_profile, load_profile, rvol_now
from signals.watchlist_index import equal_weight_index
from trading.market_hours import _ET, _SESSION_CLOSE, _SESSION_OPEN

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/api/watchlist")
def api_get_watchlist():
    return {"symbols": get_watchlist()}


@router.get("/api/watchlists")
def api_get_watchlists():
    return get_watchlists_state()


@router.put("/api/watchlists/active")
def api_set_active_watchlist(body: dict = Body(...)):
    list_id = str(body.get("id", "")).strip()
    if not list_id:
        return JSONResponse({"error": "id is required"}, status_code=400)
    try:
        state = set_active_watchlist(list_id)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    return state


@router.post("/api/watchlists")
def api_create_watchlist(body: dict = Body(...)):
    name = str(body.get("name", "")).strip()
    if not name:
        return JSONResponse({"error": "name is required"}, status_code=400)
    symbols = body.get("symbols")
    if symbols is not None and not isinstance(symbols, list):
        return JSONResponse({"error": "symbols must be a list"}, status_code=400)
    settings = body.get("settings")
    if settings is not None and not isinstance(settings, dict):
        return JSONResponse({"error": "settings must be an object"}, status_code=400)
    try:
        state = create_watchlist(
            name,
            symbols if isinstance(symbols, list) else None,
            settings if isinstance(settings, dict) else None,
        )
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    return state


@router.put("/api/watchlists/{list_id}")
def api_update_watchlist(list_id: str, body: dict = Body(...)):
    name = body.get("name")
    symbols = body.get("symbols")
    settings = body.get("settings")
    max_slots = body.get("max_slots")
    pinned = body.get("pinned")
    if name is not None and not isinstance(name, str):
        return JSONResponse({"error": "name must be a string"}, status_code=400)
    if symbols is not None and not isinstance(symbols, list):
        return JSONResponse({"error": "symbols must be a list"}, status_code=400)
    if settings is not None and not isinstance(settings, dict):
        return JSONResponse({"error": "settings must be an object"}, status_code=400)
    if max_slots is not None and not isinstance(max_slots, int):
        return JSONResponse({"error": "max_slots must be an integer"}, status_code=400)
    if pinned is not None and not isinstance(pinned, list):
        return JSONResponse({"error": "pinned must be a list"}, status_code=400)
    try:
        state = update_watchlist(
            list_id,
            name=name if isinstance(name, str) else None,
            symbols=symbols if isinstance(symbols, list) else None,
            settings=settings if isinstance(settings, dict) else None,
            max_slots=max_slots if isinstance(max_slots, int) else None,
            pinned=pinned if isinstance(pinned, list) else None,
        )
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    if symbols is not None:
        _kick_earnings_alert_catchup()
    return state


@router.delete("/api/watchlists/{list_id}")
def api_delete_watchlist(list_id: str):
    try:
        state = delete_watchlist(list_id)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    return state


# The watchlist poll's bars cache lives in api/watchlist_bars_cache.py. Aliased
# to the historic private names so the read-only cache peeks below (and the
# tests that seed it) keep working against the same dict object.
_wl_bars_cache = watchlist_bars_cache.bars_cache
_WL_BARS_TTL_S = watchlist_bars_cache.BARS_TTL_S
_wl_cached_bars_batch = watchlist_bars_cache.cached_bars_batch


# 52-week / ATR / SMA baselines move once a day, so they're computed from a
# 300-day daily-bar fetch on a LONG TTL (~4 fetches/day/symbol) — the 20-s poll
# itself never adds broker requests for them. Kept separate from
# _wl_bars_cache because that cache is keyed (symbol, timeframe) and a 300-day
# entry would collide with the 12-day one.
_wl_baseline_cache: dict[str, tuple[float, dict | None]] = {}
_WL_BASELINE_TTL_S = 6 * 3600.0
_WL_BASELINE_MISS_TTL_S = 600.0  # retry sooner when the fetch failed/was empty


def _compute_wl_baseline(bars: list) -> dict | None:
    if len(bars) < 20:
        return None
    closes = [float(b.close) for b in bars]
    # Vendor glitch guard — a bad print can ship an absurd bar low/high (seen
    # live: SPY daily low=69.005 on a 695-close day, a decimal-shift bad tick,
    # which poisoned the 52w low and range bar). A valid bar has
    # low <= close <= high, so a low/high wildly inconsistent with its own
    # close is bogus — substitute the close so 52w and ATR stay sane.
    highs = [
        h if (c and h <= c * 2) else c for h, c in ((float(b.high), float(b.close)) for b in bars)
    ]
    lows = [
        lo if (c and lo >= c * 0.5) else c
        for lo, c in ((float(b.low), float(b.close)) for b in bars)
    ]

    def _r(v: float | None) -> float | None:
        return round(v, 4) if v is not None else None

    return {
        "w52_high": _r(max(highs[-252:])),
        "w52_low": _r(min(lows[-252:])),
        "atr14": _r(indicators.atr(highs, lows, closes, 14)),
        "sma50": _r(indicators.sma(closes, 50)),
        "sma200": _r(indicators.sma(closes, 200)),
    }


async def _wl_baselines_batch(provider, symbols: list[str]) -> dict[str, dict | None]:
    now = time.time()
    out: dict[str, dict | None] = {}
    missing: list[str] = []
    for sym in symbols:
        hit = _wl_baseline_cache.get(sym)
        if hit is not None:
            ttl = _WL_BASELINE_TTL_S if hit[1] is not None else _WL_BASELINE_MISS_TTL_S
            if now - hit[0] < ttl:
                out[sym] = hit[1]
                continue
        missing.append(sym)
    if missing:
        try:
            fetched = await provider.aget_stock_bars_batch(missing, timeframe="1Day", days=300)
        except BrokerError:
            logger.warning("Watchlist baseline bars fetch failed", exc_info=True)
            fetched = {}
        for sym in missing:
            baseline = _compute_wl_baseline(fetched.get(sym) or [])
            _wl_baseline_cache[sym] = (now, baseline)
            out[sym] = baseline
    return out


# ---- Watchlist aggregate (equal-weight return index) ----

_WL_AGG_BARS_TTL = 300.0  # matches the 1Day chart-bars TTL
_wl_agg_closes_cache: dict[str, tuple[float, list[tuple[str, float]]]] = {}


async def _wl_agg_closes(symbols: list[str]) -> dict[str, list[tuple[str, float]]]:
    """Daily (date, close) series per symbol — ONE batched provider call for the
    cache misses (never a per-symbol fan-out; the provider may be Schwab and its
    WAF treats bursts as abuse). A year of dailies serves every panel range, so
    range switches are pure cache hits."""
    now = time.monotonic()
    out: dict[str, list[tuple[str, float]]] = {}
    missing: list[str] = []
    for sym in symbols:
        entry = _wl_agg_closes_cache.get(sym)
        if entry is not None and now - entry[0] < _WL_AGG_BARS_TTL:
            out[sym] = entry[1]
        else:
            missing.append(sym)
    if missing:
        try:
            provider = get_market_data_provider()
            fetched = await provider.aget_stock_bars_batch(
                missing, timeframe="1Day", days=365
            )
        except BrokerError:
            logger.warning("Watchlist aggregate bars fetch failed", exc_info=True)
            fetched = {}
        for sym in missing:
            closes = [
                (str(b.time)[:10], float(b.close)) for b in (fetched.get(sym) or [])
            ]
            _wl_agg_closes_cache[sym] = (now, closes)
            out[sym] = closes
    if len(_wl_agg_closes_cache) > 512:
        for k in [
            k
            for k, (ts, _) in list(_wl_agg_closes_cache.items())
            if now - ts >= _WL_AGG_BARS_TTL
        ]:
            _wl_agg_closes_cache.pop(k, None)
    return out


@router.get("/api/watchlist/aggregate")
async def api_watchlist_aggregate(list_id: str = "", days: int = 90):
    """Equal-weight daily-rebalanced price-return index over a watchlist.

    `list_id` accepts an id or a (case-insensitive) name; empty = active list.
    """
    if days < 2 or days > 365:
        return JSONResponse({"error": "days must be 2–365"}, status_code=400)
    state = get_watchlists_state()
    lists = state.get("lists", [])
    entry = None
    if list_id:
        wanted = list_id.strip()
        entry = next(
            (
                item
                for item in lists
                if item.get("id") == wanted
                or str(item.get("name", "")).casefold() == wanted.casefold()
            ),
            None,
        )
        if entry is None:
            return JSONResponse(
                {"error": f"unknown watchlist {list_id!r}"}, status_code=404
            )
    else:
        entry = next(
            (item for item in lists if item.get("id") == state.get("active_id")),
            lists[0] if lists else None,
        )
        if entry is None:
            return JSONResponse({"error": "no watchlists"}, status_code=404)

    symbols = [str(s).strip().upper() for s in entry.get("symbols", []) if str(s).strip()]
    closes_by_symbol = await _wl_agg_closes(symbols)
    today = datetime.now(timezone.utc).date()
    cutoff = date.fromordinal(today.toordinal() - int(days)).isoformat()
    windowed = {
        sym: [(d, c) for d, c in closes if d >= cutoff]
        for sym, closes in closes_by_symbol.items()
    }
    # A symbol needs two closes inside the window to ever contribute a return.
    no_data = sorted(sym for sym, closes in windowed.items() if len(closes) < 2)
    points, per_symbol = equal_weight_index(
        {sym: closes for sym, closes in windowed.items() if len(closes) >= 2}
    )
    return {
        "list_id": entry.get("id", ""),
        "name": entry.get("name", ""),
        "days": int(days),
        "points": [{"date": d, "pct": round(p, 4)} for d, p in points],
        "return_pct": round(points[-1][1], 4) if points else None,
        "per_symbol": sorted(
            ({"symbol": s, "return_pct": round(p, 4)} for s, p in per_symbol.items()),
            key=lambda r: r["return_pct"],
            reverse=True,
        ),
        "symbols_missing": no_data,
    }


@router.get("/api/watchlist/market")
async def api_watchlist_market(symbols: str = ""):
    try:
        provider = get_market_data_provider()
    except BrokerError as exc:
        return JSONResponse({"error": str(exc)}, status_code=502)

    symbol_list = [s.strip().upper() for s in symbols.split(",") if s.strip()]
    if not symbol_list:
        symbol_list = get_watchlist()
    if not symbol_list:
        return {"quotes": {}}

    # Any requested symbol with no fresh volume profile gets one built in the
    # background (deduped/cooldowned) so RVOL works for ANY stock, not just the
    # nightly-precomputed watchlist. It populates within a poll cycle.
    for sym in symbol_list:
        if load_profile(sym) is None:
            session_type = "globex" if sym.startswith("/") else "rth"
            _schedule_profile_build(provider, sym, session=session_type)

    try:
        bars_by_symbol = await _wl_cached_bars_batch(provider, symbol_list, days=12)
    except BrokerError as exc:
        return JSONResponse({"error": str(exc)}, status_code=502)

    # Slow tier: 52w/ATR/SMA baselines (equities only; long-TTL cache).
    equity_symbols = [s for s in symbol_list if not s.startswith("/")]
    baselines = await _wl_baselines_batch(provider, equity_symbols)

    # SPY day change for the "vs SPY" relative-performance column — one
    # cache-shared bars call (free when SPY is already on the list).
    spy_change_pct: float | None = None
    try:
        spy_bars = (await _wl_cached_bars_batch(provider, ["SPY"], days=12)).get("SPY") or []
        if len(spy_bars) >= 2 and float(spy_bars[-2].close):
            spy_change_pct = round(
                (float(spy_bars[-1].close) - float(spy_bars[-2].close))
                / float(spy_bars[-2].close)
                * 100,
                2,
            )
    except BrokerError:
        logger.warning("Watchlist SPY change fetch failed", exc_info=True)

    if symbol_list and not any(bars_by_symbol.get(s) for s in symbol_list):
        logger.error(
            "Watchlist market: bar fetch returned no data for any of %d symbols — likely provider auth/data outage",
            len(symbol_list),
        )
        return JSONResponse(
            {"error": "market data unavailable: no bars for any requested symbol"},
            status_code=502,
        )

    intraday_by_symbol: dict[str, list[StockBar]] = {}
    try:
        intraday_by_symbol = await _wl_cached_bars_batch(
            provider, symbol_list, timeframe="5Min", days=4
        )
    except BrokerError as exc:
        logger.warning("Watchlist intraday bars failed: %s", exc)

    # Extended-hours quotes — Schwab only; fail-soft to empty dict on any error.
    ext_quotes: dict[str, dict] = {}
    if hasattr(provider, "aget_quotes"):
        try:
            from brokers.hybrid_market_data import HybridMarketData
            from brokers.schwab_market_data import SchwabMarketData

            if isinstance(provider, (SchwabMarketData, HybridMarketData)):
                ext_quotes = await provider.aget_quotes(symbol_list)
        except Exception:
            logger.warning("Extended-hours quote fetch failed; continuing without", exc_info=True)

    session = _market_session()

    quotes: dict[str, dict] = {}
    for sym in symbol_list:
        bars = bars_by_symbol.get(sym, [])
        if not bars:
            continue
        closes = [b.close for b in bars]
        latest = bars[-1]
        price = closes[-1]
        prev = closes[-2] if len(closes) >= 2 else price
        change_pct = ((price - prev) / prev * 100) if prev else 0.0
        intraday = _session_intraday_closes(
            intraday_by_symbol.get(sym, []), futures=sym.startswith("/")
        )

        # Futures symbols (e.g. /ES, /NQ) have no fundamentals, extended session,
        # volume profile, or earnings — skip all equity-only enrichment.
        is_future = sym.startswith("/")

        if is_future:
            day_high = float(latest.high)
            day_low = float(latest.low)
            day_volume = float(latest.volume)
            spark_closes = closes[-7:]
            roll = _futures_price_roll(bars, intraday_by_symbol.get(sym, []))
            if roll is not None:
                price = roll["price"]
                prev = roll["prev_close"]
                change_pct = roll["change_pct"]
                day_high = roll["day_high"]
                day_low = roll["day_low"]
                day_volume = roll["volume"]
                spark_closes = (closes + [price])[-7:]
            quotes[sym] = {
                "price": price,
                "change_pct": round(change_pct, 2),
                "sparkline": [round(c, 4) for c in spark_closes],
                "prev_close": prev,
                "volume": day_volume,
                "day_high": day_high,
                "day_low": day_low,
                "intraday": intraday,
                "market_session": session,
                "ext_price": None,
                "ext_change_pct": None,
                "pe": None,
                "eps": None,
                "div_yield": None,
                "market_cap": None,
                "last_earnings": None,
                "next_earnings": None,
                "rvol": _compute_rvol_field(sym, intraday_by_symbol.get(sym, []), session="globex"),
            }
            continue

        eq = ext_quotes.get(sym, {})

        shares_outstanding = eq.get("shares_outstanding")
        market_cap: float | None = None
        if shares_outstanding is not None:
            try:
                market_cap = round(float(shares_outstanding) * price, 2)
            except (TypeError, ValueError):
                pass

        today_iso = date.today().isoformat()
        try:
            _next_earnings = next_earnings_date(sym, on_date=today_iso)
        except Exception:
            _next_earnings = None

        # "closed" (weekend/holiday) included: Schwab's quote retains the last
        # extended print, and the chart candles (extended_hours=True) end on it —
        # omitting it left watchlist/label a session behind the visible candles.
        _ext_sessions = ("pre", "post", "overnight", "closed")
        ext_price_val = eq.get("ext_last") if session in _ext_sessions else None
        ext_change_val = eq.get("ext_change_pct") if session in _ext_sessions else None

        # Gap at the open vs prior close — from the daily bar already in hand.
        gap_pct: float | None = None
        try:
            if prev and len(bars) >= 2:
                gap_pct = round((float(latest.open) - prev) / prev * 100, 2)
        except (TypeError, ValueError):
            pass

        quotes[sym] = {
            "price": price,
            "change_pct": round(change_pct, 2),
            "sparkline": [round(c, 4) for c in closes[-7:]],
            "prev_close": prev,
            "volume": float(latest.volume),
            "day_high": float(latest.high),
            "day_low": float(latest.low),
            "gap_pct": gap_pct,
            "w52_high": (baselines.get(sym) or {}).get("w52_high"),
            "w52_low": (baselines.get(sym) or {}).get("w52_low"),
            "atr14": (baselines.get(sym) or {}).get("atr14"),
            "sma50": (baselines.get(sym) or {}).get("sma50"),
            "sma200": (baselines.get(sym) or {}).get("sma200"),
            "intraday": intraday,
            "market_session": session,
            "ext_price": ext_price_val,
            "ext_change_pct": ext_change_val,
            "overnight_delayed": bool(eq.get("overnight_delayed")),
            "pe": eq.get("pe_ratio"),
            "eps": eq.get("eps"),
            "div_yield": eq.get("div_yield"),
            "market_cap": market_cap,
            # Our own calendar only — SEC EDGAR now supplies past announce dates, so
            # this must not depend on Schwab (that would gate the field on the user
            # holding a Schwab account).
            "last_earnings": last_earnings_date(sym, today_iso),
            "next_earnings": _next_earnings,
        }
        quotes[sym]["rvol"] = _compute_rvol_field(sym, intraday_by_symbol.get(sym, []))

        # Push ext fields into the WS hub so a streamed quote frame carries the
        # same pre/post/overnight pricing the phone headlines (Schwab level-one
        # stream doesn't emit ext fields; without this they'd only refresh on the
        # 6-s REST poll).
        try:
            await market_hub.apply_extended(
                sym,
                ext_price=ext_price_val,
                ext_change_pct=ext_change_val,
                market_session=session,
                broadcast=True,
            )
        except Exception:
            logger.debug("apply_extended failed for %s", sym, exc_info=True)

    try:
        from db.abnormality import get_state_many

        states = get_state_many(list(quotes))
        for sym, q in quotes.items():
            q["abnorm"] = _abnorm_field(states.get(sym))
    except Exception:
        logger.debug("abnormality watchlist attach failed", exc_info=True)

    return {"quotes": quotes, "spy_change_pct": spy_change_pct}


_profile_build_attempts: dict[str, float] = {}
_PROFILE_BUILD_COOLDOWN_S = 600  # don't re-attempt a symbol's profile more than once / 10 min


def _schedule_profile_build(provider, symbol: str, *, session: str = "rth") -> None:
    """Fire-and-forget a volume-profile build for one symbol, deduped by cooldown.

    Guards against hammering Schwab when a symbol can't form a profile (illiquid →
    compute returns None → no file saved → would otherwise retry every poll).
    Pass ``session="globex"`` for futures symbols."""
    now = time.time()
    if now - _profile_build_attempts.get(symbol, 0.0) < _PROFILE_BUILD_COOLDOWN_S:
        return
    _profile_build_attempts[symbol] = now

    async def _run() -> None:
        try:
            await build_profile(provider, symbol, session=session)
        except Exception:
            logger.exception("On-demand volume-profile build failed for %s", symbol)

    asyncio.create_task(_run())


def _compute_rvol_field(
    symbol: str, intraday_bars: list[StockBar], *, session: str = "rth"
) -> dict | None:
    try:
        profile = load_profile(symbol)
        if profile is None or not intraday_bars:
            return None
        result = rvol_now(profile, intraday_bars, session=session)
        if result is None or result.value is None:
            if result is not None:
                return {"value": None, "state": result.state}
            return None
        state = result.state
        if state == "settled":
            latest_date = max(
                datetime.fromisoformat(b.time.replace("Z", "+00:00")).astimezone(_ET).date()
                for b in intraday_bars
            )
            if latest_date < datetime.now(_ET).date():
                state = "overnight"
        return {"value": result.value, "state": state}
    except Exception as exc:  # RVOL is best-effort — one symbol must never 500 the watchlist
        logger.warning("RVOL compute failed for %s: %s", symbol, exc)
        return None


def _session_intraday_closes(bars: list[StockBar], *, futures: bool = False) -> list[float]:
    if not bars:
        return []

    def _bar_et(bar: StockBar) -> datetime:
        return datetime.fromisoformat(bar.time.replace("Z", "+00:00")).astimezone(_ET)

    # Futures trade ~23h on Globex with no RTH window — group by Globex session
    # (18:00 ET prev day -> 17:00 ET) and return the latest session's closes. The
    # RTH path below would return [] off-RTH, which the sparkline can't fall back from.
    if futures:
        from research.intraday_profile import globex_session_date

        sessions = [
            (sd, bar) for bar in bars if (sd := globex_session_date(_bar_et(bar))) is not None
        ]
        if not sessions:
            return []
        latest = max(sd for sd, _ in sessions)
        return [round(bar.close, 4) for sd, bar in sessions if sd == latest]

    session_date = _bar_et(bars[-1]).date()
    open_minutes = _SESSION_OPEN[0] * 60 + _SESSION_OPEN[1]
    close_minutes = _SESSION_CLOSE[0] * 60 + _SESSION_CLOSE[1]

    kept: list[StockBar] = []
    for bar in bars:
        et = _bar_et(bar)
        if et.date() != session_date:
            continue
        bar_minutes = et.hour * 60 + et.minute
        if open_minutes <= bar_minutes < close_minutes:
            kept.append(bar)

    return [round(b.close, 4) for b in kept]


def _futures_price_roll(daily_bars: list[StockBar], intraday_bars: list[StockBar]) -> dict | None:
    """Roll a futures quote forward when the 5-min series has a whole Globex
    session the daily series lacks — Schwab's futures DAILY bars can lag a
    session (e.g. the 2026-07-03 holiday half-session never got a daily bar),
    which froze the row's price at the prior settle while its own intraday
    line showed the newer session. Returns replacement quote fields, or None
    when the dailies are current.
    """
    if not daily_bars or not intraday_bars:
        return None
    from research.intraday_profile import globex_session_date

    def _bar_et(bar: StockBar) -> datetime:
        return datetime.fromisoformat(bar.time.replace("Z", "+00:00")).astimezone(_ET)

    sessions = [
        (sd, bar) for bar in intraday_bars if (sd := globex_session_date(_bar_et(bar))) is not None
    ]
    if not sessions:
        return None
    latest_session = max(sd for sd, _ in sessions)
    last_daily_date = datetime.fromisoformat(daily_bars[-1].time.replace("Z", "+00:00")).date()
    if latest_session <= last_daily_date:
        return None

    session_bars = [bar for sd, bar in sessions if sd == latest_session]
    price = session_bars[-1].close
    prev = daily_bars[-1].close
    return {
        "price": price,
        "prev_close": prev,
        "change_pct": ((price - prev) / prev * 100) if prev else 0.0,
        "day_high": float(max(b.high for b in session_bars)),
        "day_low": float(min(b.low for b in session_bars)),
        "volume": float(sum(b.volume for b in session_bars)),
    }


@router.put("/api/watchlist")
def api_put_watchlist(body: dict = Body(...)):
    symbols = body.get("symbols")
    if not isinstance(symbols, list):
        return JSONResponse({"error": "symbols must be a list"}, status_code=400)
    saved = save_watchlist(symbols)
    _kick_earnings_alert_catchup()
    return {"symbols": saved}


@router.post("/api/watchlist")
def api_add_watchlist_symbol(body: dict = Body(...)):
    symbol = str(body.get("symbol", "")).strip().upper()
    if not symbol:
        return JSONResponse({"error": "symbol is required"}, status_code=400)
    try:
        symbols = add_watchlist_symbol(symbol)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    _kick_earnings_alert_catchup()
    return {"symbols": symbols}


@router.delete("/api/watchlist/{symbol}")
def api_remove_watchlist_symbol(symbol: str):
    sym = symbol.strip().upper()
    try:
        updated = remove_watchlist_symbol(sym)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    return {"symbols": updated}
