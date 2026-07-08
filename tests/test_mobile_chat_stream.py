"""Hermetic tests for the mobile copilot SSE orchestrator on the unified brain.

Mobile now drives ``ai.chat.loop.run_chat_loop`` (native iterative tool-calling)
and encodes its events into the six mobile SSE events. These cover the
non-negotiables:
  * a multi-round tool run emits a tool-call/tool-result pair per round + text +
    done (proves iteration — a result feeds the next call),
  * propose_order diverts to a PENDING phone proposal and NEVER executes
    (ExecutionService.submit sentinel; create_phone_proposal is the only path),
  * create_rule stays refused (never executed),
  * memory/journal injection flows through the shared assemble_system_prompt,
  * saved-mode persistence (finalize_chat_turn) survives the brain swap.

The LLM is a deterministic ScriptedLLM (native tool-calling stub) — no live
LLM/network.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from ai.chat.agent import ChatRunResult
from ai.chat.brain import normalize_chat_mode
from ai.chat.mobile_agent import run_mobile_chat_stream
from ai.chat.mobile_stream import sse
from ai.llm.base import ToolCall, ToolCallResult


class ScriptedLLM:
    """Deterministic native-tool-calling stub: replays a queue of ToolCallResults
    for complete_tools, a fixed string for the no-tools complete()/degrade path.

    Captures the last ``complete_tools`` message list so tests can assert what the
    loop assembled (e.g. the injected memory/journal system block)."""

    provider = "scripted"

    def __init__(self, turns: list[ToolCallResult], *, final_text: str = "done") -> None:
        self._turns = list(turns)
        self._i = 0
        self._final_text = final_text
        self.last_messages: list = []

    def complete_tools(self, messages, tools, *, tool_choice="auto", max_tokens=1024, temperature=None):
        self.last_messages = list(messages)
        turn = self._turns[self._i]
        self._i += 1
        return turn

    def complete(self, messages, *, max_tokens=1024):
        self.last_messages = list(messages)
        return self._final_text


def _stub_prompt(monkeypatch):
    """Skip the real memory/journal DB reads for tool-focused tests."""

    class _P:
        system = "SYSTEM"
        memories: list = []
        journal: dict = {}

    monkeypatch.setattr(
        "ai.chat.mobile_agent.assemble_system_prompt", lambda user_text, **_: _P()
    )


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
async def test_multi_round_tool_run_emits_six_events(monkeypatch):
    """Two tool rounds -> a tool-call/tool-result pair PER round, then text + done.

    Proves iteration: the loop runs one tool, sees its result, and calls a second
    tool in the same turn — the exact thing the retired plan_tools could not do."""
    _stub_prompt(monkeypatch)
    executed: list[tuple[str, dict]] = []

    async def fake_execute(name, args):
        executed.append((name, args))
        return {"ok": True, "for": name}

    monkeypatch.setattr("ai.chat.mobile_agent.execute_tool", fake_execute)

    llm = ScriptedLLM(
        [
            ToolCallResult(
                text=None,
                tool_calls=(ToolCall(id="c1", name="get_context", arguments={"symbol": "AAPL"}),),
            ),
            ToolCallResult(
                text=None,
                tool_calls=(ToolCall(id="c2", name="get_portfolio_snapshot", arguments={}),),
            ),
            ToolCallResult(text="AAPL looks strong and your book is flat.", tool_calls=()),
        ]
    )
    monkeypatch.setattr("ai.chat.mobile_agent.get_llm", lambda *, model=None: llm)

    events, run = await _collect_events([{"role": "user", "content": "how is AAPL vs my book?"}])

    assert events[0] == {"type": "start"}
    assert events[-1]["type"] == "done"

    # one tool-call + one tool-result per REAL round, in order, id-matched
    calls = [e for e in events if e["type"] == "tool-call"]
    results = [e for e in events if e["type"] == "tool-result"]
    assert [c["name"] for c in calls] == ["get_context", "get_portfolio_snapshot"]
    assert [c["toolCallId"] for c in calls] == ["c1", "c2"]
    assert [r["toolCallId"] for r in results] == ["c1", "c2"]
    assert all(r["ok"] is True for r in results)
    assert calls[0]["args"] == {"symbol": "AAPL"}

    # real iteration: both tools actually ran, second after the first
    assert executed == [("get_context", {"symbol": "AAPL"}), ("get_portfolio_snapshot", {})]

    assert any(e["type"] == "text-delta" for e in events)
    joined = "".join(e["delta"] for e in events if e["type"] == "text-delta")
    assert joined == "AAPL looks strong and your book is flat."
    assert run.assistant_text == "AAPL looks strong and your book is flat."


@pytest.mark.asyncio
async def test_propose_order_emits_proposal_without_execution(monkeypatch):
    """propose_order -> a PENDING phone proposal + a `proposal` event, and it
    NEVER executes: ExecutionService.submit is never touched, and
    create_phone_proposal is the only path taken."""
    _stub_prompt(monkeypatch)
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

    monkeypatch.setattr("trading.proposals.create_phone_proposal", create_mock)
    monkeypatch.setattr("ai.chat.mobile_agent.execute_tool", execute_mock)
    monkeypatch.setattr("trading.execution_service.ExecutionService.submit", _submit_sentinel)

    llm = ScriptedLLM(
        [
            ToolCallResult(
                text=None,
                tool_calls=(
                    ToolCall(
                        id="c1",
                        name="propose_order",
                        arguments={"symbol": "AAPL", "side": "buy", "qty": 1, "order_type": "market"},
                    ),
                ),
            ),
            ToolCallResult(text="I created a pending order proposal for your approval.", tool_calls=()),
        ]
    )
    monkeypatch.setattr("ai.chat.mobile_agent.get_llm", lambda *, model=None: llm)

    events, run = await _collect_events(
        [{"role": "user", "content": "buy 1 share of AAPL"}], session_id="sess-1"
    )

    proposal_events = [e for e in events if e["type"] == "proposal"]
    assert len(proposal_events) == 1
    assert proposal_events[0]["proposalId"] == "paper_abc123"
    assert proposal_events[0]["symbol"] == "AAPL"
    assert proposal_events[0]["side"] == "buy"
    assert proposal_events[0]["qty"] == 1.0

    # no raw propose_order tool-call/tool-result chip leaks to the client
    assert not any(e["type"] == "tool-call" for e in events)
    assert not any(e.get("name") == "propose_order" for e in events if e["type"] == "tool-result")

    # the ONLY intake path is create_phone_proposal; the broker path is untouched
    create_mock.assert_awaited_once_with(
        {"symbol": "AAPL", "side": "buy", "qty": 1, "order_type": "market"},
        session_id="sess-1",
        source="mobile_chat",
    )
    execute_mock.assert_not_called()  # real execute_tool never runs propose_order
    assert submit_calls == []  # ExecutionService.submit never reached
    assert run.assistant_text.startswith("I created")
    assert events[-1]["type"] == "done"


@pytest.mark.asyncio
async def test_propose_order_failure_emits_error_and_still_completes(monkeypatch):
    """If create_phone_proposal fails, surface a failed tool-result and STILL
    reach the terminal answer + done — never a proposal, never a hang."""
    _stub_prompt(monkeypatch)
    boom = AsyncMock(side_effect=RuntimeError("db down"))
    monkeypatch.setattr("trading.proposals.create_phone_proposal", boom)

    llm = ScriptedLLM(
        [
            ToolCallResult(
                text=None,
                tool_calls=(ToolCall(id="c1", name="propose_order", arguments={"symbol": "AAPL", "side": "buy", "qty": 1}),),
            ),
            ToolCallResult(text="I couldn't create that proposal.", tool_calls=()),
        ]
    )
    monkeypatch.setattr("ai.chat.mobile_agent.get_llm", lambda *, model=None: llm)

    events, run = await _collect_events([{"role": "user", "content": "buy 1 AAPL"}])

    assert not any(e["type"] == "proposal" for e in events)
    failed = [e for e in events if e["type"] == "tool-result" and e["ok"] is False]
    assert len(failed) == 1 and failed[0]["name"] == "propose_order"
    assert events[-1]["type"] == "done"
    assert run.assistant_text  # final answer still produced


@pytest.mark.asyncio
async def test_create_rule_refused_on_mobile(monkeypatch):
    """create_rule arms a live alert immediately and mobile has no approval step —
    it must be refused (failed tool-result, never executed)."""
    _stub_prompt(monkeypatch)
    execute_mock = AsyncMock(return_value={"ok": True})
    monkeypatch.setattr("ai.chat.mobile_agent.execute_tool", execute_mock)

    llm = ScriptedLLM(
        [
            ToolCallResult(
                text=None,
                tool_calls=(
                    ToolCall(
                        id="c1",
                        name="create_rule",
                        arguments={"spec": {"name": "NVDA < 200d", "condition": {}}},
                    ),
                ),
            ),
            ToolCallResult(text="Arm that alert from the web cockpit.", tool_calls=()),
        ]
    )
    monkeypatch.setattr("ai.chat.mobile_agent.get_llm", lambda *, model=None: llm)

    events, run = await _collect_events(
        [{"role": "user", "content": "alert me when NVDA drops below its 200 day"}]
    )

    execute_mock.assert_not_awaited()  # create_rule never executed
    refusal = [e for e in events if e["type"] == "tool-result" and e.get("name") == "create_rule"]
    assert refusal and refusal[0]["ok"] is False
    assert not any(e["type"] == "tool-call" and e.get("name") == "create_rule" for e in events)
    assert events[-1]["type"] == "done"
    assert run.assistant_text == "Arm that alert from the web cockpit."


@pytest.mark.asyncio
async def test_mobile_injects_journal_and_memory_blocks(monkeypatch):
    """The shared assemble_system_prompt folds memory + journal into the system
    message the loop sends — same fenced blocks as web."""
    monkeypatch.setattr(
        "ai.chat.loop.turn_memory_context",
        lambda _text, **_: [{"type": "preference", "content": "Risk cap", "score": 0.9}],
    )
    monkeypatch.setattr(
        "ai.chat.loop.turn_journal_context",
        lambda _text, **_: {
            "decisions": [{"symbol": "AAPL", "decision": "buy", "outcome": "win"}],
            "ai_stats": {"n": 1, "wins": 1, "losses": 0},
        },
    )

    llm = ScriptedLLM([ToolCallResult(text="Context-aware answer.", tool_calls=())])
    monkeypatch.setattr("ai.chat.mobile_agent.get_llm", lambda *, model=None: llm)

    events, run = await _collect_events([{"role": "user", "content": "how am I doing?"}])

    assert events[-1]["type"] == "done"
    assert run.assistant_text == "Context-aware answer."
    system_content = llm.last_messages[0].content
    assert "Relevant memories" in system_content
    assert "Your track record for this turn" in system_content


@pytest.mark.asyncio
async def test_mobile_context_fetch_fail_soft(monkeypatch):
    """A memory/journal read failure never blocks the turn (fail-soft)."""

    def boom(_text, **_):
        raise RuntimeError("db down")

    monkeypatch.setattr("ai.chat.loop.turn_memory_context", boom)
    monkeypatch.setattr("ai.chat.loop.turn_journal_context", boom)

    llm = ScriptedLLM([ToolCallResult(text="Still works.", tool_calls=())])
    monkeypatch.setattr("ai.chat.mobile_agent.get_llm", lambda *, model=None: llm)

    events, run = await _collect_events([{"role": "user", "content": "hello"}])

    assert events[-1]["type"] == "done"
    assert run.assistant_text == "Still works."


@pytest.mark.asyncio
async def test_empty_user_text_greeting_has_no_tool_events(monkeypatch):
    _stub_prompt(monkeypatch)

    # get_llm must never be reached on the empty-text early return.
    def _no_llm(*_a, **_k):
        raise AssertionError("LLM must not be built for an empty turn")

    monkeypatch.setattr("ai.chat.mobile_agent.get_llm", _no_llm)

    events, run = await _collect_events([{"role": "user", "content": "   "}])

    assert events[0] == {"type": "start"}
    assert not any(e["type"] in ("tool-call", "tool-result", "proposal") for e in events)
    assert any(e["type"] == "text-delta" for e in events)
    assert events[-1]["type"] == "done"
    assert run.assistant_text == "Send a message to start chatting with your trading copilot."


@pytest.mark.asyncio
async def test_saved_mode_persists_turn(monkeypatch):
    """A saved turn still persists the user + assistant messages after the brain
    swap (finalize_chat_turn, mode='saved', sessionId round-trips)."""
    _stub_prompt(monkeypatch)
    from ai.chat.agent import finalize_chat_turn
    from db.chat_sessions import create_chat_session, get_chat_session_with_messages

    session = create_chat_session(mode="saved", title="New chat")
    sid = session["id"]

    async def fake_execute(name, args):
        return {"symbol": "AAPL"}

    monkeypatch.setattr("ai.chat.mobile_agent.execute_tool", fake_execute)
    llm = ScriptedLLM(
        [
            ToolCallResult(
                text=None,
                tool_calls=(ToolCall(id="c1", name="get_context", arguments={"symbol": "AAPL"}),),
            ),
            ToolCallResult(text="AAPL looks fine.", tool_calls=()),
        ]
    )
    monkeypatch.setattr("ai.chat.mobile_agent.get_llm", lambda *, model=None: llm)

    messages = [{"role": "user", "content": "how is AAPL"}]
    events, run = await _collect_events(messages, session_id=sid, mode="saved")

    assert events[-1] == {"type": "done", "sessionId": sid, "messageId": events[-1]["messageId"]}
    assert run.assistant_text == "AAPL looks fine."

    # the route calls finalize_chat_turn in its finally; emulate + read back.
    finalize_chat_turn(
        messages=messages,
        user_text="how is AAPL",
        assistant_text=run.assistant_text,
        session_id=sid,
        mode="saved",
        run=run,
        extract=False,
    )
    detail = get_chat_session_with_messages(sid)
    roles = [m["role"] for m in detail["messages"]]
    assert "user" in roles and "assistant" in roles
    assistant = next(m for m in detail["messages"] if m["role"] == "assistant")
    text = " ".join(p.get("text", "") for p in assistant["parts"] if p.get("type") == "text")
    assert "AAPL looks fine." in text


def test_normalize_chat_mode_temporary_and_saved():
    assert normalize_chat_mode("temporary") == "temporary"
    assert normalize_chat_mode("saved") == "saved"


def test_sse_framing():
    line = sse({"type": "start"})
    assert line == 'data: {"type": "start"}\n\n'


def test_synthesis_prompt_is_tools_free():
    # The tools-free synthesis prompt still exists for any complete()-only path.
    from ai.chat.agent import _synthesis_system_prompt, _system_prompt

    synth = _synthesis_system_prompt()
    assert "Available tools:" not in synth
    assert "call propose_order" not in synth
    assert "call create_rule" not in synth
    assert "<function_calls>" in synth  # explicitly forbids emitting the syntax
    assert "Available tools:" in _system_prompt()


def test_final_messages_use_the_tools_free_prompt():
    from ai.chat.agent import _build_final_messages

    msgs = _build_final_messages(
        [],
        user_text="how did I do today?",
        tool_results=[{"tool": "get_portfolio_snapshot", "result": {}}],
    )
    system = next(m for m in msgs if m.role == "system")
    assert "Available tools:" not in system.content
    assert "<function_calls>" in system.content
