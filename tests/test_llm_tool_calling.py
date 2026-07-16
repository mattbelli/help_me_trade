"""Hermetic tests for the native tool-call round-trip (Phase 1a).

All tests run against a scripted stub or a fake httpx transport — never a live
LLM. Covers: single call, multi-round loop, parallel tool_calls in one response,
malformed arguments -> typed error, max-iterations guard, and finish_reason=length
logging.
"""

from __future__ import annotations

import json

import httpx
import pytest

from ai.llm import openai_compat
from ai.llm.base import (
    LLMMessage,
    ToolArgumentError,
    ToolCall,
    ToolCallResult,
    ToolSpec,
)
from ai.llm.mock import MockLLM
from ai.llm.openai_compat import OpenAICompatibleLLM, _parse_tool_call, _serialize_message
from ai.llm.tool_loop import ToolLoopError, run_tool_loop

WEATHER_TOOL = ToolSpec(
    name="get_weather",
    description="Get weather for a city.",
    parameters={
        "type": "object",
        "properties": {"city": {"type": "string"}},
        "required": ["city"],
    },
)


def _oai_response(*, tool_calls=None, content=None, finish_reason="stop") -> dict:
    message: dict = {"role": "assistant"}
    if tool_calls is not None:
        message["tool_calls"] = tool_calls
        message["content"] = content
    else:
        message["content"] = content or ""
    return {"choices": [{"finish_reason": finish_reason, "message": message}]}


def _fn_call(call_id: str, name: str, arguments: str) -> dict:
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": arguments},
    }


def _make_openai_client(monkeypatch, handler) -> OpenAICompatibleLLM:
    """Patch openai_compat's httpx.Client to route through a MockTransport."""
    real_client = httpx.Client

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


# --- unit: parsing + serialization -----------------------------------------


def test_parse_tool_call_valid_object():
    call = _parse_tool_call(_fn_call("c1", "get_weather", '{"city": "NYC"}'))
    assert call == ToolCall(
        id="c1", name="get_weather", arguments={"city": "NYC"}, raw_arguments='{"city": "NYC"}'
    )


def test_parse_tool_call_empty_arguments_is_empty_dict():
    call = _parse_tool_call(_fn_call("c1", "noop", ""))
    assert call.arguments == {}


def test_parse_tool_call_malformed_json_raises_typed_error():
    with pytest.raises(ToolArgumentError) as exc:
        _parse_tool_call(_fn_call("c1", "get_weather", '{"city": '))
    assert exc.value.tool_name == "get_weather"
    assert exc.value.raw_arguments == '{"city": '


def test_parse_tool_call_non_object_json_raises_typed_error():
    with pytest.raises(ToolArgumentError):
        _parse_tool_call(_fn_call("c1", "get_weather", "[1, 2, 3]"))


def test_serialize_assistant_tool_call_and_tool_result_roundtrip():
    call = ToolCall(id="c1", name="get_weather", arguments={"city": "NYC"}, raw_arguments='{"city":"NYC"}')
    assistant = _serialize_message(
        LLMMessage(role="assistant", content="", tool_calls=(call,))
    )
    assert assistant["tool_calls"][0]["id"] == "c1"
    assert assistant["tool_calls"][0]["function"]["arguments"] == '{"city":"NYC"}'
    assert assistant["content"] is None

    tool_msg = _serialize_message(
        LLMMessage(role="tool", content="72F sunny", tool_call_id="c1", name="get_weather")
    )
    assert tool_msg == {"role": "tool", "content": "72F sunny", "tool_call_id": "c1"}

    plain = _serialize_message(LLMMessage(role="user", content="hi"))
    assert plain == {"role": "user", "content": "hi"}


# --- provider: complete_tools against a fake transport ----------------------


def test_complete_tools_single_tool_call(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["tools"][0]["function"]["name"] == "get_weather"
        assert body["tool_choice"] == "auto"
        return httpx.Response(
            200, json=_oai_response(tool_calls=[_fn_call("c1", "get_weather", '{"city": "NYC"}')])
        )

    llm = _make_openai_client(monkeypatch, handler)
    result = llm.complete_tools([LLMMessage("user", "weather in NYC?")], [WEATHER_TOOL])
    assert not result.is_final
    assert len(result.tool_calls) == 1
    assert result.tool_calls[0].name == "get_weather"
    assert result.tool_calls[0].arguments == {"city": "NYC"}


def test_complete_tools_final_text(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_oai_response(content="It is 72F."))

    llm = _make_openai_client(monkeypatch, handler)
    result = llm.complete_tools([LLMMessage("user", "hi")], [WEATHER_TOOL])
    assert result.is_final
    assert result.text == "It is 72F."
    assert result.tool_calls == ()


def test_complete_tools_parallel_tool_calls(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=_oai_response(
                tool_calls=[
                    _fn_call("c1", "get_weather", '{"city": "NYC"}'),
                    _fn_call("c2", "get_weather", '{"city": "LA"}'),
                ]
            ),
        )

    llm = _make_openai_client(monkeypatch, handler)
    result = llm.complete_tools([LLMMessage("user", "NYC and LA?")], [WEATHER_TOOL])
    assert [c.arguments["city"] for c in result.tool_calls] == ["NYC", "LA"]


def test_complete_tools_malformed_arguments_surfaces_typed_error(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json=_oai_response(tool_calls=[_fn_call("c1", "get_weather", "{bad json")])
        )

    llm = _make_openai_client(monkeypatch, handler)
    with pytest.raises(ToolArgumentError):
        llm.complete_tools([LLMMessage("user", "?")], [WEATHER_TOOL])


def test_complete_tools_logs_finish_reason_length(monkeypatch, caplog):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json=_oai_response(content="truncat", finish_reason="length")
        )

    llm = _make_openai_client(monkeypatch, handler)
    with caplog.at_level("WARNING"):
        llm.complete_tools([LLMMessage("user", "?")], [WEATHER_TOOL])
    assert any("finish_reason=length" in r.message for r in caplog.records)


def test_complete_tools_http_error_raises_status_error(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"error": {"message": "rate limited"}})

    llm = _make_openai_client(monkeypatch, handler)
    with pytest.raises(httpx.HTTPStatusError):
        llm.complete_tools([LLMMessage("user", "?")], [WEATHER_TOOL])


# --- loop driver ------------------------------------------------------------


class _ScriptedLLM:
    """Returns a preset sequence of ToolCallResults, one per complete_tools call."""

    provider = "scripted"

    def __init__(self, script: list[ToolCallResult]) -> None:
        self._script = list(script)
        self.calls: list[list[LLMMessage]] = []

    def complete_tools(self, messages, tools, **kwargs) -> ToolCallResult:
        self.calls.append(list(messages))
        return self._script.pop(0)


def test_run_tool_loop_multi_round():
    llm = _ScriptedLLM(
        [
            ToolCallResult(
                text=None,
                tool_calls=(ToolCall("c1", "get_weather", {"city": "NYC"}, '{"city":"NYC"}'),),
            ),
            ToolCallResult(text="It is 72F in NYC.", tool_calls=()),
        ]
    )
    executed: list[str] = []

    def execute(call: ToolCall) -> str:
        executed.append(call.name)
        return "72F sunny"

    result = run_tool_loop(llm, [LLMMessage("user", "weather?")], [WEATHER_TOOL], execute)
    assert result.text == "It is 72F in NYC."
    assert result.iterations == 2
    assert executed == ["get_weather"]
    # Second model call sees the appended assistant tool-call turn + tool result.
    second = llm.calls[1]
    assert second[-1].role == "tool"
    assert second[-1].tool_call_id == "c1"
    assert second[-2].tool_calls  # assistant turn carrying the request


def test_run_tool_loop_single_round_final():
    llm = _ScriptedLLM([ToolCallResult(text="direct answer", tool_calls=())])
    result = run_tool_loop(
        llm, [LLMMessage("user", "hi")], [WEATHER_TOOL], lambda c: "x"
    )
    assert result.text == "direct answer"
    assert result.iterations == 1
    assert result.tool_calls == []


def test_run_tool_loop_parallel_calls_all_executed():
    llm = _ScriptedLLM(
        [
            ToolCallResult(
                text=None,
                tool_calls=(
                    ToolCall("c1", "get_weather", {"city": "NYC"}, "{}"),
                    ToolCall("c2", "get_weather", {"city": "LA"}, "{}"),
                ),
            ),
            ToolCallResult(text="done", tool_calls=()),
        ]
    )
    seen: list[str] = []
    run_tool_loop(
        llm, [LLMMessage("user", "?")], [WEATHER_TOOL], lambda c: seen.append(c.id) or "ok"
    )
    assert seen == ["c1", "c2"]


def test_run_tool_loop_max_iterations_guard():
    never_final = ToolCallResult(
        text=None, tool_calls=(ToolCall("c", "get_weather", {}, "{}"),)
    )
    llm = _ScriptedLLM([never_final] * 10)
    with pytest.raises(ToolLoopError, match="max_iterations"):
        run_tool_loop(
            llm,
            [LLMMessage("user", "?")],
            [WEATHER_TOOL],
            lambda c: "ok",
            max_iterations=3,
        )
    assert len(llm.calls) == 3


def test_run_tool_loop_unsupported_provider_raises():
    class _NoTools:
        provider = "ollama"

    with pytest.raises(ToolLoopError, match="does not support"):
        run_tool_loop(_NoTools(), [LLMMessage("user", "?")], [WEATHER_TOOL], lambda c: "x")


def test_mock_llm_complete_tools_is_final_and_offline():
    result = MockLLM().complete_tools([LLMMessage("user", "hi")], [WEATHER_TOOL])
    assert isinstance(result, ToolCallResult)
    assert result.is_final
    assert result.tool_calls == ()


def test_run_tool_loop_terminates_with_mock_llm():
    result = run_tool_loop(
        MockLLM(), [LLMMessage("user", "hi")], [WEATHER_TOOL], lambda c: "x"
    )
    assert result.iterations == 1
    assert result.text


# --- anthropic tool-use blocks ---------------------------------------------


def test_anthropic_complete_tools_parses_tool_use(monkeypatch):
    from ai.llm import anthropic as anthropic_mod
    from ai.llm.anthropic import AnthropicLLM

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["tools"][0]["input_schema"]["type"] == "object"
        assert body["tool_choice"] == {"type": "auto"}
        return httpx.Response(
            200,
            json={
                "stop_reason": "tool_use",
                "content": [
                    {"type": "text", "text": "let me check"},
                    {"type": "tool_use", "id": "tu1", "name": "get_weather", "input": {"city": "NYC"}},
                ],
            },
        )

    real_client = httpx.Client

    def factory(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real_client(*args, **kwargs)

    monkeypatch.setattr(anthropic_mod.httpx, "Client", factory)
    llm = AnthropicLLM(api_key="sk-test", base_url="https://api.anthropic.com", model="claude-x")
    result = llm.complete_tools([LLMMessage("user", "weather?")], [WEATHER_TOOL])
    assert not result.is_final
    assert result.tool_calls[0].id == "tu1"
    assert result.tool_calls[0].arguments == {"city": "NYC"}
    assert result.finish_reason == "tool_use"
