"""Per-symbol company info API — profile + fundamentals + our facts + news (read-only).

Aggregates four sources CONCURRENTLY and fail-soft for the company-info modal: the
Finnhub company profile, curated fundamentals, and recent news (best-effort, free
tier — a missing key just omits those sections), plus the app's OWN rich per-symbol
context (``ai.perception.context.build_symbol_context`` — quote, RVOL, technicals,
session-node levels, decision win%/EV, earnings). A slow or failed source never
blocks the others and never 500s; it degrades to an empty section.

Also exposes an aggregated watchlist-news feed (news across the union of symbols
in ALL watchlists) for a dashboard news widget.

Places no orders and mutates nothing. Registered by ``api/main`` via
``app.include_router`` (behind SecurityMiddleware), same as ``api/scan_routes``.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter

router = APIRouter(prefix="/api/ticker", tags=["ticker"])
logger = logging.getLogger(__name__)


@router.get("/watchlist/news")
async def api_watchlist_news(limit: int = 50) -> dict[str, Any]:
    """Recent news aggregated across the union of symbols in ALL watchlists.

    Fetches Finnhub company news per symbol with bounded concurrency and is
    fail-soft per symbol — a single bad symbol / Finnhub error never 500s the
    whole feed, it's just dropped. Items are deduped by url and returned
    newest-first, capped at ``limit``.
    """
    from config.settings import get_watchlists_state
    from signals.finnhub import afetch_company_news

    state = get_watchlists_state()
    symbols: set[str] = set()
    for lst in state.get("lists", []):
        for s in lst.get("symbols", []):
            if isinstance(s, str) and s.strip():
                symbols.add(s.strip().upper())

    # Finnhub only understands plain equity tickers — skip options/futures-style
    # symbols (contain a space, or start with "/" e.g. "/ES").
    eligible = sorted(s for s in symbols if " " not in s and not s.startswith("/"))

    sem = asyncio.Semaphore(8)

    async def _fetch(sym: str) -> list[dict[str, Any]]:
        async with sem:
            try:
                items = await afetch_company_news(sym, days=7, limit=8)
            except Exception:  # noqa: BLE001 — fail-soft per symbol
                logger.warning("Watchlist news fetch failed for %s", sym, exc_info=True)
                return []
            return [{**item, "symbol": sym} for item in items]

    results = await asyncio.gather(*(_fetch(sym) for sym in eligible))

    seen_urls: set[str] = set()
    all_items: list[dict[str, Any]] = []
    symbols_with_news = 0
    for items in results:
        if items:
            symbols_with_news += 1
        for item in items:
            url = item.get("url")
            if not url or url in seen_urls:
                continue
            seen_urls.add(url)
            all_items.append(item)

    all_items.sort(key=lambda item: item.get("datetime") or "", reverse=True)

    return {
        "news": all_items[:limit],
        "symbol_count": len(eligible),
        "symbols_with_news": symbols_with_news,
    }


@router.get("/{symbol}")
async def api_ticker_info(symbol: str) -> dict[str, Any]:
    """Company profile + fundamentals + our per-symbol facts + recent news."""
    # Lazy imports keep the router decoupled from the app's import graph and the
    # (optional) Finnhub client — same pattern as api/scan_routes.
    from ai.perception.context import build_symbol_context
    from db.base import DEFAULT_DB_PATH
    from signals.finnhub import (
        afetch_company_metrics,
        afetch_company_news,
        afetch_company_profile,
    )

    sym = symbol.strip().upper()

    async def _facts() -> dict[str, Any]:
        # build_symbol_context is fail-soft per block, but guard the whole call so
        # an unexpected failure can't take down the aggregate response.
        try:
            return await build_symbol_context(sym, db_path=DEFAULT_DB_PATH)
        except Exception:  # noqa: BLE001 — fail-soft; return empty facts
            return {}

    profile, metrics, news, facts = await asyncio.gather(
        afetch_company_profile(sym),
        afetch_company_metrics(sym),
        afetch_company_news(sym),
        _facts(),
    )

    return {
        "symbol": sym,
        "profile": profile,
        "metrics": metrics,
        "news": news,
        "facts": facts,
    }
