"""Chat brain helpers for the Next.js route host (Python remains the brain)."""

from __future__ import annotations

from typing import Any

from ai.chat.agent import ChatRunResult, chat_system_prompt, finalize_chat_turn
from ai.chat.alert_intent import alert_intent_prompt_block
from ai.chat.context import format_memory_context_block, turn_memory_context
from ai.chat.intent import order_intent_prompt_block
from ai.chat.messages import last_user_text
from ai.chat.tools import TOOL_DEFINITIONS, execute_tool
from ai.llm.config import get_default_llm, get_resolved_ai_settings, provider_credentials

OPENAI_COMPATIBLE_PROVIDERS = frozenset(
    {"openai", "deepseek", "openrouter", "ollama"}
)


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


def build_turn_context(
    user_text: str,
    *,
    inject_memories: bool = False,
) -> dict[str, Any]:
    memories = turn_memory_context(user_text) if inject_memories else []
    system = (
        chat_system_prompt()
        + order_intent_prompt_block(user_text)
        + alert_intent_prompt_block(user_text)
        + format_memory_context_block(memories)
    )
    return {
        "system": system,
        "memories": memories,
    }


def llm_runtime_config(*, model: str | None = None) -> dict[str, Any]:
    default_provider, default_model = get_default_llm()
    ai = get_resolved_ai_settings()
    creds = provider_credentials(default_provider, ai)
    resolved_model = (model or default_model or creds.get("model") or "").strip()
    return {
        "provider": default_provider,
        "api_key": creds["api_key"],
        "base_url": creds["base_url"],
        "model": resolved_model,
        "openai_compatible": default_provider in OPENAI_COMPATIBLE_PROVIDERS,
    }


def tool_catalog() -> list[dict[str, Any]]:
    return [
        {
            "name": name,
            "title": meta.get("title", name),
            "description": meta.get("description", ""),
        }
        for name, meta in TOOL_DEFINITIONS.items()
    ]


async def run_tool(name: str, arguments: dict[str, Any] | None = None) -> Any:
    return await execute_tool(name, dict(arguments or {}))


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
        extract=False,
    )
    return {"ok": True, "memories_saved": run.memories_saved}
