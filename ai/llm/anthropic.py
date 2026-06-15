"""Anthropic Messages API provider."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Iterator

import httpx

from ai.llm.base import LLMMessage


class AnthropicLLM:
    provider = "anthropic"

    def __init__(self, *, api_key: str, base_url: str, model: str) -> None:
        self._api_key = api_key.strip()
        self._base_url = base_url.rstrip("/")
        self._model = model.strip() or "claude-sonnet-4-20250514"
        if not self._api_key:
            raise ValueError("ANTHROPIC_API_KEY is required for anthropic provider")

    def _endpoint(self) -> str:
        if self._base_url.endswith("/messages"):
            return self._base_url
        return f"{self._base_url}/v1/messages"

    def _build_body(
        self, messages: list[LLMMessage], *, max_tokens: int, stream: bool = False
    ) -> dict:
        system_parts: list[str] = []
        convo: list[dict[str, str]] = []
        for msg in messages:
            if msg.role == "system":
                system_parts.append(msg.content)
            else:
                role = "assistant" if msg.role == "assistant" else "user"
                convo.append({"role": role, "content": msg.content})
        if not convo:
            convo = [{"role": "user", "content": "Hello"}]
        body: dict = {
            "model": self._model,
            "max_tokens": max_tokens,
            "messages": convo,
        }
        if system_parts:
            body["system"] = "\n\n".join(system_parts)
        if stream:
            body["stream"] = True
        return body

    def complete(self, messages: list[LLMMessage], *, max_tokens: int = 1024) -> str:
        with httpx.Client(timeout=120.0) as client:
            resp = client.post(
                self._endpoint(),
                headers={
                    "x-api-key": self._api_key,
                    "anthropic-version": "2023-06-01",
                    "Content-Type": "application/json",
                },
                json=self._build_body(messages, max_tokens=max_tokens),
            )
            resp.raise_for_status()
            data = resp.json()
        parts = data.get("content", [])
        return "".join(
            p.get("text", "") for p in parts if isinstance(p, dict) and p.get("type") == "text"
        )

    def stream(
        self, messages: list[LLMMessage], *, max_tokens: int = 1024
    ) -> Iterator[str]:
        with httpx.Client(timeout=120.0) as client:
            with client.stream(
                "POST",
                self._endpoint(),
                headers={
                    "x-api-key": self._api_key,
                    "anthropic-version": "2023-06-01",
                    "Content-Type": "application/json",
                },
                json=self._build_body(messages, max_tokens=max_tokens, stream=True),
            ) as resp:
                resp.raise_for_status()
                for line in resp.iter_lines():
                    if not line.startswith("data: "):
                        continue
                    payload = json.loads(line[6:].strip())
                    if payload.get("type") != "content_block_delta":
                        continue
                    delta = payload.get("delta", {})
                    text = delta.get("text")
                    if text:
                        yield text

    async def acomplete(
        self, messages: list[LLMMessage], *, max_tokens: int = 1024
    ) -> str:
        return await asyncio.to_thread(self.complete, messages, max_tokens=max_tokens)

    async def astream(
        self, messages: list[LLMMessage], *, max_tokens: int = 1024
    ) -> AsyncIterator[str]:
        queue: asyncio.Queue[str | None] = asyncio.Queue()

        def _producer() -> None:
            try:
                for chunk in self.stream(messages, max_tokens=max_tokens):
                    queue.put_nowait(chunk)
            finally:
                queue.put_nowait(None)

        asyncio.get_running_loop().run_in_executor(None, _producer)
        while True:
            item = await queue.get()
            if item is None:
                break
            yield item
