"""Simple mobile copilot SSE encoder (one JSON object per data: line)."""

from __future__ import annotations

import json
from typing import Any


def sse(event: dict[str, Any]) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
