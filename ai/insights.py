"""Portfolio insight generation from snapshots."""

from __future__ import annotations

import json
from typing import Any

from ai.llm.base import LLMMessage
from ai.llm.factory import get_llm
from ai.memories.retrieval import retrieve_relevant_memories
from portfolio.analytics import compute_portfolio_analytics


def snapshot_to_context(snapshot: dict[str, Any]) -> str:
    account = snapshot.get("account", {})
    positions = snapshot.get("positions", [])
    orders = snapshot.get("open_orders", [])
    analytics = snapshot.get("analytics")
    if analytics is None:
        analytics = compute_portfolio_analytics(snapshot)
    return json.dumps(
        {
            "captured_at": snapshot.get("captured_at"),
            "broker": snapshot.get("broker"),
            "account": account,
            "analytics": analytics,
            "position_count": len(positions),
            "positions": positions[:20],
            "open_order_count": len(orders),
            "open_orders": orders[:10],
        },
        indent=2,
    )


def _memory_context_for_snapshot(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    positions = snapshot.get("positions") or []
    symbols = " ".join(str(p.get("symbol", "")) for p in positions[:10])
    analytics = snapshot.get("analytics") or {}
    flags = " ".join(analytics.get("flags") or [])
    query = f"portfolio risk preferences exposure {symbols} {flags}".strip()
    return retrieve_relevant_memories(query, limit=5)


def build_insight_messages(snapshot: dict[str, Any]) -> list[LLMMessage]:
    context = snapshot_to_context(snapshot)
    analytics = snapshot.get("analytics") or {}
    flags = analytics.get("flags") or []
    flags_hint = (
        f" Risk flags to mention if relevant: {'; '.join(flags)}."
        if flags
        else ""
    )
    memories = _memory_context_for_snapshot(snapshot)
    memory_hint = ""
    if memories:
        memory_hint = (
            "\nIncorporate relevant trader preferences or lessons from memories when they apply."
        )
    memory_json = json.dumps(
        [
            {"type": m.get("type"), "content": m.get("content"), "score": m.get("score")}
            for m in memories
        ],
        indent=2,
    )
    return [
        LLMMessage(
            role="system",
            content=(
                "You are a trading copilot. Summarize the portfolio in exactly one "
                "concise paragraph using the precomputed analytics (exposure, "
                "concentration, unrealized P/L, drift vs prior poll). Focus on risk "
                "and notable changes — not a position-by-position list."
                f"{flags_hint}{memory_hint} No bullet points."
            ),
        ),
        LLMMessage(
            role="user",
            content=(
                f"Generate a one-paragraph portfolio insight from this data:\n{context}"
                f"\n\nRelevant memories:\n{memory_json}"
            ),
        ),
    ]


async def generate_insight_paragraph(snapshot: dict[str, Any]) -> str:
    llm = get_llm()
    messages = build_insight_messages(snapshot)
    return await llm.acomplete(messages, max_tokens=300)
