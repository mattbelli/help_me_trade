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
        user_text = " ".join(m.content for m in messages if m.role == "user")
        return f"Mock LLM response ({len(user_text)} chars of context)."
