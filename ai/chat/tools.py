"""Trading copilot agent tools."""

from __future__ import annotations

import json
import logging
from typing import Any

from pydantic import BaseModel, Field

from ai.insights import snapshot_to_context
from ai.llm.base import LLMMessage
from ai.llm.factory import get_llm
from ai.llm.structured import complete_for_extraction, parse_json_object
from ai.memories import get_memory_service
from ai.research.jobs import submit_research_job
from ai.utils.symbols import extract_symbol
from db.sqlite import get_latest_snapshot
from db.trades import list_trade_events, trade_summary_stats
from db.analytics import ai_performance_context

logger = logging.getLogger(__name__)

TOOL_DEFINITIONS: dict[str, dict[str, Any]] = {
    "get_portfolio_snapshot": {
        "title": "Portfolio snapshot",
        "description": "Latest account, positions, and open orders from the broker poll.",
    },
    "search_memories": {
        "title": "Search memories",
        "description": "Search stored preferences, lessons, and research notes.",
    },
    "start_research": {
        "title": "Start research",
        "description": (
            "Enqueue Odysseus-style deep research (portfolio, bars, memories, web). "
            "Returns a job id — use the Research page or stream API for progress."
        ),
    },
    "get_trade_history": {
        "title": "Trade history",
        "description": (
            "Imported/synced trade ledger: fills, dividends, fees. "
            "Use for questions about past trades, symbols, or dates."
        ),
    },
    "get_performance_insights": {
        "title": "Performance insights",
        "description": (
            "Structured performance scorecard: win rate, P&L by symbol, day-of-week, "
            "recent closed trades, largest wins/losses. Prefer over guessing performance."
        ),
    },
}

_TOOL_PLANNER_PROMPT = """You are a tool planner for a trading copilot.
Return JSON only: {"tools": [{"name": "<tool>", "arguments": {...}}]}

Available tools:
- get_portfolio_snapshot — arguments: {}
- search_memories — arguments: {"query": string, "limit": number}
- start_research — arguments: {"topic": string, "symbol": string|null, "depth": "quick"|"standard"|"deep"}
- get_trade_history — arguments: {"symbol": string|null, "from_date": string|null, "to_date": string|null, "limit": number}
- get_performance_insights — arguments: {"period": "day"|"week"|"month"|"quarter"|"year"|"all", "account_id": string|null}

Rules:
- Return {"tools": []} when no tool is needed.
- Do NOT call start_research for general chat, risk opinions, or the word "analyze" alone.
- Only call start_research when the user explicitly asks for research, a deep dive, or a written report.
- Use get_trade_history for specific trade lookups; get_performance_insights for win rate, patterns, or overall P&L.
- Prefer 0-2 tools per turn.
"""


class PlannedToolCall(BaseModel):
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class ToolPlan(BaseModel):
    tools: list[PlannedToolCall] = Field(default_factory=list)


_ALLOWED_TOOLS = frozenset(TOOL_DEFINITIONS.keys())


def plan_tools(user_text: str, *, model: str | None = None) -> list[tuple[str, dict[str, Any]]]:
    """LLM JSON planner — replaces keyword matching."""
    text = user_text.strip()
    if not text:
        return []

    llm = get_llm(model=model)
    messages = [
        LLMMessage(role="system", content=_TOOL_PLANNER_PROMPT),
        LLMMessage(role="user", content=text),
    ]
    try:
        raw = complete_for_extraction(llm, messages, max_tokens=400)
        parsed = parse_json_object(raw)
        if not parsed:
            logger.warning("Tool plan JSON parse failed for: %r", text[:120])
            return []
        plan = ToolPlan.model_validate(parsed)
    except Exception:
        logger.exception("Tool planning failed")
        return []

    planned: list[tuple[str, dict[str, Any]]] = []
    for item in plan.tools:
        name = item.name.strip()
        if name not in _ALLOWED_TOOLS:
            logger.warning("Ignoring unknown planned tool: %s", name)
            continue
        args = dict(item.arguments or {})
        if name == "start_research" and not args.get("symbol"):
            sym = extract_symbol(text)
            if sym:
                args["symbol"] = sym
        planned.append((name, args))
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

    if name == "start_research":
        topic = str(args.get("topic", "Market research")).strip()
        symbol = args.get("symbol")
        sym = str(symbol).strip().upper() if symbol else None
        depth = str(args.get("depth", "standard")).strip().lower()
        if depth not in ("quick", "standard", "deep"):
            depth = "standard"

        job_id = submit_research_job(topic=topic, symbol=sym, depth=depth)

        return {
            "job_id": job_id,
            "status": "queued",
            "topic": topic,
            "symbol": sym,
            "depth": depth,
            "message": (
                "Research job queued. Open the Research page or poll "
                f"GET /api/research/jobs/{job_id} for status."
            ),
        }

    if name == "get_trade_history":
        symbol = args.get("symbol")
        sym = str(symbol).strip().upper() if symbol else None
        from_date = args.get("from_date")
        to_date = args.get("to_date")
        limit = min(int(args.get("limit", 50)), 200)
        events = list_trade_events(
            symbol=sym,
            from_date=str(from_date) if from_date else None,
            to_date=str(to_date) if to_date else None,
            limit=limit,
        )
        stats = trade_summary_stats(
            from_date=str(from_date) if from_date else None,
            to_date=str(to_date) if to_date else None,
        )
        return {
            "stats": stats,
            "events": events,
            "count": len(events),
            "symbol_filter": sym,
        }

    if name == "get_performance_insights":
        period = str(args.get("period", "year")).strip().lower()
        if period not in ("day", "week", "month", "quarter", "year", "all"):
            period = "year"
        account_id = args.get("account_id")
        return ai_performance_context(
            account_id=str(account_id) if account_id else None,
            period=period,
        )

    return {"error": f"Unknown tool {name}"}
