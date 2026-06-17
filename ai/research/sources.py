"""Data sources for deep research: portfolio, bars, memories, web."""

from __future__ import annotations

import logging
import re
from typing import Any

import httpx

from ai.memories.retrieval import retrieve_relevant_memories
from ai.utils.symbols import extract_symbol
from brokers.errors import BrokerError
from brokers.factory import get_broker
from config.research import get_searxng_url
from config.settings import broker_is_configured
from db.sqlite import get_latest_snapshot

logger = logging.getLogger(__name__)

_SOURCE_COUNTER = 0
_MOCK_REASON = (
    "SearXNG is not configured. These are placeholder snippets — not real web results. "
    "Set SEARXNG_URL in .env or Settings → Research."
)

WEB_SEARCH_LIMITATIONS = (
    "Web search uses SearXNG (public web index). It does NOT have live X/Twitter or "
    "Reddit API access, cannot read private posts, and cannot filter to a clock-time "
    "window (e.g. 6–8am). time_range only supports day/week/month/year granularity "
    "when the underlying search engines support it. For Reddit/X, use sites like "
    "reddit.com or x.com in the query — you still get indexed pages/snippets, not "
    "real-time sentiment scores."
)


def web_search_capabilities() -> dict[str, Any]:
    status = web_search_status()
    return {
        **status,
        "limitations": WEB_SEARCH_LIMITATIONS,
        "supports_time_range": ["day", "week", "month", "year"],
        "supports_site_scoping": True,
    }


def _next_source_id(prefix: str = "src") -> str:
    global _SOURCE_COUNTER
    _SOURCE_COUNTER += 1
    return f"{prefix}_{_SOURCE_COUNTER}"


def web_search_status() -> dict[str, Any]:
    url = get_searxng_url()
    if url:
        return {
            "mode": "searxng",
            "searxng_url": url,
            "warning": None,
            "uses_mock_web": False,
        }
    return {
        "mode": "mock",
        "searxng_url": "",
        "warning": _MOCK_REASON,
        "uses_mock_web": True,
    }


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


def normalize_research_symbol(symbol: str | None) -> str | None:
    """Single ticker for bars/API — strips commas, spaces, and junk from the UI field."""
    if not symbol:
        return None
    raw = str(symbol).strip().upper()
    if not raw:
        return None
    if "," in raw:
        raw = raw.split(",", 1)[0].strip()
    raw = raw.split()[0] if raw.split() else raw
    return raw or None


def fetch_bars_source(symbol: str, *, days: int = 90) -> dict[str, Any] | None:
    sym = normalize_research_symbol(symbol)
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
    results = retrieve_relevant_memories(query, limit=limit)
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


def _build_web_query(query: str, sites: list[str] | None = None) -> str:
    text = query.strip()
    if not sites:
        return text
    cleaned = []
    for raw in sites[:4]:
        site = str(raw).strip().lower().removeprefix("https://").removeprefix("http://")
        site = site.split("/")[0]
        if site:
            cleaned.append(f"site:{site}")
    if not cleaned:
        return text
    clause = " OR ".join(cleaned)
    return f"({clause}) {text}"


def web_search(
    query: str,
    *,
    limit: int = 5,
    time_range: str | None = None,
    sites: list[str] | None = None,
    allow_mock: bool = True,
) -> list[dict[str, Any]]:
    resolved_query = _build_web_query(query, sites)
    searxng = get_searxng_url()
    if searxng:
        results = _searxng_search(
            searxng,
            resolved_query,
            limit=limit,
            time_range=time_range,
        )
        if results:
            return results
        logger.warning("SearXNG returned no results for query: %s", resolved_query[:80])
        return [
            {
                "id": _next_source_id("web"),
                "type": "web",
                "title": "Web search returned no results",
                "url": None,
                "snippet": (
                    f"SearXNG at {searxng} did not return results for this query. "
                    "Try a different query or check your SearXNG instance."
                ),
                "content": "",
                "search_failed": True,
            }
        ]
    if not allow_mock:
        return []
    return _mock_web_search(query, limit=limit)


def _mock_web_search(query: str, *, limit: int) -> list[dict[str, Any]]:
    sym = extract_symbol(query)
    label = sym or query[:40]
    templates = [
        (
            f"{label} — market context (MOCK)",
            f"[PLACEHOLDER] Recent trading activity and sentiment around {label}. "
            "Volume has been elevated versus the 20-day average.",
        ),
        (
            f"{label} — sector overview (MOCK)",
            f"[PLACEHOLDER] Sector peers and macro drivers affecting {label} this week.",
        ),
        (
            f"{label} — risk factors (MOCK)",
            f"[PLACEHOLDER] Key downside risks including earnings, rates, and positioning in {label}.",
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
                "is_mock": True,
                "mock_reason": _MOCK_REASON,
            }
        )
    return out


def _searxng_search(
    base_url: str,
    query: str,
    *,
    limit: int,
    time_range: str | None = None,
) -> list[dict[str, Any]]:
    params: dict[str, str] = {"q": query, "format": "json", "categories": "general"}
    if time_range in ("day", "week", "month", "year"):
        params["time_range"] = time_range
    try:
        with httpx.Client(timeout=20.0) as client:
            resp = client.get(
                f"{base_url}/search",
                params=params,
            )
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:
        logger.warning("SearXNG search failed: %s", exc)
        return []

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
                "is_mock": False,
                "published": item.get("published"),
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


def summarize_web_sources(sources: list[dict[str, Any]]) -> dict[str, Any]:
    status = web_search_status()
    mock_count = sum(1 for s in sources if s.get("is_mock"))
    failed_count = sum(1 for s in sources if s.get("search_failed"))
    real_web = sum(
        1
        for s in sources
        if s.get("type") == "web" and not s.get("is_mock") and not s.get("search_failed")
    )
    warning = status.get("warning")
    if mock_count:
        warning = _MOCK_REASON
    elif failed_count and not real_web:
        warning = "Web search did not return usable results for this run."
    return {
        **status,
        "mock_source_count": mock_count,
        "real_web_source_count": real_web,
        "warning": warning,
    }


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
    sym = normalize_research_symbol(symbol) or extract_symbol(query)
    if sym:
        bars = fetch_bars_source(sym)
        if bars:
            sources.append(bars)
    sources.extend(fetch_memory_sources(query))
    if include_web:
        for hit in web_search(query, limit=3):
            if hit.get("url") and not hit.get("is_mock"):
                body = fetch_url(str(hit["url"]))
                if body:
                    hit = {**hit, "content": body}
            sources.append(hit)
    return sources
