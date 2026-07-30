"""Chat-turn helpers shared by the web and mobile chat routes.

The Next.js-host helpers that lived here (``build_turn_context``,
``llm_runtime_config``, ``run_tool``) died with the TS chat brain on
2026-07-30 — see the BUILD_LOG row and card ``chat-dead-ts-surface``.
"""

from __future__ import annotations

from typing import Any

from ai.chat.agent import ChatRunResult, finalize_chat_turn
from ai.chat.messages import last_user_text
from ai.chat.tools import TOOL_DEFINITIONS


def normalize_chat_mode(mode: str) -> str:
    m = str(mode or "temporary").strip().lower()
    if m in ("quick", "temporary"):
        return "temporary"
    if m in ("standard", "private", "saved"):
        return "saved"
    return m


def is_ephemeral_mode(mode: str) -> bool:
    return normalize_chat_mode(mode) == "temporary"


def should_learn_from_turn(*, mode: str) -> bool:
    if is_ephemeral_mode(mode):
        return False
    legacy = str(mode).strip().lower()
    if legacy == "private":
        return False
    return True


def tool_catalog() -> list[dict[str, Any]]:
    return [
        {
            "name": name,
            "title": meta.get("title", name),
            "description": meta.get("description", ""),
        }
        for name, meta in TOOL_DEFINITIONS.items()
    ]


def finalize_turn(
    *,
    messages: list[dict[str, Any]],
    assistant_text: str,
    session_id: str | None,
    mode: str,
    model: str | None = None,
    assistant_message: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if is_ephemeral_mode(mode):
        return {"ok": True, "memories_saved": []}

    user_text = last_user_text(messages)
    if not user_text and not assistant_message:
        return {"ok": True, "memories_saved": []}

    run = ChatRunResult(assistant_text=assistant_text)
    memory_mode = "standard" if should_learn_from_turn(mode=mode) else "private"
    finalize_chat_turn(
        messages=messages,
        user_text=user_text,
        assistant_text=assistant_text,
        session_id=session_id,
        mode=memory_mode if normalize_chat_mode(mode) == "saved" else mode,
        model=model,
        run=run,
        assistant_message=assistant_message,
        extract=should_learn_from_turn(mode=mode),
    )
    return {"ok": True, "memories_saved": run.memories_saved}
