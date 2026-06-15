"""Mem0-compatible memory service with local SQLite fallback."""

from __future__ import annotations

import logging
from typing import Any

from db.ai_tables import create_memory, list_memories, search_memories

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
    ) -> dict[str, Any]:
        meta = dict(metadata or {})
        meta["type"] = memory_type
        record = create_memory(
            memory_type=memory_type,
            content=content,
            metadata=meta,
        )
        if self._mem0 is not None:
            try:
                self._mem0.add(
                    content,
                    user_id=USER_ID,
                    metadata={"category": memory_type, **meta},
                )
            except Exception as exc:
                logger.warning("Mem0 add failed (local copy saved): %s", exc)
        return record

    def list(
        self, *, memory_type: str | None = None, limit: int = 50
    ) -> list[dict[str, Any]]:
        return list_memories(memory_type=memory_type, limit=limit)

    def search(
        self, query: str, *, memory_type: str | None = None, limit: int = 10
    ) -> list[dict[str, Any]]:
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
        return search_memories(query, memory_type=memory_type, limit=limit)


_memory_service: MemoryService | None = None


def get_memory_service() -> MemoryService:
    global _memory_service
    if _memory_service is None:
        _memory_service = MemoryService()
    return _memory_service
