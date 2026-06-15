"""OpenAI chat completions provider."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Iterator

import httpx

from ai.llm.base import LLMMessage
from config.settings import load_settings


class OpenAILLM:
    provider = "openai"

    def __init__(self) -> None:
        ai = load_settings()["ai"]
        self._api_key = str(ai.get("openai_api_key", "")).strip()
        self._model = str(ai.get("openai_model", "gpt-4o")).strip() or "gpt-4o"
        if not self._api_key:
            raise ValueError("OPENAI_API_KEY is required for openai provider")

    def complete(self, messages: list[LLMMessage], *, max_tokens: int = 1024) -> str:
        payload = self._build_payload(messages, max_tokens=max_tokens)
        with httpx.Client(timeout=120.0) as client:
            resp = client.post(
                "https://api.openai.com/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()
        return str(data["choices"][0]["message"]["content"])

    def stream(
        self, messages: list[LLMMessage], *, max_tokens: int = 1024
    ) -> Iterator[str]:
        payload = self._build_payload(messages, max_tokens=max_tokens, stream=True)
        with httpx.Client(timeout=120.0) as client:
            with client.stream(
                "POST",
                "https://api.openai.com/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
            ) as resp:
                resp.raise_for_status()
                for line in resp.iter_lines():
                    if not line.startswith("data: "):
                        continue
                    chunk = line[6:].strip()
                    if chunk == "[DONE]":
                        break
                    import json

                    parsed = json.loads(chunk)
                    delta = parsed["choices"][0].get("delta", {})
                    text = delta.get("content")
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

    def _build_payload(
        self, messages: list[LLMMessage], *, max_tokens: int, stream: bool = False
    ) -> dict:
        payload = {
            "model": self._model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "max_tokens": max_tokens,
        }
        if stream:
            payload["stream"] = True
        return payload
