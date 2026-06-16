"""Ollama local inference provider."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Iterator

import httpx

from ai.llm.base import LLMMessage


class OllamaLLM:
    provider = "ollama"

    def __init__(self, *, base_url: str, model: str) -> None:
        self._base_url = base_url.rstrip("/")
        self._model = model.strip() or "llama3.2"

    def complete(
        self,
        messages: list[LLMMessage],
        *,
        max_tokens: int = 1024,
        temperature: float | None = None,
        json_mode: bool = False,
    ) -> str:
        payload: dict[str, Any] = {
            "model": self._model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "stream": False,
            "options": {"num_predict": max_tokens},
        }
        if temperature is not None:
            payload["options"]["temperature"] = temperature
        if json_mode:
            payload["format"] = "json"
        with httpx.Client(timeout=180.0) as client:
            resp = client.post(f"{self._base_url}/api/chat", json=payload)
            resp.raise_for_status()
            data = resp.json()
        return str(data["message"]["content"])

    def complete_json(
        self,
        messages: list[LLMMessage],
        *,
        max_tokens: int = 1024,
        temperature: float = 0.1,
    ) -> str:
        return self.complete(
            messages,
            max_tokens=max_tokens,
            temperature=temperature,
            json_mode=True,
        )

    def stream(
        self, messages: list[LLMMessage], *, max_tokens: int = 1024
    ) -> Iterator[str]:
        payload = {
            "model": self._model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "stream": True,
            "options": {"num_predict": max_tokens},
        }
        with httpx.Client(timeout=180.0) as client:
            with client.stream(
                "POST", f"{self._base_url}/api/chat", json=payload
            ) as resp:
                resp.raise_for_status()
                for line in resp.iter_lines():
                    if not line:
                        continue
                    data = json.loads(line)
                    chunk = data.get("message", {}).get("content")
                    if chunk:
                        yield chunk

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
