"""Reflection loop — analyze session and store lessons."""

from __future__ import annotations

import json
import logging
from typing import Any

from ai.llm.base import LLMMessage
from ai.llm.factory import get_llm
from ai.llm.structured import complete_for_extraction, parse_json_object
from ai.memories import get_memory_service
from db.ai_tables import save_reflection_run
from db.sqlite import get_latest_snapshot

logger = logging.getLogger(__name__)


def _build_reflection_context() -> str:
    snapshot = get_latest_snapshot()
    lessons = get_memory_service().list(memory_type="lesson", limit=10)
    return json.dumps(
        {
            "snapshot": snapshot,
            "recent_lessons": [m["content"] for m in lessons],
        },
        indent=2,
    )


def run_reflection() -> dict[str, Any]:
    llm = get_llm()
    context = _build_reflection_context()
    messages = [
        LLMMessage(
            role="system",
            content=(
                "You are a trading coach running an end-of-session reflection. "
                "Return JSON with keys: summary (string paragraph) and lessons "
                "(array of {title, content} objects)."
            ),
        ),
        LLMMessage(
            role="user",
            content=f"Reflect on this trading session data:\n{context}",
        ),
    ]
    raw = complete_for_extraction(llm, messages, max_tokens=800)
    summary, lessons = _parse_reflection(raw)

    memory = get_memory_service()
    for lesson in lessons:
        title = lesson.get("title", "Lesson")
        content = lesson.get("content", "")
        if content:
            memory.add(
                f"{title}: {content}",
                memory_type="lesson",
                metadata={"source": "reflection"},
            )

    run = save_reflection_run(summary=summary, lessons=lessons)
    logger.info("Reflection run %s completed", run["id"])
    return run


def _parse_reflection(raw: str) -> tuple[str, list[dict[str, Any]]]:
    parsed = parse_json_object(raw)
    if parsed:
        summary = str(parsed.get("summary", raw))
        lessons = parsed.get("lessons", [])
        if isinstance(lessons, list):
            normalized = []
            for item in lessons:
                if isinstance(item, dict):
                    normalized.append(
                        {
                            "title": str(item.get("title", "Lesson")),
                            "content": str(item.get("content", "")),
                        }
                    )
                elif isinstance(item, str):
                    normalized.append({"title": "Lesson", "content": item})
            return summary, normalized
    return raw.strip(), [{"title": "Reflection", "content": raw}]
