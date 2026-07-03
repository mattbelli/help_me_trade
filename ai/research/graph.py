"""Research workflow entry point — delegates to Engine v2."""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any

from ai.research.engine_v2 import run_research_v2
from config.research_engine import resolve_effort

ProgressCallback = Callable[[str, dict[str, Any]], None]


def _normalize_depth(depth: str | None) -> str:
    chosen = str(depth or "").strip()
    if not chosen:
        return resolve_effort(None).name
    resolved = resolve_effort(chosen)
    if resolved.name != chosen:
        return resolve_effort("standard").name
    return resolved.name


def run_research(
    *,
    topic: str,
    symbol: str | None = None,
    depth: str = "standard",
    on_progress: ProgressCallback | None = None,
    cancel_event: threading.Event | None = None,
) -> dict[str, Any]:
    effort_name = _normalize_depth(depth)
    return run_research_v2(
        topic=topic,
        symbol=symbol,
        effort=effort_name,
        on_progress=on_progress,
        cancel_event=cancel_event,
    )
