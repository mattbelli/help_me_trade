"""Symbol extraction with stopword filtering."""

from __future__ import annotations

import re

from config.settings import get_watchlist

_SYMBOL_RE = re.compile(r"\b([A-Z]{1,5})\b")
_DOLLAR_SYMBOL_RE = re.compile(r"\$([A-Z]{1,5})\b")
_FOR_SYMBOL_RE = re.compile(r"\bFOR\s+([A-Z]{2,5})\b")

# Common English + finance false positives (not tickers)
_SYMBOL_STOPWORDS = frozenset(
    {
        "A",
        "I",
        "AI",
        "AM",
        "AN",
        "AND",
        "ARE",
        "AS",
        "AT",
        "BE",
        "BY",
        "CEO",
        "CFO",
        "CO",
        "DO",
        "ETF",
        "FOR",
        "FROM",
        "GDP",
        "GO",
        "HE",
        "IF",
        "IN",
        "IPO",
        "IS",
        "IT",
        "ITS",
        "MARKETS",
        "ME",
        "MY",
        "NO",
        "NOT",
        "OF",
        "ON",
        "OR",
        "OUR",
        "OUT",
        "P",
        "P/L",
        "PNL",
        "SAID",
        "SO",
        "THAT",
        "THE",
        "THIS",
        "TO",
        "UP",
        "US",
        "USA",
        "VS",
        "WE",
        "WHAT",
        "WHEN",
        "WITH",
        "YO",
        "YOU",
    }
)


def _is_valid_symbol(sym: str, watchlist: set[str] | None) -> bool:
    if sym in _SYMBOL_STOPWORDS:
        return False
    if len(sym) < 2 and sym not in (watchlist or set()):
        return False
    if watchlist is not None and sym not in watchlist:
        return False
    return True


def extract_symbol(
    text: str,
    *,
    validate_against_watchlist: bool = False,
) -> str | None:
    """Extract a likely ticker from free text."""
    if not text.strip():
        return None

    upper = text.upper()
    watchlist = set(get_watchlist()) if validate_against_watchlist else None

    dollar = _DOLLAR_SYMBOL_RE.search(upper)
    if dollar:
        sym = dollar.group(1)
        if _is_valid_symbol(sym, watchlist):
            return sym

    for_match = _FOR_SYMBOL_RE.search(upper)
    if for_match:
        sym = for_match.group(1)
        if _is_valid_symbol(sym, watchlist):
            return sym

    candidates: list[str] = []
    for match in _SYMBOL_RE.finditer(upper):
        sym = match.group(1)
        if not _is_valid_symbol(sym, watchlist):
            continue
        if sym not in candidates:
            candidates.append(sym)

    return candidates[-1] if candidates else None
