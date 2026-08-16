"""Fusion background jobs: intake, ATR refresh, session-levels producer, reason,
lifecycle (+ EOD). Read-only over the backend's bars caches — never fetches bars.

Extracted verbatim from ``api/main.py`` (2026-08-16 god-module split). ``api/main``
imports the ``_fusion_*_once`` runners into ``_register_background_jobs``. The one
back-reference is ``api.main._BARS_CACHE`` (the /api/bars cache still lives there),
read lazily at call time so test monkeypatches on ``api.main`` keep working.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime

from api import watchlist_bars_cache
from brokers.factory import get_market_data_provider
from db.base import DEFAULT_DB_PATH
from signals import indicators
from signals.session_node_strategy import compute_session_levels_batch
from signals.universe import all_watchlist_symbols as _all_watchlist_symbols
from trading.market_hours import _ET

logger = logging.getLogger(__name__)

_wl_bars_cache = watchlist_bars_cache.bars_cache
_WL_BARS_TTL_S = watchlist_bars_cache.BARS_TTL_S
_fusion_shock_until_mono = 0.0
_fusion_lifecycle_eod_last_date: dict = {}


def _bars_cache():
    # /api/bars cache still lives in api.main — read lazily so monkeypatches on
    # api.main._BARS_CACHE (and the eventual bars-family move) keep working.
    from api.main import _BARS_CACHE

    return _BARS_CACHE


def _fusion_price_from_cache(symbol: str):
    """Read the backend's existing bars caches only; never fetch."""
    from decimal import Decimal

    sym = symbol.strip().upper()
    now_wall = time.time()
    candidates: list[tuple[float, object]] = []
    for (cached_sym, timeframe), (stored_at, bars) in _wl_bars_cache.items():
        if cached_sym == sym and bars:
            ttl = _WL_BARS_TTL_S.get(timeframe, 90.0)
            if now_wall - stored_at <= ttl:
                candidates.append((stored_at, bars[-1]))
    for key, (expires, response) in _bars_cache().items():
        if key and key[0] == sym and expires > time.monotonic():
            bars = response.get("bars") if isinstance(response, dict) else None
            if bars:
                candidates.append((expires, bars[-1]))
    if not candidates:
        return None
    latest = max(candidates, key=lambda item: item[0])[1]
    close = latest.get("close") if isinstance(latest, dict) else getattr(latest, "close", None)
    return Decimal(str(close)) if close is not None else None


def _fusion_cached_daily_move(symbol: str) -> float | None:
    sym = symbol.strip().upper()
    newest: tuple[float, list] | None = None
    hit = _wl_bars_cache.get((sym, "1Day"))
    if hit is not None and hit[1]:
        newest = hit
    for key, (expires, response) in _bars_cache().items():
        if key and key[0] == sym and key[1] == "1Day":
            bars = response.get("bars") if isinstance(response, dict) else None
            if bars and (newest is None or expires > newest[0]):
                newest = (expires, bars)
    if newest is None or len(newest[1]) < 2:
        return None
    bars = newest[1]
    previous = bars[-2]
    current = bars[-1]
    prev_close = previous.get("close") if isinstance(previous, dict) else previous.close
    last_close = current.get("close") if isinstance(current, dict) else current.close
    if not prev_close:
        return None
    return (float(last_close) / float(prev_close) - 1.0) * 100.0


def _fusion_shock_active() -> bool:
    global _fusion_shock_until_mono
    from config.idea_engine import (
        FUSION_SHOCK_WINDOW_MINUTES,
        FUSION_SPY_SHOCK_MOVE_PCT,
        FUSION_VIX_SHOCK_MOVE_PCT,
    )

    now = time.monotonic()
    spy_move = _fusion_cached_daily_move("SPY")
    vix_move = _fusion_cached_daily_move("VIX")
    if (
        spy_move is not None
        and abs(spy_move) >= FUSION_SPY_SHOCK_MOVE_PCT
        or vix_move is not None
        and vix_move >= FUSION_VIX_SHOCK_MOVE_PCT
    ):
        _fusion_shock_until_mono = max(
            _fusion_shock_until_mono, now + FUSION_SHOCK_WINDOW_MINUTES * 60
        )
    return now <= _fusion_shock_until_mono


async def _fusion_intake_once() -> None:
    from signals.fusion.intake import intake_once

    await asyncio.to_thread(
        intake_once,
        price_lookup=_fusion_price_from_cache,
        shock_lookup=_fusion_shock_active,
    )


def _fusion_atr_pending_symbols_sync(as_of: str) -> list[str]:
    """Event-store ∪ watchlist symbols still missing today's cached ATR.

    The UNION walks both event tables end to end — sync SQLite, thread-only."""
    from db.base import DEFAULT_DB_PATH, connect
    from db.equity_tape import init_db as init_equity_tape
    from db.options_flow import init_db as init_options_flow

    init_options_flow(DEFAULT_DB_PATH)
    init_equity_tape(DEFAULT_DB_PATH)
    with connect(DEFAULT_DB_PATH) as conn:
        rows = conn.execute(
            """
            SELECT underlying AS symbol FROM options_flow_events
            UNION
            SELECT symbol FROM equity_tape_events
            """
        ).fetchall()
        cached_today = {
            str(row["symbol"])
            for row in conn.execute(
                "SELECT symbol FROM fusion_atr_cache WHERE as_of_date = ?", (as_of,)
            ).fetchall()
        }
    return sorted(
        ({str(row["symbol"]).upper() for row in rows} | set(_all_watchlist_symbols()))
        - cached_today
    )


def _fusion_atr_store_sync(bars_by_symbol, as_of: str) -> None:
    """Compute ATR-14 per symbol and cache it — sync SQLite writes, thread-only."""
    from decimal import Decimal

    from db.fusion import put_atr

    today_et = datetime.now(_ET).date()
    for symbol, bars in bars_by_symbol.items():
        completed = []
        for bar in bars:
            raw_time = getattr(bar, "time", None)
            try:
                bar_time = datetime.fromisoformat(str(raw_time).replace("Z", "+00:00"))
                bar_day = (
                    bar_time.astimezone(_ET).date()
                    if bar_time.tzinfo is not None
                    else bar_time.date()
                )
            except (TypeError, ValueError):
                bar_day = None
            if bar_day == today_et:
                continue
            completed.append(bar)
        highs = [float(bar.high) for bar in completed]
        lows = [float(bar.low) for bar in completed]
        closes = [float(bar.close) for bar in completed]
        atr14 = indicators.atr(highs, lows, closes, 14)
        if atr14 is not None:
            put_atr(symbol, as_of, Decimal(str(round(atr14, 6))))


async def _fusion_atr_refresh_once() -> None:
    """Once-daily external work, deliberately separate from trigger intake."""
    as_of = datetime.now(_ET).date().isoformat()
    symbols = await asyncio.to_thread(_fusion_atr_pending_symbols_sync, as_of)
    if not symbols:
        return
    provider = get_market_data_provider()
    bars_by_symbol = await provider.aget_stock_bars_batch(symbols, timeframe="1Day", days=40)
    await asyncio.to_thread(_fusion_atr_store_sync, bars_by_symbol, as_of)


def _fusion_levels_pending_symbols_sync(as_of: str) -> list[str]:
    """Event-store ∪ watchlist symbols still missing today's cached session levels.

    The UNION walks both event tables end to end — sync SQLite, thread-only. Same
    universe as the ATR pending set (fusion_atr_cache's sibling), so a symbol that
    earns an ATR stamp earns session levels: pending == universe minus stamped-today
    (a symbol whose as_of_date is still < today stays pending)."""
    from db.base import DEFAULT_DB_PATH, connect
    from db.equity_tape import init_db as init_equity_tape
    from db.options_flow import init_db as init_options_flow

    init_options_flow(DEFAULT_DB_PATH)
    init_equity_tape(DEFAULT_DB_PATH)
    with connect(DEFAULT_DB_PATH) as conn:
        rows = conn.execute(
            """
            SELECT underlying AS symbol FROM options_flow_events
            UNION
            SELECT symbol FROM equity_tape_events
            """
        ).fetchall()
        cached_today = {
            str(row["symbol"])
            for row in conn.execute(
                "SELECT symbol FROM fusion_session_levels WHERE as_of_date = ?",
                (as_of,),
            ).fetchall()
        }
    return sorted(
        ({str(row["symbol"]).upper() for row in rows} | set(_all_watchlist_symbols()))
        - cached_today
    )


def _fusion_levels_store_sync(payloads: dict[str, dict], as_of: str) -> int:
    """Stamp a batch of computed levels — sync SQLite writes, thread-only. One hop for
    the whole sweep, not one per symbol (the midnight pass stamps ~9.8K of them)."""
    from db.fusion import put_session_levels

    stamped = 0
    for symbol, payload in payloads.items():
        levels = list(payload.get("levels") or [])
        if not levels:
            continue
        put_session_levels(symbol, as_of, levels)
        stamped += 1
    return stamped


async def _fusion_levels_refresh_once() -> None:
    """Once-daily external work, deliberately separate from trigger intake.

    Structure sibling of _fusion_atr_refresh_once: stamps each pending symbol's latest
    completed session nodes into fusion_session_levels so the hot intake path only ever
    READS them (never a live compute during a burst). Fetches through
    get_market_data_provider() — the Alpaca-backed source, same lane as the ATR
    producer — so this never bursts Schwab.

    BATCHED, and that is what keeps it affordable: compute_session_levels_batch asks the
    provider once per 100-symbol slice instead of twice per symbol, so the midnight pass
    (~9.8K symbols, every one pending at the as_of rollover) costs ~120 requests instead
    of ~19,750 — measured 2026-08-12 at 7,349/min peak against a 200/min ceiling. It also
    removes the need for any back-off: a symbol that yields nothing rides along in a
    slice for free, so it can be re-attempted every cycle and recovers the moment its
    data appears, with no interval to tune and no state to lose across a restart.
    """
    from db.lane_stats import bump

    as_of = datetime.now(_ET).date().isoformat()
    symbols = await asyncio.to_thread(_fusion_levels_pending_symbols_sync, as_of)
    if not symbols:
        return
    try:
        payloads = await compute_session_levels_batch(symbols)
    except Exception:
        # Per-slice failures are already absorbed inside the batch; reaching here means
        # the sweep itself broke. Count it and leave everyone pending for the next pass.
        logger.exception("fusion levels refresh failed (%d symbols)", len(symbols))
        await asyncio.to_thread(bump, "fusion_intake", "levels_refresh_failed")
        return
    stamped = await asyncio.to_thread(_fusion_levels_store_sync, payloads, as_of)
    if stamped:
        await asyncio.to_thread(bump, "fusion_intake", "levels_stamped", stamped)


async def _fusion_reason_once() -> None:
    """Fusion C5 reason lane (research/reason.run_reason_pass), wired but DARK.

    REASON_EMIT_ENABLED gates the whole lane; while it is False this is a cheap
    no-op that never even builds an LLM client (the poll fires every 120s in market
    hours). The ARM commit flips the flag — see the ARMING CHECKLIST in
    config/idea_engine.py. run_reason_pass re-checks the flag as a belt-and-braces
    guard, but the check here is what keeps the dark poll free of client construction."""
    import config.idea_engine as _idea_cfg

    if not _idea_cfg.REASON_EMIT_ENABLED:
        return
    from ai.llm.factory import get_llm
    from config.research_engine import resolve_role_model
    from research.reason import run_reason_pass

    llm_triage = get_llm(model=resolve_role_model("triage"))
    llm_reason = get_llm(model=resolve_role_model("reason"))
    await run_reason_pass(
        db_path=DEFAULT_DB_PATH,
        now=datetime.now(_ET),
        llm_triage=llm_triage,
        llm_reason=llm_reason,
    )


async def _fusion_lifecycle_once() -> None:
    """Consolidated episode + armed-idea lifecycle sweep (signals/fusion/lifecycle).

    Mirrors _fusion_intake_once: offloaded to a thread, passing the same intake
    price cache the dots are stamped from. Runs every 60s in market hours."""
    from signals.fusion.lifecycle import sweep_lifecycle

    await asyncio.to_thread(
        sweep_lifecycle, datetime.now(_ET), price_lookup=_fusion_price_from_cache
    )


async def _fusion_lifecycle_eod_once() -> None:
    """One unconditional post-close lifecycle sweep so hard-TTL / decay-to-noise
    stamp at EOD — the 60s market-hours job stops at 16:00 ET. Self-gates to
    after-close, once per trading day (the _schwab_snapshot_once idiom)."""
    now_et = datetime.now(_ET)
    if now_et.weekday() >= 5:
        return
    after_close = now_et.hour > 16 or (now_et.hour == 16 and now_et.minute >= 5)
    if not after_close:
        return
    today = now_et.date()
    if _fusion_lifecycle_eod_last_date.get("date") == today:
        return
    await _fusion_lifecycle_once()
    _fusion_lifecycle_eod_last_date["date"] = today
