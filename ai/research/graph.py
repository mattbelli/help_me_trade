"""LangGraph research workflow."""

from __future__ import annotations

import json
from typing import Any, TypedDict

from langgraph.graph import END, StateGraph

from ai.llm.base import LLMMessage
from ai.llm.factory import get_llm
from ai.memories import get_memory_service
from db.ai_tables import save_research_artifact
from db.sqlite import get_latest_snapshot


class ResearchState(TypedDict, total=False):
    topic: str
    symbol: str | None
    context: str
    outline: str
    markdown: str
    artifact_id: int


def _gather_node(state: ResearchState) -> ResearchState:
    snapshot = get_latest_snapshot()
    memories = get_memory_service().list(memory_type="research", limit=5)
    memory_text = "\n".join(f"- {m['content']}" for m in memories)
    context = {
        "topic": state.get("topic", ""),
        "symbol": state.get("symbol"),
        "snapshot": snapshot,
        "prior_research": memory_text,
    }
    return {**state, "context": json.dumps(context, indent=2)}


def _analyze_node(state: ResearchState) -> ResearchState:
    llm = get_llm()
    messages = [
        LLMMessage(
            role="system",
            content=(
                "You are a trading research analyst. Produce a brief outline "
                "for a markdown research report."
            ),
        ),
        LLMMessage(
            role="user",
            content=(
                f"Topic: {state.get('topic')}\n"
                f"Symbol: {state.get('symbol') or 'N/A'}\n"
                f"Context:\n{state.get('context', '')}"
            ),
        ),
    ]
    outline = llm.complete(messages, max_tokens=400)
    return {**state, "outline": outline}


def _synthesize_node(state: ResearchState) -> ResearchState:
    llm = get_llm()
    messages = [
        LLMMessage(
            role="system",
            content=(
                "Write a markdown research report with sections: Summary, "
                "Key Observations, Risks, and Next Steps. Keep it practical."
            ),
        ),
        LLMMessage(
            role="user",
            content=(
                f"Topic: {state.get('topic')}\n"
                f"Outline:\n{state.get('outline', '')}\n"
                f"Context:\n{state.get('context', '')}"
            ),
        ),
    ]
    markdown = llm.complete(messages, max_tokens=1200)
    artifact = save_research_artifact(
        topic=str(state.get("topic", "Research")),
        symbol=state.get("symbol"),
        markdown=markdown,
    )
    get_memory_service().add(
        markdown[:500],
        memory_type="research",
        metadata={"artifact_id": artifact["id"], "symbol": state.get("symbol")},
    )
    return {**state, "markdown": markdown, "artifact_id": artifact["id"]}


def build_research_graph():
    graph = StateGraph(ResearchState)
    graph.add_node("gather", _gather_node)
    graph.add_node("analyze", _analyze_node)
    graph.add_node("synthesize", _synthesize_node)
    graph.set_entry_point("gather")
    graph.add_edge("gather", "analyze")
    graph.add_edge("analyze", "synthesize")
    graph.add_edge("synthesize", END)
    return graph.compile()


def run_research(*, topic: str, symbol: str | None = None) -> dict[str, Any]:
    app = build_research_graph()
    result = app.invoke({"topic": topic, "symbol": symbol})
    artifact_id = result.get("artifact_id")
    return {
        "artifact_id": artifact_id,
        "topic": topic,
        "symbol": symbol,
        "markdown": result.get("markdown", ""),
        "status": "completed",
    }
