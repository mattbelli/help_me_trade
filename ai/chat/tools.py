"""Trading copilot agent tools."""

from __future__ import annotations

import json
import re
from typing import Any

from ai.insights import snapshot_to_context
from ai.memories import get_memory_service
from ai.research.graph import run_research
from db.sqlite import get_latest_snapshot

TOOL_DEFINITIONS: dict[str, dict[str, Any]] = {
    "get_portfolio_snapshot": {
        "title": "Portfolio snapshot",
        "description": "Latest account, positions, and open orders from the broker poll.",
    },
    "search_memories": {
        "title": "Search memories",
        "description": "Search stored preferences, lessons, and research notes.",
    },
    "run_research": {
        "title": "Run research",
        "description": "Run the LangGraph research workflow for a topic or symbol.",
    },
}


def extract_symbol(text: str) -> str | None:
    match = re.search(r"\b([A-Z]{1,5})\b", text.upper())
    if match:
        sym = match.group(1)
        if sym not in {"I", "A", "THE", "AND", "OR", "FOR", "MY", "P/L"}:
            return sym
    return None


def plan_tools(user_text: str) -> list[tuple[str, dict[str, Any]]]:
    lower = user_text.lower()
    planned: list[tuple[str, dict[str, Any]]] = []

    if any(
        word in lower
        for word in (
            "portfolio",
            "position",
            "positions",
            "account",
            "holding",
            "holdings",
            "p/l",
            "pnl",
            "exposure",
            "orders",
        )
    ):
        planned.append(("get_portfolio_snapshot", {}))

    if any(
        word in lower
        for word in (
            "memory",
            "memories",
            "preference",
            "lesson",
            "remember",
            "recall",
        )
    ):
        planned.append(("search_memories", {"query": user_text, "limit": 5}))

    if any(word in lower for word in ("research", "deep dive", "analyze", "report on")):
        symbol = extract_symbol(user_text)
        planned.append(
            (
                "run_research",
                {"topic": user_text, "symbol": symbol},
            )
        )

    return planned


def execute_tool(name: str, args: dict[str, Any]) -> Any:
    if name == "get_portfolio_snapshot":
        snapshot = get_latest_snapshot()
        if snapshot is None:
            return {"error": "No snapshot yet — broker poll has not run."}
        return json.loads(snapshot_to_context(snapshot))

    if name == "search_memories":
        query = str(args.get("query", "")).strip()
        limit = int(args.get("limit", 5))
        results = get_memory_service().search(query, limit=limit)
        return {"results": results}

    if name == "run_research":
        topic = str(args.get("topic", "Market research")).strip()
        symbol = args.get("symbol")
        sym = str(symbol).strip().upper() if symbol else None
        return run_research(topic=topic, symbol=sym)

    return {"error": f"Unknown tool {name}"}
