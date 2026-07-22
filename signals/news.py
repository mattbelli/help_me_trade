"""Store-backed news fetchers (Alpaca/Benzinga) — the Finnhub feed's successors.

Same call shapes ``signals/finnhub.py`` established so consumers swap one
import: the websocket-fed ``db.news`` store is the source of truth, with a
one-page REST top-up when the store is cold for the ask (the stream only
covers headlines published since it started running).
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

logger = logging.getLogger(__name__)


def _ui_item(row: dict[str, Any]) -> dict[str, Any]:
    """db.news row → the UI shape the Finnhub feed established."""
    return {
        "headline": row.get("headline") or "",
        "source": row.get("source") or "",
        "url": row.get("url") or "",
        "datetime": row.get("published_at") or None,
        "summary": row.get("summary") or "",
        "image": row.get("image_url") or "",
    }


async def _rest_topup(symbols: list[str] | None, limit: int) -> None:
    """One Alpaca news REST page, upserted. Fail-soft — never raises."""
    from brokers.alpaca import AlpacaAdapter
    from brokers.factory import get_broker
    from db.news import init_db, normalize_news_item, upsert_news_item

    try:
        broker = get_broker("alpaca")
        if not isinstance(broker, AlpacaAdapter):
            return

        def _fetch_and_store() -> None:
            init_db()
            for raw in broker.get_news(symbols, limit=limit):
                item = normalize_news_item(raw)
                if item is not None:
                    upsert_news_item(item)

        await asyncio.to_thread(_fetch_and_store)
    except Exception:  # noqa: BLE001 — news is best-effort everywhere it's used
        logger.warning("news REST top-up failed for %s", symbols, exc_info=True)


async def afetch_company_news(
    symbol: str,
    *,
    days: int = 7,
    limit: int = 15,
) -> list[dict[str, Any]]:
    """Recent news for one symbol, newest-first — ``{headline, source, url,
    datetime (ISO), summary, image}``, or ``[]``. Store-first; a near-empty
    store triggers one REST page for the symbol."""
    from db.news import recent_news

    sym = symbol.strip().upper()
    if not sym:
        return []
    since = (datetime.now(UTC) - timedelta(days=days)).isoformat()
    rows = await asyncio.to_thread(
        recent_news, symbol=sym, since=since, limit=limit
    )
    if len(rows) < 3:
        await _rest_topup([sym], max(limit, 15))
        rows = await asyncio.to_thread(
            recent_news, symbol=sym, since=since, limit=limit
        )
    return [_ui_item(r) for r in rows]


def _watchlist_symbols() -> set[str]:
    from config.settings import get_watchlists_state

    state = get_watchlists_state()
    eligible: set[str] = set()
    for lst in state.get("lists", []):
        for s in lst.get("symbols", []):
            if isinstance(s, str) and s.strip():
                sym = s.strip().upper()
                if " " not in sym and not sym.startswith("/"):
                    eligible.add(sym)
    return eligible


async def fetch_watchlist_news(
    *,
    limit: int = 50,
    symbol: str | None = None,
) -> list[dict[str, Any]]:
    """Recent headlines across watchlist symbols (or one symbol), newest-first.

    Items carry ``{headline, source, datetime, url, summary, image, symbol}``
    — ``symbol`` is the first watchlist symbol the story mentions (or the
    requested one). Deduped by story id in the store.
    """
    from db.news import recent_news

    if symbol:
        sym = symbol.strip().upper()
        if " " in sym or sym.startswith("/"):
            return []
        items = await afetch_company_news(sym, days=7, limit=min(limit, 15))
        return [{**item, "symbol": sym} for item in items]

    eligible = _watchlist_symbols()
    wanted = sorted(eligible)
    if not wanted:
        return []
    rows = await asyncio.to_thread(recent_news, symbols=wanted, limit=limit)
    if not rows:
        await _rest_topup(wanted, limit)
        rows = await asyncio.to_thread(recent_news, symbols=wanted, limit=limit)

    out = []
    for row in rows:
        tag = next((s for s in row.get("symbols", []) if s in eligible), None)
        out.append({**_ui_item(row), "symbol": tag})
    return out
