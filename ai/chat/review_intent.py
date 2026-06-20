"""Detect user intent for trade/performance review turns."""

from __future__ import annotations

import re

_REVIEW_PATTERNS = (
    re.compile(r"\btrade review\b", re.IGNORECASE),
    re.compile(r"\bperformance review\b", re.IGNORECASE),
    re.compile(r"\bhow am i doing\b", re.IGNORECASE),
    re.compile(
        r"\banalyze my (?:trades|performance|history|trading)\b",
        re.IGNORECASE,
    ),
    re.compile(r"\breview my (?:trades|performance|history|trading)\b", re.IGNORECASE),
    re.compile(r"\bhow (?:did|have) i (?:done|performed)\b", re.IGNORECASE),
)


def looks_like_review_intent(user_text: str) -> bool:
    text = (user_text or "").strip()
    if not text:
        return False
    return any(pattern.search(text) for pattern in _REVIEW_PATTERNS)
