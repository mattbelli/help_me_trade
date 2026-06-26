"""Unit tests for mobile copilot SSE orchestrator."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from ai.chat.agent import ChatRunResult
from ai.chat.brain import normalize_chat_mode
from ai.chat.mobile_agent import run_mobile_chat_stream
from ai.chat.mobile_stream import sse


async def _collect_events(
    messages: list[dict],
    *,
    session_id: str | None = None,
    mode: str = "temporary",
    model: str | None = None,
) -> tuple[list[dict], ChatRunResult]:
    run = ChatRunResult()
    events: list[dict] = []
    async for event in run_mobile_chat_stream(
        messages,
        session_id=session_id,
        mode=mode,
        model=model,
        result=run,
    ):
        events.append(event)
    return events, run


@pytest.mark.asyncio
async def test_portfolio_plan_emits_tool_events_and_answer(monkeypatch):
    execute_mock = AsyncMock(return_value={"accounts": [], "aggregate": {}})
    monkeypatch.setattr(
        "ai.chat.mobile_agent.plan_tools",
        lambda _text, *, model=None: [("get_portfolio_snapshot", {})],
    )
    monkeypatch.setattr("ai.chat.mobile_agent.execute_tool", execute_mock)

    class FakeLLM:
        def complete(self, _messages, *, max_tokens=1200):
            del max_tokens
            return "Your portfolio is flat."

    monkeypatch.setattr("ai.chat.mobile_agent.get_llm", lambda *, model=None: FakeLLM())

    messages = [{"role": "user", "content": "what are my positions?"}]
    events, run = await _collect_events(messages)

    assert events[0] == {"type": "start"}
    assert any(e.get("type") == "tool-call" and e.get("name") == "get_portfolio_snapshot" for e in events)
    assert any(e.get("type") == "tool-result" and e.get("name") == "get_portfolio_snapshot" for e in events)
    assert any(e.get("type") == "text-delta" for e in events)
    assert events[-1]["type"] == "done"
    assert run.assistant_text == "Your portfolio is flat."
    execute_mock.assert_awaited_once_with("get_portfolio_snapshot", {})


@pytest.mark.asyncio
async def test_propose_order_emits_proposal_without_execution(monkeypatch):
    proposal_row = {
        "id": "paper_abc123",
        "summary": "BUY 1 AAPL @ market",
        "symbol": "AAPL",
        "side": "buy",
        "qty": 1.0,
        "status": "pending_phone",
    }
    create_mock = AsyncMock(return_value=proposal_row)
    execute_mock = AsyncMock()
    submit_calls: list[object] = []

    def _submit_sentinel(*_args, **_kwargs):
        submit_calls.append(True)
        raise AssertionError("ExecutionService.submit must not be called from mobile chat")

    monkeypatch.setattr(
        "ai.chat.mobile_agent.plan_tools",
        lambda _text, *, model=None: [
            (
                "propose_order",
                {
                    "symbol": "AAPL",
                    "side": "buy",
                    "qty": 1,
                    "order_type": "market",
                },
            )
        ],
    )
    monkeypatch.setattr("trading.proposals.create_phone_proposal", create_mock)
    monkeypatch.setattr("ai.chat.mobile_agent.execute_tool", execute_mock)
    monkeypatch.setattr(
        "trading.execution_service.ExecutionService.submit",
        _submit_sentinel,
    )

    class FakeLLM:
        def complete(self, _messages, *, max_tokens=1200):
            del max_tokens
            return "I created a pending order proposal for your approval."

    monkeypatch.setattr("ai.chat.mobile_agent.get_llm", lambda *, model=None: FakeLLM())

    messages = [{"role": "user", "content": "buy 1 share of AAPL"}]
    events, run = await _collect_events(messages, session_id="sess-1")

    proposal_events = [e for e in events if e.get("type") == "proposal"]
    assert len(proposal_events) == 1
    assert proposal_events[0]["proposalId"] == "paper_abc123"
    assert proposal_events[0]["symbol"] == "AAPL"
    assert not any(e.get("type") == "tool-call" for e in events)
    create_mock.assert_awaited_once()
    create_mock.assert_awaited_with(
        {
            "symbol": "AAPL",
            "side": "buy",
            "qty": 1,
            "order_type": "market",
        },
        session_id="sess-1",
        source="mobile_chat",
    )
    execute_mock.assert_not_called()
    assert submit_calls == []
    assert run.assistant_text.startswith("I created")


@pytest.mark.asyncio
async def test_propose_order_failure_emits_error_and_still_completes(monkeypatch):
    """If create_phone_proposal raises, surface a tool-result error and STILL reach the
    terminal answer + done — never hang the client mid-stream."""
    monkeypatch.setattr(
        "ai.chat.mobile_agent.plan_tools",
        lambda _text, *, model=None: [("propose_order", {"symbol": "AAPL", "side": "buy", "qty": 1})],
    )
    boom = AsyncMock(side_effect=RuntimeError("db down"))
    monkeypatch.setattr("trading.proposals.create_phone_proposal", boom)

    class FakeLLM:
        def complete(self, _messages, *, max_tokens=1200):
            del max_tokens
            return "I couldn't create that proposal."

    monkeypatch.setattr("ai.chat.mobile_agent.get_llm", lambda *, model=None: FakeLLM())

    events, run = await _collect_events([{"role": "user", "content": "buy 1 AAPL"}])

    assert not any(e.get("type") == "proposal" for e in events)
    failed = [e for e in events if e.get("type") == "tool-result" and e.get("ok") is False]
    assert len(failed) == 1
    assert events[-1]["type"] == "done"  # stream still terminates cleanly
    assert run.assistant_text  # final answer was still produced


@pytest.mark.asyncio
async def test_empty_plan_greeting_has_no_tool_events(monkeypatch):
    monkeypatch.setattr(
        "ai.chat.mobile_agent.plan_tools",
        lambda _text, *, model=None: [],
    )

    class FakeLLM:
        def complete(self, _messages, *, max_tokens=1200):
            del max_tokens
            return "Hey there!"

    monkeypatch.setattr("ai.chat.mobile_agent.get_llm", lambda *, model=None: FakeLLM())

    messages = [{"role": "user", "content": "hi"}]
    events, run = await _collect_events(messages)

    assert events[0] == {"type": "start"}
    assert not any(e.get("type") in ("tool-call", "tool-result", "proposal") for e in events)
    assert any(e.get("type") == "text-delta" for e in events)
    assert events[-1]["type"] == "done"
    assert run.assistant_text == "Hey there!"


def test_normalize_chat_mode_temporary_and_saved():
    assert normalize_chat_mode("temporary") == "temporary"
    assert normalize_chat_mode("saved") == "saved"


def test_sse_framing():
    line = sse({"type": "start"})
    assert line == 'data: {"type": "start"}\n\n'
