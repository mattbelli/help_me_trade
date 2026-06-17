"""Helpers for passing full source text into research analysis."""

from __future__ import annotations

import json
from typing import Any

_CONTENT_LIMIT = 1500


def source_content_excerpt(source: dict[str, Any], *, max_chars: int = _CONTENT_LIMIT) -> str:
    content = source.get("content")
    if content is None:
        return str(source.get("snippet", ""))[:max_chars]
    if isinstance(content, (dict, list)):
        return json.dumps(content, default=str)[:max_chars]
    return str(content)[:max_chars]


def source_for_analysis(source: dict[str, Any], *, max_chars: int = _CONTENT_LIMIT) -> dict[str, Any]:
    return {
        "id": source.get("id"),
        "type": source.get("type"),
        "title": source.get("title"),
        "url": source.get("url"),
        "snippet": source.get("snippet"),
        "is_mock": bool(source.get("is_mock")),
        "content_excerpt": source_content_excerpt(source, max_chars=max_chars),
    }
