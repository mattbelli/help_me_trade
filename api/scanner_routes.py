"""RVOL scanner + abnormality scanner routes, and the RVOL-spike alert job.

Extracted from ``api/main`` (strangler round 2). ``_rvol_bucket_now`` /
``_compute_rvol_from_total_volume`` are the shared "cumulative totalVolume vs the
symbol's volume profile" screen used by both the scanner route and the alert
job; ``_abnorm_field`` is reused by ``api/watchlist_routes`` (watchlist market
rows). ``_rvol_alert_once`` is scheduled by ``api/main``'s job registry;
``api_scanner_rvol`` is also called in-process by ``ai/perception/resources``.
Registered by ``api/main`` via ``app.include_router``.
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import date, datetime

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from brokers.errors import BrokerError
from brokers.factory import get_market_data_provider
from brokers.market_clock import market_session as _market_session
from signals.universe import all_watchlist_symbols as _all_watchlist_symbols
from signals.volume_profile import load_profile, profile_dir
from trading.market_hours import _ET, _SESSION_OPEN

logger = logging.getLogger(__name__)
router = APIRouter()


RVOL_ALERT_INTERVAL_SECONDS = max(60, int(os.environ.get("RVOL_ALERT_INTERVAL_SECONDS", "300")))
_RVOL_ALERTS_ENABLED = os.environ.get("RVOL_ALERTS_ENABLED", "1").strip() not in (
    "0",
    "false",
    "no",
)
RVOL_ALERT_THRESHOLD = float(os.environ.get("RVOL_ALERT_THRESHOLD", "3.0"))


# ---------------------------------------------------------------------------
# RVOL-spike alert dedup: persisted in the earnings_alerts_sent key store (a
# generic one-row-per-key table) under key rvol:{symbol}:{ET-date} so a mid-day
# restart does not re-fire already-sent alerts (ops MED-5). The job runs at most
# once per RVOL_ALERT_INTERVAL_SECONDS (default 300s), so a plain DB read per
# symbol per tick is cheap — no in-memory memo needed.
# ---------------------------------------------------------------------------
def _rvol_alert_key(symbol: str, today_et: date) -> str:
    return f"rvol:{symbol}:{today_et.isoformat()}"


def _should_rvol_alert(symbol: str, today_et: date) -> bool:
    """Return True if we have NOT already sent an RVOL alert for this symbol today."""
    from db.earnings_alerts import was_sent

    return not was_sent(_rvol_alert_key(symbol, today_et))


def _mark_rvol_alerted(symbol: str, today_et: date) -> None:
    from db.earnings_alerts import mark_sent

    mark_sent(_rvol_alert_key(symbol, today_et))


async def _rvol_alert_once() -> None:
    if _market_session() != "regular":
        return

    bucket = _rvol_bucket_now()
    if bucket is None:
        return

    symbols = _all_watchlist_symbols()
    if not symbols:
        return

    try:
        provider = get_market_data_provider()
    except BrokerError as exc:
        logger.warning("RVOL alert: provider unavailable: %s", exc)
        return

    if not hasattr(provider, "aget_quotes"):
        return

    from brokers.hybrid_market_data import HybridMarketData
    from brokers.schwab_market_data import SchwabMarketData

    if not isinstance(provider, (SchwabMarketData, HybridMarketData)):
        return

    try:
        quotes = await provider.aget_quotes(symbols)
    except Exception:
        logger.exception("RVOL alert: quote fetch failed")
        return

    today_et = datetime.now(_ET).date()

    from signals.notify import notify

    for sym in symbols:
        try:
            q = quotes.get(sym)
            if q is None:
                continue
            total_volume = q.get("total_volume")
            if total_volume is None or total_volume <= 0:
                continue
            profile = load_profile(sym)
            if profile is None:
                continue
            rvol_val = _compute_rvol_from_total_volume(profile, float(total_volume), bucket)
            if rvol_val is None or rvol_val < RVOL_ALERT_THRESHOLD:
                continue
            if not _should_rvol_alert(sym, today_et):
                continue
            price = q.get("regular_last") or q.get("ext_last")
            price_str = f"${price:.2f}" if price else "n/a"
            await notify(
                kind="rvol",
                title=f"Unusual volume: {sym}",
                body=f"{sym} {rvol_val:.1f}× normal volume ({price_str})",
                severity="info",
                action_url=f"/watchlist/{sym}",
                data={
                    "symbol": sym,
                    "rvol": round(rvol_val, 2),
                    "price": price,
                },
            )
            _mark_rvol_alerted(sym, today_et)
            logger.info("RVOL alert sent: %s rvol=%.2f", sym, rvol_val)
        except Exception:
            logger.exception("RVOL alert: error processing symbol %s", sym)


_SCANNER_CHUNK_SIZE = 250  # Schwab get_quotes accepts many symbols; chunk to stay safe


def _rvol_bucket_now() -> int | None:
    """Return the current 5-min bucket index (0–77) based on ET wall clock.

    Returns None if before 09:30 ET (pre-market — totalVolume unreliable for RVOL).
    Clamps to 77 after 16:00 ET (post-close settled).
    """
    now = datetime.now(_ET)
    open_min = _SESSION_OPEN[0] * 60 + _SESSION_OPEN[1]
    now_min = now.hour * 60 + now.minute
    if now_min < open_min:
        return None
    return min((now_min - open_min) // 5, 77)


def _compute_rvol_from_total_volume(
    profile,
    total_volume: float,
    bucket: int,
) -> float | None:
    """Compute RVOL_now from a symbol's cumulative totalVolume and its precomputed profile.

    This is an efficient *screen* using Schwab's totalVolume from the quote block
    (cumulative RTH + extended volume as reported by the exchange), divided by the
    expected cumulative RTH volume at this point in the day.

    Note: totalVolume includes pre-market volume accumulated before 09:30, which may
    inflate RVOL slightly in early morning buckets. Rankings are what matter; the
    per-symbol watchlist RVOL (which uses RTH 5-min bars only) is more precise for
    individual tracking. This scanner is designed for finding anomalies, not for
    exact calibration.

    Returns None if profile is missing, bucket is out of range, or expected volume <= 0.
    """
    if profile is None or bucket < 0 or bucket > 77:
        return None
    expected = profile.median_daily_total * profile.cumfrac[bucket]
    if expected <= 0:
        return None
    return round(total_volume / expected, 2)


@router.get("/api/scanner/rvol")
async def api_scanner_rvol(min_rvol: float = 1.5, limit: int = 50):
    """Market-wide unusual-volume scanner powered by precomputed volume profiles.

    Returns symbols from the ~986-name liquid-core universe whose cumulative
    today volume is unusually high for this time of day, ranked by RVOL descending.

    Query params:
      min_rvol  — minimum RVOL to include (default 1.5)
      limit     — max symbols returned (default 50, max 200)

    Only meaningful with the Schwab provider (requires quote.totalVolume).
    Returns 400 if the active provider is not Schwab.
    Returns an empty results list (not an error) outside RTH (pre/post/closed/overnight).
    """
    from brokers.hybrid_market_data import HybridMarketData
    from brokers.schwab_market_data import SchwabMarketData

    if limit < 1 or limit > 200:
        return JSONResponse({"error": "limit must be 1–200"}, status_code=400)
    if min_rvol < 0:
        return JSONResponse({"error": "min_rvol must be >= 0"}, status_code=400)

    try:
        provider = get_market_data_provider()
    except BrokerError as exc:
        return JSONResponse({"error": str(exc)}, status_code=502)

    if not isinstance(provider, (SchwabMarketData, HybridMarketData)):
        return JSONResponse(
            {
                "error": (
                    "RVOL scanner requires Schwab as the market data provider "
                    "(needs quote.totalVolume). Set MARKET_DATA_PROVIDER=schwab "
                    "or hybrid."
                )
            },
            status_code=400,
        )

    session = _market_session()
    as_of = datetime.now(_ET).isoformat(timespec="seconds")

    # RVOL is only meaningful during RTH — totalVolume before open is unreliable.
    bucket = _rvol_bucket_now()
    if bucket is None or session not in ("regular",):
        return {
            "as_of": as_of,
            "session": session,
            "count": 0,
            "results": [],
            "note": (
                "RVOL scanner only runs during regular RTH session (09:30–16:00 ET). "
                f"Current session: {session!r}."
            ),
        }

    # Gather all symbols that have a cached volume profile.
    try:
        profile_files = list(profile_dir().glob("*.json"))
    except OSError as exc:
        return JSONResponse(
            {"error": f"Cannot read volume profile directory: {exc}"},
            status_code=502,
        )

    if not profile_files:
        return {
            "as_of": as_of,
            "session": session,
            "count": 0,
            "results": [],
            "note": "No volume profiles found. Run the nightly profile precompute first.",
        }

    symbols = [f.stem for f in profile_files]

    # Batch fetch quotes in chunks; gather concurrently.
    chunks = [
        symbols[i : i + _SCANNER_CHUNK_SIZE] for i in range(0, len(symbols), _SCANNER_CHUNK_SIZE)
    ]
    try:
        chunk_results = await asyncio.gather(*(provider.aget_quotes(chunk) for chunk in chunks))
    except BrokerError as exc:
        return JSONResponse(
            {"error": f"Schwab quote fetch failed: {exc}"},
            status_code=502,
        )

    # Merge chunk dicts into one flat map.
    all_quotes: dict[str, dict] = {}
    for chunk_dict in chunk_results:
        all_quotes.update(chunk_dict)

    # Compute RVOL for each symbol; keep those meeting the threshold.
    results: list[dict] = []
    for sym in symbols:
        q = all_quotes.get(sym)
        if q is None:
            continue
        total_volume = q.get("total_volume")
        if total_volume is None or total_volume <= 0:
            continue
        # load_profile respects max_age_days=7 staleness guard.
        from signals.volume_profile import load_profile as _load_profile

        profile = _load_profile(sym)
        if profile is None:
            continue
        rvol_val = _compute_rvol_from_total_volume(profile, float(total_volume), bucket)
        if rvol_val is None or rvol_val < min_rvol:
            continue
        price = q.get("regular_last") or q.get("ext_last")
        results.append(
            {
                "symbol": sym,
                "rvol": rvol_val,
                "total_volume": int(total_volume),
                "price": round(price, 4) if price is not None else None,
            }
        )

    results.sort(key=lambda r: r["rvol"], reverse=True)
    results = results[:limit]

    return {
        "as_of": as_of,
        "session": session,
        "count": len(results),
        "results": results,
    }


def _abnorm_field(row: dict | None) -> dict | None:
    if not row:
        return None
    status = str(row.get("status") or "")
    # abnormality_state is keyed by symbol and never expires — Friday's rows read
    # status='live' all weekend. Stale = not live OR not from today's session.
    session_today = datetime.now(_ET).date().isoformat()
    stale = status != "live" or str(row.get("session") or "") != session_today
    return {
        "range_ratio": row.get("range_ratio"),
        "vol_ratio": row.get("vol_ratio"),
        "rvol_session": row.get("rvol_session"),
        "range_pctile": row.get("range_pctile"),
        "vol_pctile": row.get("vol_pctile"),
        "composite": row.get("composite"),
        "base_state": row.get("base_state"),
        "status": status,
        "session": row.get("session"),
        "stale": stale,
        "label": row.get("label"),
        "caveat": row.get("caveat"),
    }


@router.get("/api/scanner/abnormality")
async def api_scanner_abnormality(
    fired: bool = False,
    paired: bool = False,
    base_low: bool = False,
    base_high: bool = False,
    limit: int = 50,
):
    """Live abnormality state ranked by composite. Surfaces-only — not alerts."""
    from db.abnormality import events_today, list_state, recent_outcomes
    from signals.abnormality.constants import CAVEAT, LABEL
    from signals.abnormality.outcomes import aggregate

    if limit < 1 or limit > 200:
        return JSONResponse({"error": "limit must be 1–200"}, status_code=400)
    rows = list_state(
        fired=fired, paired=paired, base_low=base_low, base_high=base_high, limit=limit
    )
    as_of = datetime.now(_ET).isoformat(timespec="seconds")
    return {
        "as_of": as_of,
        "count": len(rows),
        "results": rows,
        "fires_today": len(events_today()),
        "stats": aggregate(recent_outcomes(500)),
        "label": LABEL,
        "caveat": CAVEAT,
    }
