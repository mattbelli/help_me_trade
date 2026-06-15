"""Convert AI SDK UI messages to LLM messages."""

from __future__ import annotations

from typing import Any

from ai.llm.base import LLMMessage


def _text_from_parts(parts: list[dict[str, Any]]) -> str:
    chunks: list[str] = []
    for part in parts:
        if not isinstance(part, dict):
            continue
        if part.get("type") == "text" and part.get("text"):
            chunks.append(str(part["text"]))
    return "\n".join(chunks).strip()


def ui_messages_to_llm(messages: list[dict[str, Any]]) -> list[LLMMessage]:
    out: list[LLMMessage] = []
    for msg in messages:
        if not isinstance(msg, dict):
            continue
        role = str(msg.get("role", "user"))
        if role not in ("user", "assistant", "system"):
            continue
        parts = msg.get("parts")
        if isinstance(parts, list):
            text = _text_from_parts(parts)
        else:
            text = str(msg.get("content", "")).strip()
        if text:
            out.append(LLMMessage(role=role, content=text))
    return out


def last_user_text(messages: list[dict[str, Any]]) -> str:
    for msg in reversed(messages):
        if not isinstance(msg, dict) or msg.get("role") != "user":
            continue
        parts = msg.get("parts")
        if isinstance(parts, list):
            text = _text_from_parts(parts)
            if text:
                return text
        content = str(msg.get("content", "")).strip()
        if content:
            return content
    return ""
