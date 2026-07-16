"""Anthropic Messages API provider."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Iterator

import httpx

from ai.llm.base import (
    LLMMessage,
    ToolArgumentError,
    ToolCall,
    ToolCallResult,
    ToolSpec,
)


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

    def _headers(self) -> dict[str, str]:
        return {
            "x-api-key": self._api_key,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        }

    def _build_tools_body(
        self, messages: list[LLMMessage], *, max_tokens: int, tool_choice: str
    ) -> dict:
        system_parts: list[str] = []
        convo: list[dict] = []
        for msg in messages:
            if msg.role == "system":
                system_parts.append(msg.content)
            elif msg.tool_calls:
                blocks: list[dict] = []
                if msg.content:
                    blocks.append({"type": "text", "text": msg.content})
                for tc in msg.tool_calls:
                    blocks.append(
                        {
                            "type": "tool_use",
                            "id": tc.id,
                            "name": tc.name,
                            "input": tc.arguments,
                        }
                    )
                convo.append({"role": "assistant", "content": blocks})
            elif msg.role == "tool":
                convo.append(
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": msg.tool_call_id or "",
                                "content": msg.content,
                            }
                        ],
                    }
                )
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
        return body

    def complete_tools(
        self,
        messages: list[LLMMessage],
        tools: list[ToolSpec],
        *,
        tool_choice: str = "auto",
        max_tokens: int = 1024,
        temperature: float | None = None,
    ) -> ToolCallResult:
        body = self._build_tools_body(
            messages, max_tokens=max_tokens, tool_choice=tool_choice
        )
        if tools:
            body["tools"] = [
                {
                    "name": t.name,
                    "description": t.description,
                    "input_schema": t.parameters or {"type": "object", "properties": {}},
                }
                for t in tools
            ]
            choice_map = {"auto": "auto", "required": "any", "any": "any"}
            body["tool_choice"] = {"type": choice_map.get(tool_choice, "auto")}
        if temperature is not None:
            body["temperature"] = temperature

        with httpx.Client(timeout=120.0) as client:
            resp = client.post(self._endpoint(), headers=self._headers(), json=body)
            resp.raise_for_status()
            data = resp.json()

        stop_reason = data.get("stop_reason")
        blocks = data.get("content", [])
        text_parts: list[str] = []
        calls: list[ToolCall] = []
        for block in blocks:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "text":
                text_parts.append(block.get("text", ""))
            elif block.get("type") == "tool_use":
                name = str(block.get("name") or "")
                tool_input = block.get("input", {})
                if not isinstance(tool_input, dict):
                    raise ToolArgumentError(
                        tool_name=name,
                        raw_arguments=str(tool_input),
                        message=(
                            f"tool '{name}' input must be a JSON object, "
                            f"got {type(tool_input).__name__}"
                        ),
                    )
                calls.append(
                    ToolCall(
                        id=str(block.get("id") or ""),
                        name=name,
                        arguments=tool_input,
                        raw_arguments=json.dumps(tool_input),
                    )
                )
        if calls:
            return ToolCallResult(
                text="".join(text_parts) or None,
                tool_calls=tuple(calls),
                finish_reason=stop_reason,
            )
        return ToolCallResult(
            text="".join(text_parts), tool_calls=(), finish_reason=stop_reason
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
