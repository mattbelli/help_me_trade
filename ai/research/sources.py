"""Data sources for deep research: portfolio, bars, memories, web."""

from __future__ import annotations

import logging
import re
import uuid
from typing import Any

import httpx

from config.research import get_searxng_url

from ai.memories import get_memory_service
from ai.utils.symbols import extract_symbol
from brokers.errors import BrokerError
from brokers.factory import get_broker
from config.settings import broker_is_configured
from db.sqlite import get_latest_snapshot

logger = logging.getLogger(__name__)

_SOURCE_COUNTER = 0


def _next_source_id(prefix: str = "src") -> str:
    global _SOURCE_COUNTER
    _SOURCE_COUNTER += 1
    return f"{prefix}_{_SOURCE_COUNTER}"


def fetch_portfolio_source() -> dict[str, Any] | None:
    snapshot = get_latest_snapshot()
    if not snapshot:
        return None
    return {
        "id": _next_source_id("portfolio"),
        "type": "portfolio",
        "title": "Portfolio snapshot",
        "url": None,
        "snippet": (
            f"{snapshot.get('position_count', 0)} positions, "
            f"portfolio ${snapshot.get('account', {}).get('portfolio_value', 0):,.0f}"
        ),
        "content": snapshot,
    }


def fetch_bars_source(symbol: str, *, days: int = 90) -> dict[str, Any] | None:
    sym = symbol.strip().upper()
    if not sym or not broker_is_configured("alpaca"):
        return None
    try:
        broker = get_broker("alpaca")
        bars = broker.get_stock_bars(sym, days=days)
    except (BrokerError, AttributeError, TypeError) as exc:
        logger.warning("Bars fetch failed for %s: %s", sym, exc)
        return None
    if not bars:
        return None
    closes = [b.close for b in bars[-30:]]
    change = ((closes[-1] - closes[0]) / closes[0] * 100) if len(closes) >= 2 else 0
    return {
        "id": _next_source_id("bars"),
        "type": "market_data",
        "title": f"{sym} daily bars ({days}d)",
        "url": None,
        "snippet": f"Latest close ${closes[-1]:.2f}, {change:+.1f}% over window",
        "content": {
            "symbol": sym,
            "days": days,
            "bars": [
                {
                    "time": b.time,
                    "open": b.open,
                    "high": b.high,
                    "low": b.low,
                    "close": b.close,
                    "volume": b.volume,
                }
                for b in bars[-60:]
            ],
        },
    }


def fetch_memory_sources(query: str, *, limit: int = 5) -> list[dict[str, Any]]:
    results = get_memory_service().search(query, limit=limit)
    out: list[dict[str, Any]] = []
    for item in results:
        out.append(
            {
                "id": _next_source_id("memory"),
                "type": "memory",
                "title": f"Memory ({item.get('type', 'note')})",
                "url": None,
                "snippet": str(item.get("content", ""))[:200],
                "content": item,
            }
        )
    return out


def web_search(query: str, *, limit: int = 5) -> list[dict[str, Any]]:
    searxng = get_searxng_url()
    if searxng:
        return _searxng_search(searxng, query, limit=limit)
    return _mock_web_search(query, limit=limit)


def _mock_web_search(query: str, *, limit: int) -> list[dict[str, Any]]:
    sym = extract_symbol(query)
    label = sym or query[:40]
    templates = [
        (
            f"{label} — market context",
            f"Recent trading activity and sentiment around {label}. "
            "Volume has been elevated versus the 20-day average.",
        ),
        (
            f"{label} — sector overview",
            f"Sector peers and macro drivers affecting {label} this week.",
        ),
        (
            f"{label} — risk factors",
            f"Key downside risks including earnings, rates, and positioning in {label}.",
        ),
    ]
    out: list[dict[str, Any]] = []
    for i, (title, snippet) in enumerate(templates[:limit]):
        out.append(
            {
                "id": _next_source_id("web"),
                "type": "web",
                "title": title,
                "url": f"https://research.local/mock/{i + 1}",
                "snippet": snippet,
                "content": snippet,
            }
        )
    return out


def _searxng_search(base_url: str, query: str, *, limit: int) -> list[dict[str, Any]]:
    try:
        with httpx.Client(timeout=20.0) as client:
            resp = client.get(
                f"{base_url}/search",
                params={"q": query, "format": "json", "categories": "general"},
            )
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:
        logger.warning("SearXNG search failed: %s", exc)
        return _mock_web_search(query, limit=limit)

    out: list[dict[str, Any]] = []
    for item in (data.get("results") or [])[:limit]:
        out.append(
            {
                "id": _next_source_id("web"),
                "type": "web",
                "title": str(item.get("title", "Web result")),
                "url": item.get("url"),
                "snippet": str(item.get("content", ""))[:400],
                "content": str(item.get("content", ""))[:2000],
            }
        )
    return out


def fetch_url(url: str) -> str:
    if not url or "research.local" in url:
        return ""
    try:
        with httpx.Client(timeout=15.0, follow_redirects=True) as client:
            resp = client.get(url, headers={"User-Agent": "HelpMeTrade-Research/1.0"})
            resp.raise_for_status()
            text = resp.text
    except Exception as exc:
        logger.warning("URL fetch failed %s: %s", url, exc)
        return ""
    text = re.sub(r"<script[\s\S]*?</script>", " ", text, flags=re.I)
    text = re.sub(r"<style[\s\S]*?</style>", " ", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:4000]


def collect_for_query(
    query: str,
    *,
    symbol: str | None,
    include_web: bool = True,
) -> list[dict[str, Any]]:
    sources: list[dict[str, Any]] = []
    portfolio = fetch_portfolio_source()
    if portfolio:
        sources.append(portfolio)
    sym = symbol or extract_symbol(query)
    if sym:
        bars = fetch_bars_source(sym)
        if bars:
            sources.append(bars)
    sources.extend(fetch_memory_sources(query))
    if include_web:
        for hit in web_search(query, limit=3):
            if hit.get("url"):
                body = fetch_url(str(hit["url"]))
                if body:
                    hit = {**hit, "content": body}
            sources.append(hit)
    return sources
