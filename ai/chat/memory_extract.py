"""Extract durable memories from chat turns (standard mode only)."""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from ai.llm.base import LLMMessage
from ai.llm.factory import get_llm
from ai.memories import get_memory_service

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
    text = raw.strip()
    try:
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            return []
        parsed = json.loads(text[start : end + 1])
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
    except json.JSONDecodeError:
        return []


def extract_memories_from_turn(
    *,
    user_text: str,
    assistant_text: str,
    session_id: str,
) -> list[dict[str, Any]]:
    """Return memory records created from a chat turn."""
    if not user_text.strip() or not assistant_text.strip():
        return []

    llm = get_llm()
    messages = [
        LLMMessage(
            role="system",
            content=(
                "Extract 0-2 durable trading facts from this chat turn for long-term "
                "memory. Return JSON: {\"memories\": [{\"type\": \"preference\"|\"lesson\", "
                "\"content\": \"...\"}]}. Only include explicit user preferences, risk "
                "rules, or lessons — not generic market commentary. Return "
                "{\"memories\": []} if nothing is worth storing."
            ),
        ),
        LLMMessage(
            role="user",
            content=f"User:\n{user_text}\n\nAssistant:\n{assistant_text}",
        ),
    ]

    try:
        raw = llm.complete(messages, max_tokens=300)
        candidates = _parse_llm_memories(raw)
    except Exception as exc:
        logger.warning("LLM memory extraction failed, using heuristics: %s", exc)
        candidates = []

    if not candidates:
        candidates = _heuristic_memories(user_text, assistant_text)

    memory = get_memory_service()
    saved: list[dict[str, Any]] = []
    for item in candidates:
        record = memory.add(
            item["content"],
            memory_type=item["type"],
            metadata={"source": "chat", "session_id": session_id},
        )
        saved.append(record)
    return saved
