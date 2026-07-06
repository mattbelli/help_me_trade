"""Mem0-compatible memory service with local SQLite fallback."""

from __future__ import annotations

import logging
from typing import Any

from db.ai_tables import (
    create_memory,
    delete_memory,
    get_memory,
    list_memories,
    update_memory,
)

logger = logging.getLogger(__name__)

USER_ID = "trader"


class MemoryService:
    """Wraps mem0ai when available; always mirrors to local SQLite."""

    def __init__(self) -> None:
        self._mem0: Any | None = None
        self._init_mem0()

    def _init_mem0(self) -> None:
        try:
            from mem0 import Memory  # type: ignore[import-untyped]

            self._mem0 = Memory()
            logger.info("Mem0 Memory client initialized")
        except Exception as exc:
            logger.info("Mem0 unavailable, using local SQLite store: %s", exc)
            self._mem0 = None

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
        record = create_memory(
            memory_type=memory_type,
            content=content,
            metadata=meta,
            source=source,
            authority=authority,
            quote=quote,
            symbols=symbols,
        )
        if self._mem0 is not None:
            try:
                self._mem0.add(
                    content,
                    user_id=USER_ID,
                    metadata={"category": memory_type, **meta, "local_id": record["id"]},
                )
            except Exception as exc:
                logger.warning("Mem0 add failed (local copy saved): %s", exc)
        return record

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

        if self._mem0 is not None:
            try:
                results = self._mem0.search(
                    query,
                    user_id=USER_ID,
                    limit=limit,
                )
                if results:
                    out: list[dict[str, Any]] = []
                    for item in results:
                        if isinstance(item, dict):
                            meta = item.get("metadata") or {}
                            cat = meta.get("category") or meta.get("type") or "preference"
                            if memory_type and cat != memory_type:
                                continue
                            out.append(
                                {
                                    "id": item.get("id"),
                                    "type": cat,
                                    "content": item.get("memory") or item.get("text", ""),
                                    "metadata": meta,
                                    "score": item.get("score"),
                                }
                            )
                    if out:
                        return out[:limit]
            except Exception as exc:
                logger.warning("Mem0 search failed, falling back to local: %s", exc)
        return retrieve_relevant_memories(
            query, memory_type=memory_type, limit=limit
        )

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
        record = update_memory(
            memory_id,
            content=content,
            memory_type=memory_type,
            metadata=metadata,
            status=status,
            pinned=pinned,
            authority=authority,
            valid_until=valid_until,
        )
        if record is None:
            return None
        if self._mem0 is not None and content is not None:
            try:
                self._mem0.add(
                    content,
                    user_id=USER_ID,
                    metadata={
                        "category": record["type"],
                        **record.get("metadata", {}),
                        "local_id": memory_id,
                    },
                )
            except Exception as exc:
                logger.warning("Mem0 update mirror failed (local saved): %s", exc)
        return record

    def delete(self, memory_id: int) -> bool:
        deleted = delete_memory(memory_id)
        if deleted and self._mem0 is not None:
            try:
                found = self._mem0.get_all(
                    filters={"user_id": USER_ID, "local_id": memory_id}
                )
                items = found.get("results", []) if isinstance(found, dict) else found or []
                for item in items:
                    item_id = item.get("id") if isinstance(item, dict) else None
                    if item_id:
                        self._mem0.delete(item_id)
            except Exception as exc:
                logger.warning("Mem0 delete failed (local delete succeeded): %s", exc)
        return deleted


_memory_service: MemoryService | None = None


def get_memory_service() -> MemoryService:
    global _memory_service
    if _memory_service is None:
        _memory_service = MemoryService()
    return _memory_service
