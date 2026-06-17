"""Detect user intent for chat tool auto-enable and prompt supplements."""

from __future__ import annotations

import re

_ORDER_INTENT = re.compile(
    r"\b(buy|sell|purchase|short)\b[\s\S]{0,80}\b(shares?|stock|stocks|position)\b",
    re.IGNORECASE,
)
_ORDER_INTENT_TICKER = re.compile(
    r"\b(buy|sell|purchase|short)\b[\s\S]{0,40}\b[A-Za-z]{1,5}\b",
    re.IGNORECASE,
)
_ORDER_INTENT_EXPLICIT = re.compile(
    r"\b(propose|place|submit)\s+(an?\s+)?(paper\s+)?order\b",
    re.IGNORECASE,
)
_ORDER_FOR_SHARES = re.compile(
    r"\b(?:place\s+(?:an?\s+)?order|order)\s+for\s+\d+\s+[A-Za-z]{1,5}\s+shares?\b",
    re.IGNORECASE,
)


def looks_like_order_intent(user_text: str) -> bool:
    text = (user_text or "").strip()
    if not text:
        return False
    return bool(
        _ORDER_INTENT.search(text)
        or _ORDER_INTENT_TICKER.search(text)
        or _ORDER_INTENT_EXPLICIT.search(text)
        or _ORDER_FOR_SHARES.search(text)
    )


def order_intent_prompt_block(user_text: str) -> str:
    if not looks_like_order_intent(user_text):
        return ""
    return (
        "\n\nORDER REQUEST (this turn):\n"
        "- Call propose_order immediately with symbol, side, qty, and order_type "
        "(market or limit only).\n"
        "- Do NOT narrate the tool or write fake calls like "
        "[Calling propose_order...] or \"Let me propose the order now\".\n"
        "- The approval card is the UI — minimal text before the tool call.\n"
        "- market-on-open / MOO / at-open are not supported; use order_type=market "
        "and mention the MOO limitation in one sentence only after the tool runs.\n"
    )
