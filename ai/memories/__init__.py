"""Memory service — one store (local SQLite), one seam.

mem0 was removed 2026-07-17 (founder call, per MEMORY_OVERHAUL_DESIGN.md §5.1): it was a
third store that produced two real bugs (delete-drift; the inert `{"results": [...]}`
search parsing) while its semantic search never actually served a hit. The judge +
FTS5/embedding retriever built by the overhaul replace its entire value natively.
`MemoryService` keeps the same public API so no caller changed.
"""

from __future__ import annotations

import logging
from typing import Any

from db.ai_tables import (
    create_memory,
    delete_memory,
    get_memory,
    list_memories,
    resolve_memory_proposal,
    update_memory,
)

logger = logging.getLogger(__name__)

USER_ID = "trader"


class MemoryService:
    """The single seam in front of the local SQLite memory store."""

    def add(
        self,
        content: str,
        *,
        memory_type: str,
        metadata: dict[str, Any] | None = None,
        source: str = "legacy",
        authority: str = "agent_inferred",
        quote: str | None = None,
        symbols: list[str] | None = None,
    ) -> dict[str, Any]:
        meta = dict(metadata or {})
        meta["type"] = memory_type
        return create_memory(
            memory_type=memory_type,
            content=content,
            metadata=meta,
            source=source,
            authority=authority,
            quote=quote,
            symbols=symbols,
        )

    def list(
        self,
        *,
        memory_type: str | None = None,
        limit: int = 50,
        include_inactive: bool = False,
    ) -> list[dict[str, Any]]:
        return list_memories(
            memory_type=memory_type, limit=limit, include_inactive=include_inactive
        )

    def search(
        self, query: str, *, memory_type: str | None = None, limit: int = 10
    ) -> list[dict[str, Any]]:
        from ai.memories.retrieval import retrieve_relevant_memories

        return retrieve_relevant_memories(query, memory_type=memory_type, limit=limit)

    def get(self, memory_id: int) -> dict[str, Any] | None:
        return get_memory(memory_id)

    def update(
        self,
        memory_id: int,
        *,
        content: str | None = None,
        memory_type: str | None = None,
        metadata: dict[str, Any] | None = None,
        status: str | None = None,
        pinned: bool | None = None,
        authority: str | None = None,
        valid_until: str | None = None,
    ) -> dict[str, Any] | None:
        return update_memory(
            memory_id,
            content=content,
            memory_type=memory_type,
            metadata=metadata,
            status=status,
            pinned=pinned,
            authority=authority,
            valid_until=valid_until,
        )

    def resolve_proposal(
        self, memory_id: int, *, action: str
    ) -> dict[str, Any] | None:
        """Founder Accept/Keep-old on a pending supersede proposal (M-1). The
        accept path IS the founder Confirm gesture, so it legally mints
        user_confirmed inside the atomic DB transition."""
        return resolve_memory_proposal(memory_id, action=action)

    def delete(self, memory_id: int) -> bool:
        return delete_memory(memory_id)


_memory_service: MemoryService | None = None


def get_memory_service() -> MemoryService:
    global _memory_service
    if _memory_service is None:
        _memory_service = MemoryService()
    return _memory_service
