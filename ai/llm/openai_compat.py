"""OpenAI-compatible chat completions API (OpenAI, DeepSeek, OpenRouter, etc.)."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator, Iterator
from typing import Any
from urllib.parse import urljoin

import httpx

from ai.llm.base import (
    LLMMessage,
    StreamEvent,
    StreamFinish,
    StreamTextDelta,
    StreamToolCall,
    ToolArgumentError,
    ToolCall,
    ToolCallResult,
    ToolSpec,
)

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


def _serialize_message(m: LLMMessage) -> dict[str, Any]:
    """Serialize an LLMMessage into an OpenAI chat-completions message dict,
    including assistant tool-call requests and tool-result messages."""
    if m.tool_calls:
        return {
            "role": m.role,
            "content": m.content or None,
            "tool_calls": [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.name,
                        "arguments": tc.raw_arguments or json.dumps(tc.arguments),
                    },
                }
                for tc in m.tool_calls
            ],
        }
    if m.tool_call_id is not None:
        return {
            "role": m.role,
            "content": m.content,
            "tool_call_id": m.tool_call_id,
        }
    return {"role": m.role, "content": m.content}


def _parse_tool_call(raw: dict[str, Any]) -> ToolCall:
    """Parse one provider tool_call entry into a ToolCall.

    Malformed argument JSON (or a non-object payload) is surfaced as a typed
    ToolArgumentError rather than crashing the caller with a JSONDecodeError.
    """
    fn = raw.get("function") or {}
    name = str(fn.get("name") or "")
    call_id = str(raw.get("id") or "")
    raw_args = fn.get("arguments")

    # Some OpenAI-compatible providers return already-parsed dict arguments.
    if isinstance(raw_args, dict):
        return ToolCall(
            id=call_id, name=name, arguments=raw_args, raw_arguments=json.dumps(raw_args)
        )

    raw_str = raw_args if isinstance(raw_args, str) else ""
    text = raw_str.strip()
    if not text:
        return ToolCall(id=call_id, name=name, arguments={}, raw_arguments=raw_str)
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ToolArgumentError(
            tool_name=name,
            raw_arguments=raw_str,
            message=f"tool '{name}' returned malformed JSON arguments: {exc}",
        ) from exc
    if not isinstance(parsed, dict):
        raise ToolArgumentError(
            tool_name=name,
            raw_arguments=raw_str,
            message=(
                f"tool '{name}' arguments must be a JSON object, "
                f"got {type(parsed).__name__}"
            ),
        )
    return ToolCall(id=call_id, name=name, arguments=parsed, raw_arguments=raw_str)


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

    def _http_timeout(self) -> float:
        # DeepSeek V4 thinking can run minutes on a book turn. 120s became the
        # silent successor to the 8k token clamp (08-14: uncap the book, keep
        # other OpenAI-compat providers on the original budget).
        if self.provider == "deepseek":
            return 600.0
        return 120.0

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
        with httpx.Client(timeout=self._http_timeout()) as client:
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

    def complete_tools(
        self,
        messages: list[LLMMessage],
        tools: list[ToolSpec],
        *,
        tool_choice: str = "auto",
        max_tokens: int = 1024,
        temperature: float | None = None,
    ) -> ToolCallResult:
        """One turn of a native tool-call round-trip.

        Sends the conversation plus tool definitions and returns either the
        model's tool-call requests or its final text answer. Separate from
        ``complete()`` so the plain/JSON-mode paths are unaffected.
        """
        payload: dict[str, Any] = {
            "model": self._model,
            "messages": [_serialize_message(m) for m in messages],
            "max_tokens": max_tokens,
        }
        if tools:
            payload["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": t.name,
                        "description": t.description,
                        "parameters": t.parameters or {"type": "object", "properties": {}},
                    },
                }
                for t in tools
            ]
            payload["tool_choice"] = tool_choice
        if temperature is not None:
            payload["temperature"] = temperature

        with httpx.Client(timeout=self._http_timeout()) as client:
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
        finish_reason = choice.get("finish_reason")
        if finish_reason == "length":
            logger.warning(
                "%s tool completion truncated at max_tokens=%d (finish_reason=length)",
                self.provider,
                max_tokens,
            )
        message = choice.get("message", {})
        raw_calls = message.get("tool_calls") or []
        if raw_calls:
            return ToolCallResult(
                text=message.get("content") or None,
                tool_calls=tuple(_parse_tool_call(c) for c in raw_calls),
                finish_reason=finish_reason,
            )
        return ToolCallResult(
            text=str(message.get("content") or ""),
            tool_calls=(),
            finish_reason=finish_reason,
        )

    async def acomplete_tools(
        self,
        messages: list[LLMMessage],
        tools: list[ToolSpec],
        *,
        tool_choice: str = "auto",
        max_tokens: int = 1024,
        temperature: float | None = None,
    ) -> ToolCallResult:
        return await asyncio.to_thread(
            self.complete_tools,
            messages,
            tools,
            tool_choice=tool_choice,
            max_tokens=max_tokens,
            temperature=temperature,
        )

    def _stream_tools_sync(
        self,
        messages: list[LLMMessage],
        tools: list[ToolSpec],
        *,
        tool_choice: str,
        max_tokens: int,
        temperature: float | None,
    ) -> Iterator[StreamEvent]:
        """Blocking SSE reader for the streaming tool-call round-trip.

        Yields text deltas the moment they arrive, then the fully reassembled
        tool calls, then a final ``StreamFinish``. Tool-call deltas carry an
        ``index``; id/name land on the first fragment for that index and the
        argument string accumulates across fragments (multiple parallel calls
        are tracked by index).
        """
        payload: dict[str, Any] = {
            "model": self._model,
            "messages": [_serialize_message(m) for m in messages],
            "max_tokens": max_tokens,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if tools:
            payload["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": t.name,
                        "description": t.description,
                        "parameters": t.parameters or {"type": "object", "properties": {}},
                    },
                }
                for t in tools
            ]
            payload["tool_choice"] = tool_choice
        if temperature is not None:
            payload["temperature"] = temperature

        # index -> {"id", "name", "args"} reassembly buffers (insertion-ordered).
        frags: dict[int, dict[str, str]] = {}
        finish_reason: str | None = None
        usage: dict[str, Any] | None = None
        with httpx.Client(timeout=self._http_timeout()) as client:
            with client.stream(
                "POST", self._endpoint(), headers=self._headers(), json=payload
            ) as resp:
                if resp.is_error:
                    resp.read()
                    detail = _http_error_detail(resp)
                    raise httpx.HTTPStatusError(
                        f"{resp.status_code} {resp.reason_phrase} from {self.provider}: {detail}",
                        request=resp.request,
                        response=resp,
                    )
                for line in resp.iter_lines():
                    if not line.startswith("data:"):
                        continue
                    chunk = line[len("data:") :].strip()
                    if chunk == "[DONE]":
                        break
                    parsed = json.loads(chunk)
                    if parsed.get("usage"):
                        usage = parsed["usage"]
                    choices = parsed.get("choices") or []
                    if not choices:
                        continue
                    choice = choices[0]
                    delta = choice.get("delta") or {}
                    text = delta.get("content")
                    if text:
                        yield StreamTextDelta(text)
                    for tc in delta.get("tool_calls") or []:
                        idx = tc.get("index", 0)
                        frag = frags.setdefault(idx, {"id": "", "name": "", "args": ""})
                        if tc.get("id"):
                            frag["id"] = str(tc["id"])
                        fn = tc.get("function") or {}
                        if fn.get("name"):
                            frag["name"] = str(fn["name"])
                        if fn.get("arguments"):
                            frag["args"] += str(fn["arguments"])
                    if choice.get("finish_reason"):
                        finish_reason = choice["finish_reason"]
        if finish_reason == "length":
            logger.warning(
                "%s tool stream truncated at max_tokens=%d (finish_reason=length)",
                self.provider,
                max_tokens,
            )
        for idx in sorted(frags):
            frag = frags[idx]
            yield StreamToolCall(
                _parse_tool_call(
                    {
                        "id": frag["id"],
                        "function": {"name": frag["name"], "arguments": frag["args"]},
                    }
                )
            )
        yield StreamFinish(finish_reason=finish_reason, usage=usage)

    async def stream_tools(
        self,
        messages: list[LLMMessage],
        tools: list[ToolSpec],
        *,
        tool_choice: str = "auto",
        max_tokens: int = 1024,
        temperature: float | None = None,
    ) -> AsyncIterator[StreamEvent]:
        """Async streaming variant of ``complete_tools``.

        Bridges the blocking SSE reader to async via an executor + queue. The
        producer thread must hand events over with ``call_soon_threadsafe`` —
        ``asyncio.Queue.put_nowait`` from a foreign thread doesn't wake the
        event-loop consumer, so deltas pile up and flush in one late burst
        (verified live: token-live became post-hoc). A provider/transport error
        mid-stream is surfaced by re-raising into the consumer so callers can
        terminate the stream cleanly instead of hanging.
        """
        queue: asyncio.Queue[StreamEvent | Exception | None] = asyncio.Queue()
        loop = asyncio.get_running_loop()

        def _producer() -> None:
            try:
                for event in self._stream_tools_sync(
                    messages,
                    tools,
                    tool_choice=tool_choice,
                    max_tokens=max_tokens,
                    temperature=temperature,
                ):
                    loop.call_soon_threadsafe(queue.put_nowait, event)
            except Exception as exc:  # surfaced to the consumer, not swallowed
                loop.call_soon_threadsafe(queue.put_nowait, exc)
            finally:
                loop.call_soon_threadsafe(queue.put_nowait, None)

        loop.run_in_executor(None, _producer)
        while True:
            item = await queue.get()
            if item is None:
                break
            if isinstance(item, Exception):
                raise item
            yield item

    def stream(
        self, messages: list[LLMMessage], *, max_tokens: int = 1024
    ) -> Iterator[str]:
        payload = self._build_payload(messages, max_tokens=max_tokens, stream=True)
        with httpx.Client(timeout=self._http_timeout()) as client:
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
        loop = asyncio.get_running_loop()

        def _producer() -> None:
            # call_soon_threadsafe: put_nowait from a foreign thread doesn't
            # wake the event-loop consumer (chunks flush in one late burst).
            try:
                for chunk in self.stream(messages, max_tokens=max_tokens):
                    loop.call_soon_threadsafe(queue.put_nowait, chunk)
            finally:
                loop.call_soon_threadsafe(queue.put_nowait, None)

        loop.run_in_executor(None, _producer)
        while True:
            item = await queue.get()
            if item is None:
                break
            yield item
