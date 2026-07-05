"""Social mention accumulator — Reddit JSON + StockTwits trending (phase 1).

Gentle background sampling into ``social_mentions`` SQLite buckets. No LLM, no
scan integration. Polite client: distinctive User-Agent, 30-min cadence, bounded
requests (4 subreddit + 1 StockTwits per cycle), fail-soft on errors/429.
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections import deque
from pathlib import Path
from typing import Any

import httpx

from config.settings import get_watchlists_state
from db.base import DEFAULT_DB_PATH
from db.social_mentions import et_hour_bucket, record_mentions
from signals.strategy_universe import resolve_universe_spec

logger = logging.getLogger(__name__)

_USER_AGENT = "help_me_trade/1.0 mention-baseline"
_HTTP_TIMEOUT = 10.0
_LOOP_INTERVAL_SECONDS = 30 * 60
_DEDUPE_MAX = 500

_REDDIT_SUBS = ("wallstreetbets", "stocks", "investing", "StockMarket")
_STOCKTWITS_TRENDING_URL = "https://api.stocktwits.com/api/2/streams/trending.json"

_CASHTAG_RE = re.compile(r"\$([A-Z]{1,5})\b")
_BARE_TICKER_RE = re.compile(r"\b([A-Z]{1,5})\b")

# Common English words / abbreviations that happen to be valid tickers. Cashtags
# ($ALL, $IT) still count; bare words here are ignored even if in the known set.
_BARE_WORD_BLACKLIST = frozenset(
    {
        "A",
        "AI",
        "ALL",
        "AM",
        "AN",
        "ARE",
        "AS",
        "AT",
        "BE",
        "BY",
        "CAN",
        "CEO",
        "DD",
        "DO",
        "EDIT",
        "EOD",
        "EPS",
        "ETF",
        "FDA",
        "FOR",
        "GO",
        "IF",
        "IMO",
        "IN",
        "IPO",
        "IS",
        "IT",
        "ME",
        "MY",
        "NO",
        "OF",
        "OK",
        "ON",
        "OR",
        "POST",
        "PT",
        "RH",
        "SEC",
        "SO",
        "TO",
        "TV",
        "UK",
        "UP",
        "US",
        "WE",
        "YOLO",
    }
)


class _BoundedIdSet:
    """Remember the last N ids for cross-cycle dedupe."""

    def __init__(self, maxlen: int = _DEDUPE_MAX) -> None:
        self._maxlen = maxlen
        self._order: deque[str] = deque()
        self._seen: set[str] = set()

    def consume(self, item_id: str) -> bool:
        """Return True when ``item_id`` is newly seen (should count)."""
        if not item_id or item_id in self._seen:
            return False
        self._order.append(item_id)
        self._seen.add(item_id)
        while len(self._order) > self._maxlen:
            old = self._order.popleft()
            self._seen.discard(old)
        return True

    def clear(self) -> None:
        self._order.clear()
        self._seen.clear()


_reddit_seen = _BoundedIdSet()
_stocktwits_seen = _BoundedIdSet()


def reset_dedupe_state_for_tests() -> None:
    """Clear in-memory dedupe sets (tests only)."""
    _reddit_seen.clear()
    _stocktwits_seen.clear()


def _all_watchlist_symbols() -> set[str]:
    symbols: set[str] = set()
    state = get_watchlists_state()
    for lst in state.get("lists", []):
        if not isinstance(lst, dict):
            continue
        for raw in lst.get("symbols", []):
            if isinstance(raw, str) and raw.strip():
                sym = raw.strip().upper()
                if " " not in sym and not sym.startswith("/"):
                    symbols.add(sym)
    return symbols


def known_symbol_set() -> set[str]:
    """Watchlist symbols ∪ S&P 500 universe (cached per call site)."""
    return _all_watchlist_symbols() | set(resolve_universe_spec("sp500"))


def extract_symbols(text: str, *, known: set[str] | None = None) -> set[str]:
    """Extract ticker symbols from free text."""
    if not text:
        return set()
    known = known if known is not None else known_symbol_set()
    found: set[str] = set()
    for match in _CASHTAG_RE.finditer(text):
        found.add(match.group(1))
    for match in _BARE_TICKER_RE.finditer(text):
        word = match.group(1)
        if word in _BARE_WORD_BLACKLIST:
            continue
        if word in known:
            found.add(word)
    return found


def _http_headers() -> dict[str, str]:
    return {"User-Agent": _USER_AGENT}


def fetch_reddit_mentions() -> dict[str, int]:
    """Fetch new Reddit posts from finance subs; return symbol→count."""
    counts: dict[str, int] = {}
    for sub in _REDDIT_SUBS:
        url = f"https://www.reddit.com/r/{sub}/new.json?limit=100"
        try:
            resp = httpx.get(url, headers=_http_headers(), timeout=_HTTP_TIMEOUT)
            if resp.status_code == 429:
                logger.warning("Reddit rate-limited (429) on r/%s — skipping Reddit", sub)
                return {}
            resp.raise_for_status()
            payload = resp.json()
        except httpx.HTTPError:
            logger.warning("Reddit fetch failed for r/%s", sub, exc_info=True)
            continue
        except (ValueError, TypeError):
            logger.warning("Reddit JSON parse failed for r/%s", sub, exc_info=True)
            continue

        children = (
            payload.get("data", {}).get("children")
            if isinstance(payload, dict)
            else None
        )
        for child in children or []:
            if not isinstance(child, dict):
                continue
            post = child.get("data")
            if not isinstance(post, dict):
                continue
            post_id = str(post.get("id") or "").strip()
            if not post_id or not _reddit_seen.consume(post_id):
                continue
            title = str(post.get("title") or "")
            selftext = str(post.get("selftext") or "")
            text = f"{title}\n{selftext}"
            for sym in extract_symbols(text):
                counts[sym] = counts.get(sym, 0) + 1
    return counts


def fetch_stocktwits_trending() -> dict[str, int]:
    """Fetch StockTwits trending stream; one mention per message per symbol."""
    try:
        resp = httpx.get(
            _STOCKTWITS_TRENDING_URL,
            headers=_http_headers(),
            timeout=_HTTP_TIMEOUT,
        )
        if resp.status_code == 429:
            logger.warning("StockTwits rate-limited (429) — skipping StockTwits")
            return {}
        resp.raise_for_status()
        payload = resp.json()
    except httpx.HTTPError:
        logger.warning("StockTwits fetch failed", exc_info=True)
        return {}
    except (ValueError, TypeError):
        logger.warning("StockTwits JSON parse failed", exc_info=True)
        return {}

    messages = payload.get("messages") if isinstance(payload, dict) else None
    counts: dict[str, int] = {}
    for message in messages or []:
        if not isinstance(message, dict):
            continue
        msg_id = str(message.get("id") or "").strip()
        if not msg_id or not _stocktwits_seen.consume(msg_id):
            continue
        symbols = message.get("symbols")
        if not isinstance(symbols, list):
            continue
        seen_in_message: set[str] = set()
        for entry in symbols:
            if not isinstance(entry, dict):
                continue
            sym = str(entry.get("symbol") or "").strip().upper()
            if not sym or sym in seen_in_message:
                continue
            seen_in_message.add(sym)
            counts[sym] = counts.get(sym, 0) + 1
    return counts


def _merge_source_counts(
    agg: dict[str, dict[str, int]],
    source: str,
    source_counts: dict[str, int],
) -> None:
    for sym, n in source_counts.items():
        if n <= 0:
            continue
        by_source = agg.setdefault(sym, {})
        by_source[source] = by_source.get(source, 0) + n


def run_accumulator_cycle(*, db_path: Path = DEFAULT_DB_PATH) -> dict[str, Any]:
    """Run one sampling pass: fetch, dedupe, aggregate, persist."""
    reddit_counts: dict[str, int] = {}
    stocktwits_counts: dict[str, int] = {}
    try:
        reddit_counts = fetch_reddit_mentions()
    except Exception:
        logger.exception("Reddit mention fetch failed")
    try:
        stocktwits_counts = fetch_stocktwits_trending()
    except Exception:
        logger.exception("StockTwits mention fetch failed")

    aggregated: dict[str, dict[str, int]] = {}
    _merge_source_counts(aggregated, "reddit", reddit_counts)
    _merge_source_counts(aggregated, "stocktwits", stocktwits_counts)

    bucket = et_hour_bucket()
    rows = record_mentions(aggregated, bucket=bucket, db_path=db_path)

    return {
        "bucket": bucket,
        "symbols": len(aggregated),
        "rows_touched": rows,
        "reddit_symbols": len(reddit_counts),
        "stocktwits_symbols": len(stocktwits_counts),
        "reddit_mentions": sum(reddit_counts.values()),
        "stocktwits_mentions": sum(stocktwits_counts.values()),
    }


async def social_buzz_loop() -> None:
    """Background loop — samples every 30 minutes, never raises."""
    try:
        from api.jobs import mark_alive
    except ImportError:
        mark_alive = None  # type: ignore[assignment,misc]

    try:
        await asyncio.to_thread(run_accumulator_cycle)
        if mark_alive is not None:
            mark_alive("social_buzz")
    except Exception:
        logger.exception("Initial social buzz cycle failed")

    while True:
        await asyncio.sleep(_LOOP_INTERVAL_SECONDS)
        try:
            summary = await asyncio.to_thread(run_accumulator_cycle)
            if summary.get("symbols") or summary.get("rows_touched"):
                logger.info("Social buzz cycle: %s", summary)
            if mark_alive is not None:
                mark_alive("social_buzz")
        except Exception:
            logger.exception("Periodic social buzz cycle failed")
