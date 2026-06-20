"""Extract durable memories from chat turns (standard mode only)."""

from __future__ import annotations

import logging
import re
from typing import Any

from ai.chat.alert_intent import looks_like_alert_intent
from ai.chat.review_intent import looks_like_review_intent
from ai.llm.base import LLMMessage
from ai.llm.factory import get_llm
from ai.llm.structured import complete_for_extraction, parse_json_object
from ai.memories import get_memory_service
from db.ai_tables import _is_duplicate, _memory_tokens

logger = logging.getLogger(__name__)


def _heuristic_memories(user_text: str, assistant_text: str) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    lower = user_text.lower()

    pref_patterns = [
        r"i prefer (.+)",
        r"i like to (.+)",
        r"my risk (?:is|tolerance is) (.+)",
        r"i usually (.+)",
        r"remember that i (.+)",
    ]
    for pattern in pref_patterns:
        match = re.search(pattern, lower)
        if match:
            items.append(
                {
                    "type": "preference",
                    "content": f"User preference: {match.group(1).strip()}",
                }
            )
            break

    if any(word in lower for word in ("lesson", "learned", "mistake", "should have")):
        snippet = user_text.strip()[:300]
        if len(snippet) > 20:
            items.append({"type": "lesson", "content": snippet})

    if not items and len(assistant_text) > 80:
        if any(word in lower for word in ("always", "never", "rule", "strategy")):
            items.append(
                {
                    "type": "preference",
                    "content": f"From chat: {user_text.strip()[:200]}",
                }
            )

    return items[:2]


def _parse_llm_memories(raw: str) -> list[dict[str, str]]:
    parsed = parse_json_object(raw)
    if not parsed:
        return []
    memories = parsed.get("memories", [])
    if not isinstance(memories, list):
        return []
    out: list[dict[str, str]] = []
    for item in memories:
        if not isinstance(item, dict):
            continue
        mtype = str(item.get("type", "")).strip()
        content = str(item.get("content", "")).strip()
        if mtype in ("preference", "lesson", "research") and content:
            out.append({"type": mtype, "content": content})
    return out[:3]


def extract_memories_from_turn(
    *,
    user_text: str,
    assistant_text: str,
    session_id: str,
    model: str | None = None,
    source_message_id: str | None = None,
) -> list[dict[str, Any]]:
    """Return memory records created from a chat turn."""
    if not user_text.strip() or not assistant_text.strip():
        return []

    # A rule is not a memory. If this turn was creating an alert/signal rule, the
    # rule itself is the durable, executable record — don't ALSO store it as a
    # "preference" (redundant, and it would mislead future grounding into treating
    # a one-off alert as a standing preference).
    if looks_like_alert_intent(user_text):
        return []

    # Review/analysis turns synthesize narrative from analytics — do not seed
    # memories from generated reviews (avoids learning fabricated preferences).
    if looks_like_review_intent(user_text):
        return []

    llm = get_llm(model=model)
    messages = [
        LLMMessage(
            role="system",
            content=(
                "Extract ONLY new durable facts the user explicitly stated this turn. "
                "Return JSON: {\"memories\": [{\"type\": \"preference\"|\"lesson\", "
                "\"content\": \"...\"}]}. Do NOT extract: the assistant's own statements "
                "or restatements, facts already known/in memory, risk/config settings, "
                "or inferences. Only include explicit user preferences or lessons — not "
                "generic market commentary, and NOT one-off action requests (creating an "
                "alert, rule, or order are actions, not durable preferences). If the user "
                "did not explicitly state a new preference or lesson, return "
                "{\"memories\": []}."
            ),
        ),
        LLMMessage(
            role="user",
            content=f"User:\n{user_text}\n\nAssistant:\n{assistant_text}",
        ),
    ]

    try:
        raw = complete_for_extraction(llm, messages, max_tokens=300)
        candidates = _parse_llm_memories(raw)
    except Exception as exc:
        logger.warning("LLM memory extraction failed, using heuristics: %s", exc)
        candidates = []

    if not candidates:
        candidates = _heuristic_memories(user_text, assistant_text)

    memory = get_memory_service()
    metadata: dict[str, Any] = {"source": "chat", "session_id": session_id}
    if source_message_id:
        metadata["source_message_id"] = source_message_id
    saved: list[dict[str, Any]] = []
    for item in candidates:
        candidate_tokens = _memory_tokens(item["content"])
        existing = memory.list(memory_type=item["type"], limit=200)
        if any(
            _is_duplicate(candidate_tokens, _memory_tokens(row["content"]))
            for row in existing
        ):
            continue
        record = memory.add(
            item["content"],
            memory_type=item["type"],
            metadata=metadata,
        )
        saved.append(record)
    return saved
