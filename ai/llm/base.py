"""LLM client protocol and shared types."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class LLMMessage:
    role: str
    content: str


@runtime_checkable
class LLMClient(Protocol):
    provider: str

    def complete(self, messages: list[LLMMessage], *, max_tokens: int = 1024) -> str: ...

    def stream(
        self, messages: list[LLMMessage], *, max_tokens: int = 1024
    ) -> Iterator[str]: ...

    async def acomplete(
        self, messages: list[LLMMessage], *, max_tokens: int = 1024
    ) -> str: ...

    async def astream(
        self, messages: list[LLMMessage], *, max_tokens: int = 1024
    ) -> AsyncIterator[str]: ...
