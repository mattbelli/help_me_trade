"""Per-turn context injected into chat (memories, etc.)."""

from __future__ import annotations

import json
from typing import Any

from ai.memories.retrieval import retrieve_relevant_memories


def turn_memory_context(user_text: str, *, limit: int = 5) -> list[dict[str, Any]]:
    return retrieve_relevant_memories(user_text, limit=limit)


def format_memory_context_block(memories: list[dict[str, Any]]) -> str:
    if not memories:
        return ""
    compact = [
        {
            "type": item.get("type"),
            "content": item.get("content"),
            "score": item.get("score"),
        }
        for item in memories
    ]
    return "\n\nRelevant memories for this turn:\n" + json.dumps(compact, indent=2)
