"""Extract durable memories from chat turns (standard mode only) — design §4.1.

LLM-or-nothing: no regex fallback. Candidates flow through the judge (§4.2), which
decides ADD/UPDATE/SUPERSEDE/NOOP. Most turns yield zero candidates — that is the
expected, correct output.
"""

from __future__ import annotations

import logging
from typing import Any

from ai.chat.alert_intent import looks_like_alert_intent
from ai.chat.review_intent import looks_like_review_intent
from ai.llm.base import LLMMessage
from ai.llm.factory import get_llm
from ai.llm.structured import complete_for_extraction, parse_json_object
from ai.memories import get_memory_service
from ai.memories.judge import Candidate, judge_and_store

logger = logging.getLogger(__name__)

_EXTRACT_TYPES = frozenset({"directive", "preference", "fact", "lesson"})

_SYSTEM = (
    "You extract durable memories for a trading copilot's long-term store. Most "
    "turns contain NOTHING worth remembering — an empty list is the expected, "
    "correct output.\n"
    "A candidate memory must be ALL of: stated by the USER (never the assistant, "
    "never a restatement of the Already-known list); durable (still true and useful "
    "in 3 months); about the user's rules, tastes, situation, or a hard-won lesson "
    "— NOT a question, NOT a one-off request/action (creating an alert, rule, or "
    "order is an action), NOT market commentary, NOT a risk/config setting (those "
    "live in Settings).\n"
    "Types:\n"
    "- directive: a standing order to you, imperative (never/always ...).\n"
    "- preference: a soft taste or style.\n"
    "- fact: a durable fact about the user's accounts/situation/workflow.\n"
    "- lesson: a behavioral lesson the user stated about their own trading.\n"
    "Rules: write content in the third person, <= 200 chars, keep the user's "
    "numbers VERBATIM (20% stays 20%). Also return \"quote\": the user's exact words "
    "the memory came from (a substring of the user message). Return \"symbols\": "
    "tickers the memory is about, [] if none. Max 2 candidates per turn.\n"
    'Return JSON only: {"memories": [{"type": "...", "content": "...", '
    '"quote": "...", "symbols": ["..."]}]}'
)


def _parse_llm_memories(raw: str) -> list[dict[str, Any]]:
    parsed = parse_json_object(raw, prefer_keys={"memories"})
    if not parsed:
        return []
    memories = parsed.get("memories", [])
    if not isinstance(memories, list):
        return []
    out: list[dict[str, Any]] = []
    for item in memories:
        if not isinstance(item, dict):
            continue
        mtype = str(item.get("type", "")).strip()
        content = str(item.get("content", "")).strip()
        if mtype not in _EXTRACT_TYPES or not content:
            continue
        quote_raw = item.get("quote")
        quote = str(quote_raw).strip() if quote_raw else None
        symbols_raw = item.get("symbols")
        symbols = (
            [str(s).strip().upper() for s in symbols_raw if str(s).strip()]
            if isinstance(symbols_raw, list)
            else []
        )
        out.append(
            {"type": mtype, "content": content, "quote": quote, "symbols": symbols}
        )
    return out[:2]


def _already_known_block() -> str:
    """The anti-echo negative-example list: recently-touched active memories the
    extractor must NOT re-extract (the structural fix for the self-reinforcing
    loop — §4.1)."""
    try:
        recent = get_memory_service().list(limit=10)
    except Exception:  # noqa: BLE001 — never let context-building break extraction
        recent = []
    if not recent:
        return "Already known (do NOT re-extract, even reworded): (none)"
    lines = [f"- [{m['id']}] {m['content']}" for m in recent]
    return "Already known (do NOT re-extract, even reworded):\n" + "\n".join(lines)


def extract_memories_from_turn(
    *,
    user_text: str,
    assistant_text: str,
    session_id: str,
    model: str | None = None,
    source_message_id: str | None = None,
) -> list[dict[str, Any]]:
    """Return memory records created from a chat turn (post-judge)."""
    if not user_text.strip() or not assistant_text.strip():
        return []

    # A rule is not a memory. If this turn created an alert/signal rule, the rule
    # itself is the durable, executable record — don't ALSO store it as a memory.
    if looks_like_alert_intent(user_text):
        return []
    # Review/analysis turns synthesize narrative from analytics — never seed
    # memories from generated reviews (avoids learning fabricated preferences).
    if looks_like_review_intent(user_text):
        return []

    llm = get_llm(model=model)
    messages = [
        LLMMessage(role="system", content=_SYSTEM),
        LLMMessage(
            role="user",
            content=(
                f"{_already_known_block()}\n\n"
                f"User:\n{user_text}\n\nAssistant:\n{assistant_text}"
            ),
        ),
    ]

    try:
        raw = complete_for_extraction(llm, messages, max_tokens=400)
        candidates = _parse_llm_memories(raw)
    except Exception as exc:  # noqa: BLE001 — LLM-or-nothing, fail-soft
        logger.warning("LLM memory extraction failed: %s", exc)
        return []

    if not candidates:
        logger.info("LLM memory extraction returned no candidates")
        return []

    metadata: dict[str, Any] = {"source": "chat", "session_id": session_id}
    if source_message_id:
        metadata["source_message_id"] = source_message_id

    batch = [
        Candidate(
            memory_type=item["type"],
            content=item["content"],
            quote=item["quote"],
            symbols=item["symbols"],
            source="chat_extract",
            authority="agent_inferred",
            metadata=metadata,
        )
        for item in candidates
    ]
    return judge_and_store(batch)
