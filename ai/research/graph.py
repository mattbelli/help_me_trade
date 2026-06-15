"""LangGraph research workflow — delegates to Odysseus-style deep research."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ai.research.deep_graph import run_deep_research
from ai.research.depth import RESEARCH_DEPTHS

ProgressCallback = Callable[[str, dict[str, Any]], None]


def run_research(
    *,
    topic: str,
    symbol: str | None = None,
    depth: str = "standard",
    on_progress: ProgressCallback | None = None,
) -> dict[str, Any]:
    if depth not in RESEARCH_DEPTHS:
        depth = "standard"
    return run_deep_research(
        topic=topic,
        symbol=symbol,
        depth=depth,
        on_progress=on_progress,
    )
