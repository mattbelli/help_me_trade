"""Social mention accumulator — Reddit RSS + StockTwits trending (phase 1).

Gentle background sampling into ``social_posts`` + ``social_mentions`` SQLite
buckets. No LLM, no scan integration. Polite client: distinctive User-Agent,
30-min cadence, bounded requests (1 round-robin subreddit + 1 StockTwits per
cycle), fail-soft on errors/429.
"""

from __future__ import annotations

import asyncio
import html
import logging
import re
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx

from config.settings import get_watchlists_state
from db.base import DEFAULT_DB_PATH
from db.social_mentions import (
    et_hour_bucket,
    record_mentions,
    record_reddit_posts,
)
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
_HTML_TAG_RE = re.compile(r"<[^>]+>")
_URL_RE = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)
_REDDIT_URL_RE = re.compile(r"https?://(?:www\.)?reddit\.com/", re.IGNORECASE)
_DD_FLAG_RE = re.compile(r"(?i)(?:\[dd\]|\bdd\b)")
_ATOM_NS = "{http://www.w3.org/2005/Atom}"

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
        "BULL",
        "BY",
        "C",
        "CAN",
        "CEO",
        "D",
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
        "MEME",
        "MY",
        "NO",
        "OF",
        "OK",
        "ON",
        "OR",
        "PEG",
        "POST",
        "PT",
        "Q",
        "RH",
        "SEC",
        "SI",
        "SO",
        "T",
        "TECH",
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
    """Clear in-memory dedupe sets + the sub round-robin cursor (tests only)."""
    global _reddit_sub_index
    _reddit_sub_index = 0
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


def _parse_published_at(raw: str) -> datetime | None:
    text = (raw or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def _has_external_url(*parts: str) -> int:
    blob = "\n".join(p for p in parts if p)
    for match in _URL_RE.finditer(blob):
        if not _REDDIT_URL_RE.match(match.group(0)):
            return 1
    return 0


def _word_count(*parts: str) -> int:
    blob = " ".join(p for p in parts if p).strip()
    if not blob:
        return 0
    return len(blob.split())


def _dd_flag(title: str) -> int:
    return 1 if _DD_FLAG_RE.search(title or "") else 0


def _enrich_reddit_post(
    parsed: dict[str, str],
    *,
    subreddit: str,
    known: set[str] | None = None,
) -> dict[str, Any]:
    """Stamp extract/flags/bucket onto a parsed Atom entry."""
    title = parsed.get("title") or ""
    text = parsed.get("text") or ""
    published_raw = parsed.get("published_at") or ""
    published_dt = _parse_published_at(published_raw)
    bucket = et_hour_bucket(published_dt) if published_dt is not None else et_hour_bucket()
    symbols = extract_symbols(text, known=known)
    return {
        "post_id": parsed["id"],
        "id": parsed["id"],
        "source": "reddit",
        "subreddit": subreddit,
        "author": parsed.get("author") or None,
        "published_at": published_raw
        or (published_dt.isoformat() if published_dt else ""),
        "url": parsed.get("url") or None,
        "title": title,
        "text": text,
        "symbols": symbols,
        "n_symbols": len(symbols),
        "has_url": _has_external_url(title, text),
        "word_count": _word_count(text),
        "dd_flag": _dd_flag(title),
        "bucket": bucket,
    }


def _parse_reddit_atom(xml_text: str) -> list[dict[str, str]]:
    """Parse a Reddit .rss (Atom) feed into post dicts — pure, testable."""
    import xml.etree.ElementTree as ET

    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []
    out: list[dict[str, str]] = []
    for entry in root.findall(f"{_ATOM_NS}entry"):
        entry_id = (entry.findtext(f"{_ATOM_NS}id") or "").strip()
        if not entry_id:
            continue
        title = entry.findtext(f"{_ATOM_NS}title") or ""
        content = entry.findtext(f"{_ATOM_NS}content") or ""
        text = html.unescape(_HTML_TAG_RE.sub(" ", f"{title}\n{content}"))
        author_el = entry.find(f"{_ATOM_NS}author")
        author = ""
        if author_el is not None:
            author = (author_el.findtext(f"{_ATOM_NS}name") or "").strip()
        published_at = (entry.findtext(f"{_ATOM_NS}published") or "").strip()
        link_el = entry.find(f"{_ATOM_NS}link")
        url = ""
        if link_el is not None:
            url = (link_el.get("href") or "").strip()
        out.append(
            {
                "id": entry_id,
                "author": author,
                "published_at": published_at,
                "url": url,
                "title": title,
                "text": text,
            }
        )
    return out


# Round-robin cursor: ONE subreddit per cycle. Even 2.5s-spaced multi-sub
# bursts tripped Reddit's anonymous rate limit (429 on request #2, observed
# live), and a mid-burst 429 was discarding the counts already fetched. Each
# sub gets sampled every 4 cycles (~2h at the 30-min cadence) — for BASELINES,
# consistent sampling matters more than per-cycle completeness.
_reddit_sub_index = 0


def fetch_reddit_posts() -> list[dict[str, Any]]:
    """Fetch new posts from ONE finance sub (round-robin); return enriched posts.

    Uses the .rss (Atom) endpoints — Reddit 403-blocks unauthenticated .json
    and has gated self-serve OAuth app creation, but the RSS feeds serve fine.
    In-memory ``_reddit_seen`` is a hot cache; durable dedupe is the DB PK.
    """
    global _reddit_sub_index
    sub = _REDDIT_SUBS[_reddit_sub_index % len(_REDDIT_SUBS)]
    _reddit_sub_index += 1

    url = f"https://www.reddit.com/r/{sub}/new.rss?limit=100"
    try:
        resp = httpx.get(url, headers=_http_headers(), timeout=_HTTP_TIMEOUT)
        if resp.status_code in (403, 429):
            logger.warning(
                "Reddit blocked/rate-limited (%s) on r/%s — skipping this cycle",
                resp.status_code,
                sub,
            )
            return []
        resp.raise_for_status()
    except httpx.HTTPError:
        logger.warning("Reddit fetch failed for r/%s", sub, exc_info=True)
        return []

    known = known_symbol_set()
    posts: list[dict[str, Any]] = []
    for parsed in _parse_reddit_atom(resp.text):
        entry_id = parsed["id"]
        if not _reddit_seen.consume(entry_id):
            continue
        posts.append(_enrich_reddit_post(parsed, subreddit=sub, known=known))
    return posts


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


def _rollup_reddit_mentions(
    newly: list[dict[str, Any]],
    *,
    db_path: Path,
) -> tuple[int, dict[str, int]]:
    """Increment hourly reddit mentions for newly inserted posts by published bucket."""
    by_bucket: dict[str, dict[str, dict[str, int]]] = {}
    symbol_totals: dict[str, int] = {}
    for post in newly:
        bucket = str(post.get("bucket") or et_hour_bucket())
        symbols = post.get("symbols") or []
        for sym in symbols:
            s = str(sym).strip().upper()
            if not s:
                continue
            by_source = by_bucket.setdefault(bucket, {}).setdefault(s, {})
            by_source["reddit"] = by_source.get("reddit", 0) + 1
            symbol_totals[s] = symbol_totals.get(s, 0) + 1
    rows = 0
    for bucket, counts in by_bucket.items():
        rows += record_mentions(counts, bucket=bucket, db_path=db_path)
    return rows, symbol_totals


def run_accumulator_cycle(*, db_path: Path = DEFAULT_DB_PATH) -> dict[str, Any]:
    """Run one sampling pass: fetch, persist posts, roll up mentions."""
    reddit_posts: list[dict[str, Any]] = []
    stocktwits_counts: dict[str, int] = {}
    try:
        reddit_posts = fetch_reddit_posts()
    except Exception:
        logger.exception("Reddit mention fetch failed")
    try:
        stocktwits_counts = fetch_stocktwits_trending()
    except Exception:
        logger.exception("StockTwits mention fetch failed")

    newly = record_reddit_posts(reddit_posts, db_path=db_path)
    reddit_rows, reddit_counts = _rollup_reddit_mentions(newly, db_path=db_path)

    fetch_bucket = et_hour_bucket()
    st_aggregated: dict[str, dict[str, int]] = {
        sym: {"stocktwits": n} for sym, n in stocktwits_counts.items() if n > 0
    }
    st_rows = record_mentions(st_aggregated, bucket=fetch_bucket, db_path=db_path)

    all_symbols = set(reddit_counts) | set(stocktwits_counts)
    return {
        "bucket": fetch_bucket,
        "symbols": len(all_symbols),
        "rows_touched": reddit_rows + st_rows,
        "reddit_posts_new": len(newly),
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
