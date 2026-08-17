"""Options-side read routes: chain, flow prints, index pressure, overnight flow.

Extracted from ``api/main`` (strangler round 2). Read-only — nothing here places
orders. Registered by ``api/main`` via ``app.include_router``.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from api.alpaca_guard import _require_alpaca
from brokers.errors import BrokerError
from trading.market_hours import _ET

logger = logging.getLogger(__name__)
router = APIRouter()


_OPTIONS_CHAIN_CACHE: dict[tuple[str, str, str], tuple[float, dict]] = {}
# Coalesces concurrent readers (desktop + phone on the same chain) onto one
# upstream fetch; it is NOT the update cadence. It was 30 s, which stacked with
# the widget's own 30 s poll into ~60 s worst-case staleness on quotes that
# arrive sub-second — the reason the chain looked frozen during RTH.
_OPTIONS_CHAIN_TTL_S = 1.0


@router.get("/api/options-chain")
async def api_options_chain(
    symbol: str = "",
    expiry: str | None = None,
    strikes: str = "10",
):
    """Read-only OPRA option chain for one underlying + expiration."""
    broker = _require_alpaca()
    if isinstance(broker, JSONResponse):
        return broker

    sym = symbol.strip().upper()
    if not sym:
        return JSONResponse({"error": "symbol required"}, status_code=400)

    strikes_key = strikes.strip().lower() if strikes else "10"
    if strikes_key != "all":
        try:
            n = int(strikes_key)
            if n <= 0:
                return JSONResponse(
                    {"error": "strikes must be a positive integer or 'all'"},
                    status_code=400,
                )
            strikes_key = str(n)
        except ValueError:
            return JSONResponse(
                {"error": "strikes must be a positive integer or 'all'"},
                status_code=400,
            )

    cache_expiry = (expiry or "").strip()[:10] if expiry else ""
    cache_key = (sym, cache_expiry, strikes_key)
    now = time.time()
    hit = _OPTIONS_CHAIN_CACHE.get(cache_key)
    if hit is not None and now - hit[0] < _OPTIONS_CHAIN_TTL_S:
        return hit[1]

    from brokers.options_chain import build_options_chain_sync

    try:
        result = await asyncio.to_thread(build_options_chain_sync, broker, sym, expiry, strikes_key)
    except BrokerError as exc:
        return JSONResponse({"error": str(exc)}, status_code=502)
    except Exception as exc:  # noqa: BLE001 — read-only endpoint, fail-soft
        logger.exception("options-chain: fetch failed for %s", sym)
        return JSONResponse({"error": str(exc)}, status_code=500)

    _OPTIONS_CHAIN_CACHE[cache_key] = (now, result)
    return result


@router.get("/api/options-flow")
async def api_options_flow(
    limit: int = 200,
    symbol: str | None = None,
    event_type: str | None = None,
    min_premium: float | None = None,
    since: str | None = None,
    order: str = "recent",
):
    """Recent unusual options-activity events (Mode-A perception).

    Facts about the OPRA tape — NOT a buy/sell signal. Each event carries a
    ``caveat`` and never a win%/EV/confidence. Populated by the nightly EOD scan
    (``OPTIONS_FLOW_ENABLED``); returns an empty list until that has run.
    ``event_type`` accepts a comma-separated list to filter the tape by kind.
    ``order`` is recent (default), premium, unusual, or conviction.
    """
    from db.options_flow import recent_events

    capped = max(1, min(int(limit), 250))
    order_key = order if order in ("recent", "premium", "unusual", "conviction") else "recent"
    try:
        events = recent_events(
            symbol=symbol,
            event_type=event_type,
            min_premium=min_premium,
            since=since,
            order=order_key,
            limit=capped,
        )
    except Exception as exc:  # noqa: BLE001 — read-only endpoint, fail-soft
        return JSONResponse({"error": str(exc)}, status_code=500)
    return {
        "as_of": datetime.now(_ET).isoformat(timespec="seconds"),
        "count": len(events),
        "events": events,
        # Survives every transport (honesty red-team rule): the tape is perception.
        "caveat": (
            "OPRA tape facts, not a signal. rarity_p = this print vs this name's own "
            "daily maxima in its right/tenor/moneyness bucket (how quiet the bucket "
            "usually is); conviction = rarity x shape rank, perception not a forecast; "
            "no direction, no win%."
        ),
    }


@router.get("/api/index-pressure")
async def api_index_pressure(
    date: str | None = None, series: str | None = None, window: str = "open"
):
    """Session-bookend index/megacap flow pressure — raw perception.

    ``window=open`` (09:30-10:00 ET, default) or ``close`` (15:30-16:00 ET).
    Premium-weighted net imbalance per underlying (bull = call buys + put sells;
    bear = put buys + call sells; the undetermined remainder is surfaced as
    ``unclassified_usd``, never guessed). NOT a signal: no score, no win%, and no
    "abnormal" judgment until the per-minute baseline has accrued enough days
    (``baseline_days`` is returned so the UI can label the warm-up honestly).
    ``series=SYM`` adds that underlying's per-minute rows for a sparkline.
    """
    from db.index_pressure import baseline_days, day_summary, latest_date, minute_series

    win = window.strip().lower()
    if win not in ("open", "close"):
        return JSONResponse({"error": "window must be 'open' or 'close'"}, status_code=400)
    target = (date or datetime.now(_ET).date().isoformat()).strip()
    try:
        from signals.options_flow.constants import GAUGE_ZERO_DTE_PICKS

        rows = day_summary(target, window=win)
        fallback = False
        # Carry the last session rather than a blank tile: outside the window
        # (weekend, pre-open) an un-dated request serves the newest date with
        # data, labeled with that date — never presented as today.
        if not rows and date is None:
            prior = latest_date(window=win)
            if prior and prior != target:
                target, rows, fallback = prior, day_summary(prior, window=win), True

        payload: dict = {
            "as_of": datetime.now(_ET).isoformat(timespec="seconds"),
            "date": target,
            "fallback": fallback,
            "window": win,
            "window_et": "09:30-10:00" if win == "open" else "15:30-16:00",
            "rows": rows,
            "baseline_days": baseline_days(window=win),
            # Disclosed truncation: index 0-1DTE coverage = the top-N contracts
            # by day-$vol per name (ATM cluster), not the whole 0DTE chain.
            "lane": {"zero_dte_top_n": GAUGE_ZERO_DTE_PICKS},
        }
        if series:
            payload["series"] = {
                "underlying": series.strip().upper(),
                "minutes": minute_series(target, series, window=win),
            }
    except Exception as exc:  # noqa: BLE001 — read-only endpoint, fail-soft
        return JSONResponse({"error": str(exc)}, status_code=500)
    return payload


@router.get("/api/index-pressure/history")
async def api_index_pressure_history(days: int = 40):
    """Daily gauge-tilt trend: per session the open/close net premium plus how
    SPY finished that day (close-vs-prior-close %). Co-occurrence display so
    drift toward bull/bear is visible over weeks — never an accuracy score.
    """
    from signals.options_flow.pressure_history import pressure_trend

    try:
        rows = await asyncio.to_thread(pressure_trend, min(max(days, 1), 120))
    except Exception as exc:  # noqa: BLE001 — read-only endpoint, fail-soft
        return JSONResponse({"error": str(exc)}, status_code=500)
    return {
        "as_of": datetime.now(_ET).isoformat(timespec="seconds"),
        "days": rows,
    }


@router.get("/api/overnight-flow")
async def api_overnight_flow(nights: int = 60):
    """Last night's overnight (Cboe GTH) index-option line + trailing nights.

    The morning job (report kind ``overnight_flow``, ~9:16 ET) writes the line;
    this serves it with a trailing-notional history for the widget sparkline.
    Before the first live line exists the newest history night is served in its
    place (labeled with its own session date). Activity only, never direction —
    no overnight quotes exist on any lane we hold; the z is context for the
    open, not a signal.
    """
    from signals.options_flow.gth_morning import latest_line, load_history

    try:
        line, hist = await asyncio.gather(
            asyncio.to_thread(latest_line),
            asyncio.to_thread(load_history),
        )
        trail = [
            {
                "session": r.get("session"),
                "notional": r.get("notional"),
                "put_share_notional": r.get("put_share_notional"),
            }
            for r in hist[-min(max(nights, 0), 260):]
            if r.get("prints", 0) > 0
        ]
    except Exception as exc:  # noqa: BLE001 — read-only endpoint, fail-soft
        return JSONResponse({"error": str(exc)}, status_code=500)
    return {"line": line, "history": trail}
