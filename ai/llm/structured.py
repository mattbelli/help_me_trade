"""Centralized JSON extraction and structured LLM completions."""

from __future__ import annotations

import json
import logging
import re
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from ai.llm.base import LLMMessage

logger = logging.getLogger(__name__)

EXTRACTION_TEMPERATURE = 0.1
T = TypeVar("T", bound=BaseModel)

_FENCE_RE = re.compile(r"^```(?:json)?\s*\n?", re.IGNORECASE)
_FENCE_END_RE = re.compile(r"\n?```\s*$")


def strip_json_fences(text: str) -> str:
    text = text.strip()
    text = _FENCE_RE.sub("", text)
    text = _FENCE_END_RE.sub("", text)
    return text.strip()


def extract_json_object(text: str) -> str | None:
    """Balanced-brace extraction (not rfind) with string/escape awareness."""
    text = strip_json_fences(text)
    start = text.find("{")
    if start < 0:
        return None

    depth = 0
    in_string = False
    escape = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            continue

        if char == '"':
            in_string = True
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]
    return None


def parse_json_object(raw: str) -> dict[str, Any] | None:
    snippet = extract_json_object(raw)
    if not snippet:
        logger.warning("No JSON object found in LLM output (%d chars)", len(raw))
        return None
    try:
        parsed = json.loads(snippet)
    except json.JSONDecodeError as exc:
        logger.warning("JSON decode failed: %s", exc)
        return None
    if not isinstance(parsed, dict):
        logger.warning("Expected JSON object, got %s", type(parsed).__name__)
        return None
    return parsed


def parse_structured(raw: str, model: type[T]) -> T | None:
    data = parse_json_object(raw)
    if data is None:
        return None
    try:
        return model.model_validate(data)
    except ValidationError as exc:
        logger.warning("Schema validation failed for %s: %s", model.__name__, exc)
        return None


def complete_for_extraction(
    llm: Any,
    messages: list[LLMMessage],
    *,
    max_tokens: int = 1024,
) -> str:
    complete_json = getattr(llm, "complete_json", None)
    if callable(complete_json):
        return complete_json(
            messages,
            max_tokens=max_tokens,
            temperature=EXTRACTION_TEMPERATURE,
        )
    return llm.complete(messages, max_tokens=max_tokens)


def parse_with_retry(
    raw: str,
    model: type[T],
    *,
    retry_prompt: str | None = None,
    llm: Any | None = None,
    messages: list[LLMMessage] | None = None,
    max_tokens: int = 1024,
) -> T | None:
    parsed = parse_structured(raw, model)
    if parsed is not None:
        return parsed
    if not retry_prompt or llm is None or messages is None:
        return None

    retry_messages = [
        *messages,
        LLMMessage(role="assistant", content=raw),
        LLMMessage(role="user", content=retry_prompt),
    ]
    retry_raw = complete_for_extraction(llm, retry_messages, max_tokens=max_tokens)
    return parse_structured(retry_raw, model)
