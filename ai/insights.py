"""Portfolio insight generation from snapshots."""

from __future__ import annotations

import json
from typing import Any

from ai.llm.base import LLMMessage
from ai.llm.factory import get_llm


def snapshot_to_context(snapshot: dict[str, Any]) -> str:
    account = snapshot.get("account", {})
    positions = snapshot.get("positions", [])
    orders = snapshot.get("open_orders", [])
    return json.dumps(
        {
            "captured_at": snapshot.get("captured_at"),
            "broker": snapshot.get("broker"),
            "account": account,
            "position_count": len(positions),
            "positions": positions[:20],
            "open_order_count": len(orders),
            "open_orders": orders[:10],
        },
        indent=2,
    )


def build_insight_messages(snapshot: dict[str, Any]) -> list[LLMMessage]:
    context = snapshot_to_context(snapshot)
    return [
        LLMMessage(
            role="system",
            content=(
                "You are a trading copilot. Summarize the portfolio snapshot in "
                "exactly one concise paragraph. Focus on risk, exposure, and "
                "notable P/L. No bullet points."
            ),
        ),
        LLMMessage(
            role="user",
            content=f"Generate a one-paragraph portfolio insight from this snapshot:\n{context}",
        ),
    ]


async def generate_insight_paragraph(snapshot: dict[str, Any]) -> str:
    llm = get_llm()
    messages = build_insight_messages(snapshot)
    return await llm.acomplete(messages, max_tokens=300)
