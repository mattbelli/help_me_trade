"""OpenAI-compatible chat completions API (OpenAI, DeepSeek, OpenRouter, etc.)."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Iterator
from typing import Any
from urllib.parse import urljoin

import httpx

from ai.llm.base import LLMMessage


class OpenAICompatibleLLM:
    """Chat completions client for any OpenAI-compatible HTTP API."""

    def __init__(
        self,
        *,
        provider: str,
        api_key: str,
        base_url: str,
        model: str,
        extra_headers: dict[str, str] | None = None,
        require_api_key: bool = True,
    ) -> None:
        self.provider = provider
        self._api_key = api_key.strip()
        self._base_url = base_url.rstrip("/")
        self._model = model.strip()
        self._extra_headers = extra_headers or {}
        if require_api_key and not self._api_key:
            raise ValueError(f"API key is required for {provider} provider")
        if not self._model:
            raise ValueError(f"Model is required for {provider} provider")

    def _endpoint(self) -> str:
        if self._base_url.endswith("/chat/completions"):
            return self._base_url
        return urljoin(f"{self._base_url}/", "chat/completions")

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json", **self._extra_headers}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        return headers

    def complete(self, messages: list[LLMMessage], *, max_tokens: int = 1024) -> str:
        payload = self._build_payload(messages, max_tokens=max_tokens)
        with httpx.Client(timeout=120.0) as client:
            resp = client.post(self._endpoint(), headers=self._headers(), json=payload)
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
                self._endpoint(),
                headers=self._headers(),
                json=payload,
            ) as resp:
                resp.raise_for_status()
                for line in resp.iter_lines():
                    if not line.startswith("data: "):
                        continue
                    chunk = line[6:].strip()
                    if chunk == "[DONE]":
                        break
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
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self._model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "max_tokens": max_tokens,
        }
        if stream:
            payload["stream"] = True
        return payload
