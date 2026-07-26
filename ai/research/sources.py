"""Data sources for deep research: portfolio, bars, memories, web."""

from __future__ import annotations

import ipaddress
import itertools
import logging
import re
import socket
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx

from ai.memories.retrieval import retrieve_relevant_memories
from ai.utils.symbols import extract_symbol
from brokers.errors import BrokerError
from brokers.factory import get_broker
from config.research import get_searxng_url
from config.settings import broker_is_configured
from db.sqlite import get_latest_snapshot

logger = logging.getLogger(__name__)

_SOURCE_COUNTER = itertools.count(1)
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
    return f"{prefix}_{next(_SOURCE_COUNTER)}"


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
        results, health = _searxng_search(
            searxng,
            resolved_query,
            limit=limit,
            time_range=time_range,
        )
        if results:
            return results
        # An empty result set has three very different causes and the caller must
        # be able to tell them apart. Reporting "no results for this query" when
        # every engine is suspended is what left the copilot silently web-blind
        # (07-02, 07-14, 07-25 — the same shape three times).
        down = health["unresponsive_engines"]
        if not health["reachable"]:
            kind = "unreachable"
            snippet = f"SearXNG at {searxng} could not be reached."
        elif down:
            kind = "engines_down"
            snippet = (
                f"SearXNG at {searxng} answered, but every search engine failed: "
                f"{', '.join(down)}. This is an instance fault, not a bad query — "
                "results are NOT available."
            )
        else:
            kind = "no_results"
            snippet = (
                f"SearXNG at {searxng} ran the search and returned no matches. "
                "Try a different query."
            )
        logger.warning(
            "SearXNG %s for query: %s%s",
            kind,
            resolved_query[:80],
            f" (engines down: {', '.join(down)})" if down else "",
        )
        return [
            {
                "id": _next_source_id("web"),
                "type": "web",
                "title": "Web search returned no results",
                "url": None,
                "snippet": snippet,
                "content": "",
                "search_failed": True,
                "failure_kind": kind,
                "unresponsive_engines": down,
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
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Results plus instance health: ``{"reachable": bool, "unresponsive_engines": [...]}``.

    A SearXNG whose engines are all suspended still answers HTTP 200 with an
    empty ``results`` array — indistinguishable from a query with no matches
    unless ``unresponsive_engines`` is read, which is why the caller gets it.
    """
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
        return [], {"reachable": False, "unresponsive_engines": []}

    # Each entry is [engine, reason]; older builds emit bare strings.
    down = [
        str(e[0]) if isinstance(e, (list, tuple)) and e else str(e)
        for e in (data.get("unresponsive_engines") or [])
    ]
    health = {"reachable": True, "unresponsive_engines": sorted(down)}

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
    return out, health


MAX_REDIRECTS = 5
_REDIRECT_STATUS_CODES = frozenset({301, 302, 303, 307, 308})
_DEFAULT_PORTS = {"http": 80, "https": 443}


def _ip_is_public(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return not (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


def _is_fetch_url_safe(url: str) -> bool:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        return False
    host = parsed.hostname
    if not host:
        return False
    if host == "localhost" or host.lower().endswith(".localhost"):
        return False
    try:
        if not _ip_is_public(ipaddress.ip_address(host)):
            return False
    except ValueError:
        pass
    else:
        return True
    port = parsed.port or _DEFAULT_PORTS[parsed.scheme]
    try:
        addrinfo = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except OSError:
        return False
    for entry in addrinfo:
        ip_str = entry[4][0]
        try:
            ip = ipaddress.ip_address(ip_str)
        except ValueError:
            return False
        if not _ip_is_public(ip):
            return False
    return True


def fetch_url(url: str, *, max_chars: int = 4000) -> str:
    if not url or "research.local" in url:
        return ""
    if not _is_fetch_url_safe(url):
        logger.warning("Blocked unsafe URL fetch: %s", url)
        return ""
    try:
        current = url
        with httpx.Client(timeout=15.0, follow_redirects=False) as client:
            for _ in range(MAX_REDIRECTS + 1):
                resp = client.get(
                    current,
                    headers={"User-Agent": "HelpMeTrade-Research/1.0"},
                )
                if resp.status_code in _REDIRECT_STATUS_CODES:
                    location = resp.headers.get("Location")
                    if not location:
                        resp.raise_for_status()
                    current = urljoin(current, location)
                    if not _is_fetch_url_safe(current):
                        logger.warning("Blocked unsafe URL fetch: %s", current)
                        return ""
                    continue
                resp.raise_for_status()
                text = resp.text
                break
            else:
                logger.warning("Too many redirects fetching URL: %s", url)
                return ""
    except Exception as exc:
        logger.warning("URL fetch failed %s: %s", url, exc)
        return ""
    text = re.sub(r"<script[\s\S]*?</script>", " ", text, flags=re.I)
    text = re.sub(r"<style[\s\S]*?</style>", " ", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:max_chars]


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
    web_limit: int = 3,
    content_max_chars: int = 4000,
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
        for hit in web_search(query, limit=web_limit):
            if hit.get("url") and not hit.get("is_mock"):
                body = fetch_url(str(hit["url"]), max_chars=content_max_chars)
                if body:
                    hit = {**hit, "content": body}
            sources.append(hit)
    return sources
