"""Relevance-ranked memory retrieval (local store; Mem0 when available)."""

from __future__ import annotations

import re
from typing import Any

from db.ai_tables import list_memories, search_memories

_TOKEN_RE = re.compile(r"[a-z0-9]{3,}")


def _tokenize(text: str) -> set[str]:
    return set(_TOKEN_RE.findall(text.lower()))


def score_memory_relevance(query: str, content: str) -> float:
    """Token overlap score in [0, 1+] — higher is more relevant."""
    q_tokens = _tokenize(query)
    if not q_tokens:
        return 0.0
    c_tokens = _tokenize(content)
    if not c_tokens:
        return 0.0
    overlap = len(q_tokens & c_tokens)
    score = overlap / len(q_tokens)
    lowered_query = query.strip().lower()
    lowered_content = content.lower()
    if lowered_query and lowered_query in lowered_content:
        score += 0.35
    return score


def retrieve_relevant_memories(
    query: str,
    *,
    memory_type: str | None = None,
    limit: int = 5,
) -> list[dict[str, Any]]:
    """Top-k memories by relevance, falling back to recent items when query is empty."""
    lim = max(1, min(limit, 20))
    q = query.strip()
    if not q:
        return list_memories(memory_type=memory_type, limit=lim)

    candidates = search_memories(q, memory_type=memory_type, limit=lim * 8)
    if len(candidates) < lim:
        seen = {item["id"] for item in candidates}
        for item in list_memories(memory_type=memory_type, limit=lim * 6):
            if item["id"] not in seen:
                candidates.append(item)
                seen.add(item["id"])

    scored: list[tuple[float, dict[str, Any]]] = []
    for item in candidates:
        content = str(item.get("content", ""))
        relevance = score_memory_relevance(q, content)
        if relevance > 0:
            scored.append((relevance, {**item, "score": round(relevance, 3)}))

    scored.sort(key=lambda pair: pair[0], reverse=True)
    if scored:
        return [item for _, item in scored[:lim]]

    return candidates[:lim]
