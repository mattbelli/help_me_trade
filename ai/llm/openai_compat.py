"""OpenAI-compatible chat completions API (OpenAI, DeepSeek, OpenRouter, etc.)."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator, Iterator
from typing import Any
from urllib.parse import urljoin

import httpx

from ai.llm.base import LLMMessage

logger = logging.getLogger(__name__)


def _http_error_detail(resp: httpx.Response) -> str:
    try:
        body = resp.json()
        if isinstance(body, dict):
            err = body.get("error")
            if isinstance(err, dict) and err.get("message"):
                return str(err["message"])
            if body.get("message"):
                return str(body["message"])
    except Exception:
        pass
    text = resp.text.strip()
    if text:
        return text[:500]
    return resp.reason_phrase or "HTTP error"


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

    def _build_payload(
        self,
        messages: list[LLMMessage],
        *,
        max_tokens: int,
        stream: bool = False,
        temperature: float | None = None,
        json_mode: bool = False,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self._model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "max_tokens": max_tokens,
        }
        if stream:
            payload["stream"] = True
        if temperature is not None:
            payload["temperature"] = temperature
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        return payload

    def _json_mode_allowed(self, messages: list[LLMMessage]) -> bool:
        """DeepSeek rejects json_object unless the prompt mentions json; reasoner often rejects it entirely."""
        if self.provider != "deepseek":
            return True
        if "reasoner" in self._model.lower():
            return False
        combined = " ".join(m.content for m in messages).lower()
        return "json" in combined

    def complete(
        self,
        messages: list[LLMMessage],
        *,
        max_tokens: int = 1024,
        temperature: float | None = None,
        json_mode: bool = False,
    ) -> str:
        payload = self._build_payload(
            messages,
            max_tokens=max_tokens,
            temperature=temperature,
            json_mode=json_mode,
        )
        with httpx.Client(timeout=120.0) as client:
            resp = client.post(self._endpoint(), headers=self._headers(), json=payload)
            if resp.is_error:
                detail = _http_error_detail(resp)
                raise httpx.HTTPStatusError(
                    f"{resp.status_code} {resp.reason_phrase} from {self.provider}: {detail}",
                    request=resp.request,
                    response=resp,
                )
            data = resp.json()
        choice = data["choices"][0]
        if choice.get("finish_reason") == "length":
            logger.warning(
                "%s completion truncated at max_tokens=%d (finish_reason=length)",
                self.provider,
                max_tokens,
            )
        return str(choice["message"]["content"] or "")

    def complete_json(
        self,
        messages: list[LLMMessage],
        *,
        max_tokens: int = 1024,
        temperature: float = 0.1,
    ) -> str:
        json_mode = self._json_mode_allowed(messages)
        raw = self.complete(
            messages,
            max_tokens=max_tokens,
            temperature=temperature,
            json_mode=json_mode,
        )
        # DeepSeek's json_object mode is documented to occasionally return empty
        # content — retry once without it before callers hit their fallbacks.
        if json_mode and not raw.strip():
            logger.warning(
                "%s returned empty content in json_object mode; retrying without it",
                self.provider,
            )
            raw = self.complete(
                messages,
                max_tokens=max_tokens,
                temperature=temperature,
                json_mode=False,
            )
        return raw

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
