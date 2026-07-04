"""Research workflow entry point — delegates to Engine v2 or setup scan."""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable
from typing import Any

from ai.research.engine_v2 import run_research_v2
from ai.research.setup_scan import run_setup_scan
from config.research_engine import resolve_effort

ProgressCallback = Callable[[str, dict[str, Any]], None]

VALID_RECIPES = frozenset({"single_stock", "setup_scan"})


def _normalize_depth(depth: str | None) -> str:
    chosen = str(depth or "").strip()
    if not chosen:
        return resolve_effort(None).name
    resolved = resolve_effort(chosen)
    if resolved.name != chosen:
        return resolve_effort("standard").name
    return resolved.name


def _run_setup_scan_sync(**kwargs: Any) -> dict[str, Any]:
    return asyncio.run(run_setup_scan(**kwargs))


def run_research(
    *,
    topic: str,
    symbol: str | None = None,
    depth: str = "standard",
    recipe: str = "single_stock",
    symbols: list[str] | None = None,
    on_progress: ProgressCallback | None = None,
    cancel_event: threading.Event | None = None,
) -> dict[str, Any]:
    effort_name = _normalize_depth(depth)
    recipe_name = str(recipe or "single_stock").strip().lower()
    if recipe_name not in VALID_RECIPES:
        raise ValueError(
            f"Unknown recipe {recipe!r}; must be one of: {', '.join(sorted(VALID_RECIPES))}"
        )

    if recipe_name == "setup_scan":
        return _run_setup_scan_sync(
            topic=topic,
            symbols=symbols,
            effort=effort_name,
            on_progress=on_progress,
            cancel_event=cancel_event,
        )

    return run_research_v2(
        topic=topic,
        symbol=symbol,
        effort=effort_name,
        on_progress=on_progress,
        cancel_event=cancel_event,
    )
