"""Deterministic mock LLM for tests and offline development."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Iterator

from ai.llm.base import LLMMessage


class MockLLM:
    provider = "mock"

    def complete(self, messages: list[LLMMessage], *, max_tokens: int = 1024) -> str:
        return self._synthesize(messages)

    def stream(
        self, messages: list[LLMMessage], *, max_tokens: int = 1024
    ) -> Iterator[str]:
        text = self._synthesize(messages)
        words = text.split()
        for i, word in enumerate(words):
            yield word if i == 0 else " " + word

    async def acomplete(
        self, messages: list[LLMMessage], *, max_tokens: int = 1024
    ) -> str:
        return self.complete(messages, max_tokens=max_tokens)

    async def astream(
        self, messages: list[LLMMessage], *, max_tokens: int = 1024
    ) -> AsyncIterator[str]:
        for chunk in self.stream(messages, max_tokens=max_tokens):
            await asyncio.sleep(0)
            yield chunk

    def _synthesize(self, messages: list[LLMMessage]) -> str:
        combined = " ".join(m.content for m in messages).lower()
        user_text = " ".join(m.content for m in messages if m.role == "user")
        if "tool planner" in combined:
            user_lower = user_text.lower()
            if any(
                phrase in user_lower
                for phrase in (
                    "deep dive",
                    "research report",
                    "run research",
                )
            ):
                return json.dumps(
                    {
                        "tools": [
                            {
                                "name": "start_research",
                                "arguments": {
                                    "topic": user_text[:200],
                                    "depth": "standard",
                                },
                            }
                        ]
                    }
                )
            if any(
                word in user_lower
                for word in ("portfolio", "positions", "holdings", "exposure")
            ):
                return json.dumps(
                    {"tools": [{"name": "get_portfolio_snapshot", "arguments": {}}]}
                )
            return json.dumps({"tools": []})
        if "reflect" in combined or "lesson" in combined:
            return json.dumps(
                {
                    "summary": (
                        "Today's session shows disciplined sizing. "
                        "Focus on patience around entries."
                    ),
                    "lessons": [
                        {
                            "title": "Patience",
                            "content": "Wait for confirmation before scaling winners.",
                        }
                    ],
                }
            )
        if "trading research editor" in combined or "source_catalog" in combined:
            return json.dumps(
                {
                    "title": "Mock Research Report",
                    "summary": (
                        "Mock deep research complete. Trend is neutral with moderate "
                        "volatility. Consider monitoring key support before adding exposure."
                    ),
                    "metrics": [
                        {"label": "Trend", "value": "Neutral"},
                        {"label": "Volatility", "value": "Moderate"},
                    ],
                    "sections": [
                        {
                            "heading": "Key observations",
                            "body": (
                                "Price action shows consolidation. Volume is in line with "
                                "the 20-day average."
                            ),
                            "citations": ["src_1"],
                        }
                    ],
                    "risks": ["Earnings surprise", "Macro rate sensitivity"],
                    "next_steps": ["Watch support", "Size positions conservatively"],
                    "sources": [
                        {
                            "id": "src_1",
                            "title": "Portfolio snapshot",
                            "type": "portfolio",
                        }
                    ],
                }
            )
        if "research planner" in combined or '"queries"' in combined:
            return json.dumps(
                {"queries": ["momentum outlook", "sector peers and risks"]}
            )
        if "extract trading research findings" in combined:
            return json.dumps(
                {
                    "findings": [
                        {
                            "source_id": "src_1",
                            "claim": "Portfolio exposure is balanced with moderate risk.",
                            "confidence": "medium",
                        }
                    ]
                }
            )
        if any(
            word in combined
            for word in ("research", "analyze", "outline", "report", "topic:")
        ):
            return (
                "# Research Summary\n\n"
                "Mock analysis complete. Trend is neutral with moderate volatility. "
                "Consider monitoring key support levels before adding exposure."
            )
        if "insight" in combined or "portfolio" in combined or "snapshot" in combined:
            return (
                "Portfolio snapshot reviewed: cash and positions are stable. "
                "No urgent risk signals detected in the latest poll."
            )
        return f"Mock LLM response ({len(user_text)} chars of context)."
