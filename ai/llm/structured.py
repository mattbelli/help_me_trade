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
_FENCE_BLOCK_RE = re.compile(
    r"```(?:json)?\s*\n?(.*?)\n?```",
    re.DOTALL | re.IGNORECASE,
)
_TRAILING_COMMA_RE = re.compile(r",(\s*[}\]])")


def strip_json_fences(text: str) -> str:
    text = text.strip()
    match = _FENCE_BLOCK_RE.search(text)
    if match:
        return match.group(1).strip()
    text = _FENCE_RE.sub("", text)
    text = _FENCE_END_RE.sub("", text)
    return text.strip()


def _relax_trailing_commas(snippet: str) -> str:
    relaxed = snippet
    while True:
        updated = _TRAILING_COMMA_RE.sub(r"\1", relaxed)
        if updated == relaxed:
            return relaxed
        relaxed = updated


def _extract_balanced_object(text: str, start: int) -> str | None:
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


def _loads_relaxed_dict(snippet: str) -> dict[str, Any] | None:
    try:
        parsed = json.loads(_relax_trailing_commas(snippet))
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, dict):
        return None
    return parsed


def extract_json_object(
    text: str, prefer_keys: set[str] | None = None
) -> str | None:
    """Balanced-brace extraction with string/escape awareness. When ``prefer_keys`` is
    given, prefers the object containing the most of those keys (i.e. the one matching the
    target schema rather than an example object echoed from the prompt), tie-broken by
    size; otherwise prefers the largest valid object."""
    text = strip_json_fences(text)
    candidates: list[str] = []
    index = 0
    while index < len(text):
        start = text.find("{", index)
        if start < 0:
            break
        snippet = _extract_balanced_object(text, start)
        if snippet:
            candidates.append(snippet)
            index = start + len(snippet)
        else:
            index = start + 1

    scored: list[tuple[int, int, str]] = []
    for snippet in candidates:
        parsed = _loads_relaxed_dict(snippet)
        if parsed is None:
            continue
        key_score = (
            len(prefer_keys & set(parsed.keys())) if prefer_keys else 0
        )
        scored.append((key_score, len(snippet), snippet))
    if not scored:
        return None
    scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return scored[0][2]


def parse_json_object(
    raw: str, prefer_keys: set[str] | None = None
) -> dict[str, Any] | None:
    snippet = extract_json_object(raw, prefer_keys=prefer_keys)
    if not snippet:
        logger.warning("No JSON object found in LLM output (%d chars)", len(raw))
        return None
    parsed = _loads_relaxed_dict(snippet)
    if parsed is None:
        logger.warning("JSON decode failed for extracted object (%d chars)", len(snippet))
        return None
    if not isinstance(parsed, dict):
        logger.warning("Expected JSON object, got %s", type(parsed).__name__)
        return None
    return parsed


def parse_structured(raw: str, model: type[T]) -> T | None:
    data = parse_json_object(raw, prefer_keys=set(model.model_fields))
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
