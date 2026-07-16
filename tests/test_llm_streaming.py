"""Hermetic tests for the streaming native tool-call round-trip (stream_tools).

All tests run against a fake httpx transport that replays a canned SSE body —
never a live LLM. Covers the reassembly cases the founder's UX depends on:
tool-call arguments split across many fragments, two parallel tool calls
interleaved by index, text deltas surfacing live, and finish_reason=length
still logged (the DeepSeek truncation guard) on the streaming path.
"""

from __future__ import annotations

import json

import httpx
import pytest

from ai.llm import openai_compat
from ai.llm.base import (
    LLMMessage,
    StreamFinish,
    StreamTextDelta,
    StreamToolCall,
    ToolSpec,
)
from ai.llm.mock import MockLLM
from ai.llm.openai_compat import OpenAICompatibleLLM

WEATHER_TOOL = ToolSpec(
    name="get_weather",
    description="Get weather for a city.",
    parameters={"type": "object", "properties": {"city": {"type": "string"}}},
)


def _sse(*chunks: dict) -> bytes:
    body = "".join(f"data: {json.dumps(c)}\n\n" for c in chunks)
    return (body + "data: [DONE]\n\n").encode()


def _delta(delta: dict, *, finish: str | None = None) -> dict:
    return {"choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}


def _make_streaming_client(monkeypatch, body: bytes) -> OpenAICompatibleLLM:
    real_client = httpx.Client

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=body)

    def factory(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real_client(*args, **kwargs)

    monkeypatch.setattr(openai_compat.httpx, "Client", factory)
    return OpenAICompatibleLLM(
        provider="deepseek",
        api_key="sk-test",
        base_url="https://api.deepseek.com",
        model="deepseek-chat",
    )


async def _collect(llm, tools=None):
    out = []
    async for ev in llm.stream_tools([LLMMessage("user", "?")], tools or [WEATHER_TOOL]):
        out.append(ev)
    return out


@pytest.mark.asyncio
async def test_tool_call_arguments_split_across_fragments(monkeypatch):
    body = _sse(
        _delta({"tool_calls": [{"index": 0, "id": "c1", "type": "function",
                                "function": {"name": "get_weather", "arguments": ""}}]}),
        _delta({"tool_calls": [{"index": 0, "function": {"arguments": '{"ci'}}]}),
        _delta({"tool_calls": [{"index": 0, "function": {"arguments": 'ty": "N'}}]}),
        _delta({"tool_calls": [{"index": 0, "function": {"arguments": 'YC"}'}}]}),
        _delta({}, finish="tool_calls"),
    )
    llm = _make_streaming_client(monkeypatch, body)
    events = await _collect(llm)

    calls = [e for e in events if isinstance(e, StreamToolCall)]
    assert len(calls) == 1
    assert calls[0].call.id == "c1"
    assert calls[0].call.name == "get_weather"
    assert calls[0].call.arguments == {"city": "NYC"}  # reassembled across fragments
    finish = events[-1]
    assert isinstance(finish, StreamFinish) and finish.finish_reason == "tool_calls"


@pytest.mark.asyncio
async def test_two_parallel_tool_calls_interleaved_by_index(monkeypatch):
    body = _sse(
        _delta({"tool_calls": [{"index": 0, "id": "c1", "type": "function",
                                "function": {"name": "get_weather", "arguments": ""}}]}),
        _delta({"tool_calls": [{"index": 1, "id": "c2", "type": "function",
                                "function": {"name": "get_weather", "arguments": ""}}]}),
        _delta({"tool_calls": [{"index": 0, "function": {"arguments": '{"city": "NYC"}'}}]}),
        _delta({"tool_calls": [{"index": 1, "function": {"arguments": '{"city": "LA"}'}}]}),
        _delta({}, finish="tool_calls"),
    )
    llm = _make_streaming_client(monkeypatch, body)
    events = await _collect(llm)

    calls = [e.call for e in events if isinstance(e, StreamToolCall)]
    assert [(c.id, c.arguments["city"]) for c in calls] == [("c1", "NYC"), ("c2", "LA")]


@pytest.mark.asyncio
async def test_text_deltas_surface_live_then_finish(monkeypatch):
    body = _sse(
        _delta({"content": "AAPL "}),
        _delta({"content": "is "}),
        _delta({"content": "up."}),
        _delta({}, finish="stop"),
    )
    llm = _make_streaming_client(monkeypatch, body)
    events = await _collect(llm)

    deltas = [e.text for e in events if isinstance(e, StreamTextDelta)]
    assert deltas == ["AAPL ", "is ", "up."]
    assert not any(isinstance(e, StreamToolCall) for e in events)
    assert isinstance(events[-1], StreamFinish)


@pytest.mark.asyncio
async def test_finish_reason_length_logged_on_stream(monkeypatch, caplog):
    body = _sse(
        _delta({"content": "truncat"}),
        _delta({}, finish="length"),
    )
    llm = _make_streaming_client(monkeypatch, body)
    with caplog.at_level("WARNING"):
        events = await _collect(llm)
    assert any("finish_reason=length" in r.message for r in caplog.records)
    assert isinstance(events[-1], StreamFinish)
    assert events[-1].finish_reason == "length"


@pytest.mark.asyncio
async def test_http_error_raises_status_error(monkeypatch):
    real_client = httpx.Client

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"error": {"message": "rate limited"}})

    def factory(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real_client(*args, **kwargs)

    monkeypatch.setattr(openai_compat.httpx, "Client", factory)
    llm = OpenAICompatibleLLM(
        provider="deepseek", api_key="sk-test",
        base_url="https://api.deepseek.com", model="deepseek-chat",
    )
    with pytest.raises(httpx.HTTPStatusError):
        await _collect(llm)


@pytest.mark.asyncio
async def test_mock_llm_stream_tools_streams_text_then_finish():
    events = []
    async for ev in MockLLM().stream_tools([LLMMessage("user", "hello there")], [WEATHER_TOOL]):
        events.append(ev)
    assert any(isinstance(e, StreamTextDelta) for e in events)
    assert not any(isinstance(e, StreamToolCall) for e in events)
    assert isinstance(events[-1], StreamFinish)
    text = "".join(e.text for e in events if isinstance(e, StreamTextDelta))
    assert text  # deterministic synthesized answer streamed as word deltas
