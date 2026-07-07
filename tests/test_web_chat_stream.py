"""Hermetic tests for the one Python chat brain on the web (UIMessageStream).

Covers the Phase-1b non-negotiables:
  * tool round-trip through the loop/endpoint (scripted + MockLLM),
  * stream-framing golden (a recorded ordered chunk transcript),
  * proposal-card part-shape regression + propose-only safety (never executes),
  * approval resume (execute on approve, deny on reject),
  * memory-injection parity across surfaces,
  * saved-mode session persistence (tool/text parts survive a reload).
"""

from __future__ import annotations

import asyncio
import json

import pytest

from ai.chat.agent import ChatRunResult
from ai.chat.web_agent import run_web_chat_stream
from ai.llm.base import (
    StreamFinish,
    StreamTextDelta,
    StreamToolCall,
    ToolCall,
    ToolCallResult,
)


class ScriptedLLM:
    """Deterministic native-tool-calling stub: replays a queue of
    ToolCallResults for complete_tools, and a fixed string for complete."""

    provider = "scripted"

    def __init__(self, turns: list[ToolCallResult], *, final_text: str = "done") -> None:
        self._turns = list(turns)
        self._i = 0
        self._final_text = final_text

    def complete_tools(self, messages, tools, *, tool_choice="auto", max_tokens=1024, temperature=None):
        turn = self._turns[self._i]
        self._i += 1
        return turn

    def complete(self, messages, *, max_tokens=1024):
        return self._final_text


class StreamingScriptedLLM:
    """Native streaming stub: replays a queue of per-turn StreamEvent lists via
    stream_tools. complete_tools must never be called (the loop must prefer the
    streaming path); complete backs the iteration-cap wrap-up only."""

    provider = "streaming-scripted"

    def __init__(self, turns: list[list], *, final_text: str = "done") -> None:
        self._turns = list(turns)
        self._i = 0
        self._final_text = final_text

    def complete_tools(self, *a, **k):
        raise AssertionError("streaming path must use stream_tools, not complete_tools")

    def complete(self, messages, *, max_tokens=1024):
        return self._final_text

    async def stream_tools(self, messages, tools, *, tool_choice="auto", max_tokens=1024, temperature=None):
        turn = self._turns[self._i]
        self._i += 1
        for ev in turn:
            await asyncio.sleep(0)
            if isinstance(ev, Exception):
                raise ev
            yield ev


def _events(lines: list[str]) -> list[dict]:
    out: list[dict] = []
    for line in lines:
        for raw in line.split("\n"):
            raw = raw.strip()
            if not raw.startswith("data:"):
                continue
            payload = raw[len("data:") :].strip()
            if not payload or payload == "[DONE]":
                continue
            out.append(json.loads(payload))
    return out


async def _collect(messages, *, llm, monkeypatch, execute=None, **kwargs):
    monkeypatch.setattr("ai.chat.web_agent.get_llm", lambda *, model=None: llm)
    monkeypatch.setattr(
        "ai.chat.web_agent.assemble_system_prompt",
        lambda user_text, **_: _StubPrompt(),
    )
    if execute is not None:
        monkeypatch.setattr("ai.chat.web_agent.execute_tool", execute)
    run = ChatRunResult()
    lines: list[str] = []
    async for chunk in run_web_chat_stream(messages, result=run, **kwargs):
        lines.append(chunk)
    return _events(lines), run


class _StubPrompt:
    system = "SYSTEM"
    memories: list = []
    journal: dict = {}


def _user(text: str) -> dict:
    return {"role": "user", "parts": [{"type": "text", "text": text}]}


@pytest.mark.asyncio
async def test_tool_round_trip_events_and_parts(monkeypatch):
    """A non-approval tool executes in-loop and streams input->output->text."""
    calls: list[tuple[str, dict]] = []

    async def fake_execute(name, args):
        calls.append((name, args))
        return {"symbol": "AAPL", "quote": 123.0}

    llm = ScriptedLLM(
        [
            ToolCallResult(
                text=None,
                tool_calls=(ToolCall(id="c1", name="get_context", arguments={"symbol": "AAPL"}),),
            ),
            ToolCallResult(text="AAPL is at 123.", tool_calls=()),
        ]
    )
    events, run = await _collect(
        [_user("how is AAPL")], llm=llm, monkeypatch=monkeypatch, execute=fake_execute
    )

    types = [e["type"] for e in events]
    assert "tool-input-start" in types
    assert "tool-input-available" in types
    assert "tool-output-available" in types
    assert "text-delta" in types
    # No dynamic flag on static tools (custom cards match tool-<name> parts).
    tia = next(e for e in events if e["type"] == "tool-input-available")
    assert tia["toolName"] == "get_context"
    assert "dynamic" not in tia
    assert tia["input"] == {"symbol": "AAPL"}
    assert calls == [("get_context", {"symbol": "AAPL"})]
    assert run.assistant_text == "AAPL is at 123."

    parts = run.assistant_message["parts"]
    tool_parts = [p for p in parts if p["type"] == "tool-get_context"]
    assert tool_parts and tool_parts[0]["state"] == "output-available"
    assert tool_parts[0]["input"] == {"symbol": "AAPL"}
    assert tool_parts[0]["output"] == {"symbol": "AAPL", "quote": 123.0}
    assert any(p["type"] == "text" for p in parts)


@pytest.mark.asyncio
async def test_stream_framing_golden(monkeypatch):
    """Locks the exact ordered chunk transcript for a one-tool turn."""

    async def fake_execute(name, args):
        return {"ok": True}

    llm = ScriptedLLM(
        [
            ToolCallResult(
                text=None,
                tool_calls=(ToolCall(id="c1", name="get_agent_status", arguments={}),),
            ),
            ToolCallResult(text="All running.", tool_calls=()),
        ]
    )
    events, _ = await _collect(
        [_user("status")], llm=llm, monkeypatch=monkeypatch, execute=fake_execute
    )
    assert [e["type"] for e in events] == [
        "start",
        "start-step",
        "tool-input-start",
        "tool-input-available",
        "tool-output-available",
        "text-start",
        "text-delta",
        "text-end",
        "finish-step",
        "finish",
    ]


@pytest.mark.asyncio
async def test_propose_order_pauses_for_approval_and_never_executes(monkeypatch):
    """The make-or-break: propose_order surfaces an approval request and the
    loop PAUSES — nothing executes, matching the founder's Approve/Reject card."""
    executed: list = []

    async def fake_execute(name, args):
        executed.append(name)
        return {"status": "should-not-happen"}

    llm = ScriptedLLM(
        [
            ToolCallResult(
                text=None,
                tool_calls=(
                    ToolCall(
                        id="c1",
                        name="propose_order",
                        arguments={"symbol": "AAPL", "side": "buy", "qty": 1},
                    ),
                ),
            )
        ]
    )
    events, run = await _collect(
        [_user("buy 1 AAPL")], llm=llm, monkeypatch=monkeypatch, execute=fake_execute
    )

    approval = next(e for e in events if e["type"] == "tool-approval-request")
    assert approval["toolCallId"] == "c1"
    assert isinstance(approval["approvalId"], str) and approval["approvalId"]
    # exact field set — client validates with strictObject
    assert set(approval) == {"type", "approvalId", "toolCallId"}
    assert not any(e["type"] == "tool-output-available" for e in events)
    assert executed == []  # propose only — NEVER executes without approval

    part = next(p for p in run.assistant_message["parts"] if p["type"] == "tool-propose_order")
    assert part["state"] == "approval-requested"
    assert part["input"] == {"symbol": "AAPL", "side": "buy", "qty": 1}
    assert part["approval"]["id"] == approval["approvalId"]


def _approval_resume_messages(
    *, approval_id: str, approved: bool = True, qty: float = 1
) -> list[dict]:
    return [
        _user("buy 1 AAPL"),
        {
            "role": "assistant",
            "parts": [
                {
                    "type": "tool-propose_order",
                    "toolCallId": "c1",
                    "input": {"symbol": "AAPL", "side": "buy", "qty": qty},
                    "state": "approval-responded",
                    "approval": {"id": approval_id, "approved": approved},
                }
            ],
        },
    ]


def _seed_ledger(approval_id: str, *, qty: float = 1) -> None:
    from db.chat_approvals import record_approval_request

    record_approval_request(
        approval_id,
        tool_call_id="c1",
        tool_name="propose_order",
        tool_input={"symbol": "AAPL", "side": "buy", "qty": qty},
    )


@pytest.mark.asyncio
async def test_resume_approved_executes_and_emits_output(monkeypatch):
    executed: list[tuple[str, dict]] = []

    async def fake_execute(name, args):
        executed.append((name, args))
        return {"status": "submitted", "proposal_id": "p1", "proposal": args}

    llm = ScriptedLLM([ToolCallResult(text="Order placed.", tool_calls=())])
    _seed_ledger("apr_ok")
    messages = _approval_resume_messages(approval_id="apr_ok")
    events, run = await _collect(
        messages, llm=llm, monkeypatch=monkeypatch, execute=fake_execute
    )

    assert executed == [("propose_order", {"symbol": "AAPL", "side": "buy", "qty": 1})]
    out = next(e for e in events if e["type"] == "tool-output-available")
    assert out["toolCallId"] == "c1"
    assert out["output"]["status"] == "submitted"
    part = next(p for p in run.assistant_message["parts"] if p["type"] == "tool-propose_order")
    assert part["state"] == "output-available"
    assert part["approval"] == {"id": "apr_ok", "approved": True}
    assert run.assistant_text == "Order placed."


@pytest.mark.asyncio
async def test_resume_rejected_denies_without_execution(monkeypatch):
    executed: list = []

    async def fake_execute(name, args):
        executed.append(name)
        return {}

    llm = ScriptedLLM([ToolCallResult(text="No problem, cancelled.", tool_calls=())])
    _seed_ledger("apr_rej")
    messages = _approval_resume_messages(approval_id="apr_rej", approved=False)
    events, run = await _collect(
        messages, llm=llm, monkeypatch=monkeypatch, execute=fake_execute
    )

    assert executed == []  # rejected — never runs
    denied = next(e for e in events if e["type"] == "tool-output-denied")
    assert set(denied) == {"type", "toolCallId"}  # no toolName on this chunk
    assert denied["toolCallId"] == "c1"
    part = next(p for p in run.assistant_message["parts"] if p["type"] == "tool-propose_order")
    assert part["state"] == "output-denied"


@pytest.mark.asyncio
async def test_replay_same_approval_executes_exactly_once(monkeypatch):
    """The same approval-responded turn POSTed twice → one execution, then denial."""
    executed: list = []

    async def fake_execute(name, args):
        executed.append(name)
        return {"status": "submitted"}

    _seed_ledger("apr_replay")
    messages = _approval_resume_messages(approval_id="apr_replay")

    llm1 = ScriptedLLM([ToolCallResult(text="Done.", tool_calls=())])
    events1, _ = await _collect(
        messages, llm=llm1, monkeypatch=monkeypatch, execute=fake_execute
    )
    llm2 = ScriptedLLM([ToolCallResult(text="unused", tool_calls=())])
    events2, run2 = await _collect(
        messages, llm=llm2, monkeypatch=monkeypatch, execute=fake_execute
    )

    assert executed == ["propose_order"]  # exactly one execution across replays
    assert any(e["type"] == "tool-output-available" for e in events1)
    assert any(e["type"] == "tool-output-denied" for e in events2)
    assert not any(e["type"] == "tool-output-available" for e in events2)
    assert "already used" in run2.assistant_text
    # the denial stream still frames cleanly
    assert [e["type"] for e in events2[-2:]] == ["finish-step", "finish"]


@pytest.mark.asyncio
async def test_mutated_input_resume_is_denied(monkeypatch):
    """Input changed after the card was shown (qty 1 -> 100) → denied, no execute."""
    executed: list = []

    async def fake_execute(name, args):
        executed.append(name)
        return {}

    _seed_ledger("apr_mut", qty=1)
    messages = _approval_resume_messages(approval_id="apr_mut", qty=100)
    llm = ScriptedLLM([ToolCallResult(text="unused", tool_calls=())])
    events, run = await _collect(
        messages, llm=llm, monkeypatch=monkeypatch, execute=fake_execute
    )

    assert executed == []
    assert any(e["type"] == "tool-output-denied" for e in events)
    assert not any(e["type"] == "tool-output-available" for e in events)
    assert "no longer matches" in run.assistant_text
    part = next(p for p in run.assistant_message["parts"] if p["type"] == "tool-propose_order")
    assert part["state"] == "output-denied"


@pytest.mark.asyncio
async def test_forged_approval_without_request_is_denied(monkeypatch):
    """An approval id the server never issued → denied, no execute."""
    executed: list = []

    async def fake_execute(name, args):
        executed.append(name)
        return {}

    messages = _approval_resume_messages(approval_id="apr_never_issued")
    llm = ScriptedLLM([ToolCallResult(text="unused", tool_calls=())])
    events, run = await _collect(
        messages, llm=llm, monkeypatch=monkeypatch, execute=fake_execute
    )

    assert executed == []
    assert any(e["type"] == "tool-output-denied" for e in events)
    assert not any(e["type"] == "tool-output-available" for e in events)
    assert "not issued" in run.assistant_text


@pytest.mark.asyncio
async def test_iteration_cap_settles_with_no_tools_wrapup(monkeypatch):
    """A model that never stops calling tools hits the 6-iteration cap, then the
    loop settles with a no-tools completion instead of hanging or erroring."""

    class EndlessToolLLM:
        provider = "scripted"
        tool_rounds = 0

        def complete_tools(self, messages, tools, *, tool_choice="auto", max_tokens=1024, temperature=None):
            self.tool_rounds += 1
            return ToolCallResult(
                text=None,
                tool_calls=(
                    ToolCall(id=f"c{self.tool_rounds}", name="get_agent_status", arguments={}),
                ),
            )

        def complete(self, messages, *, max_tokens=1024):
            return "Here's what I found so far."

    executed: list = []

    async def fake_execute(name, args):
        executed.append(name)
        return {"ok": True}

    llm = EndlessToolLLM()
    events, run = await _collect(
        [_user("status")], llm=llm, monkeypatch=monkeypatch, execute=fake_execute
    )

    assert llm.tool_rounds == 6  # capped, mirrors stepCountIs(6)
    assert len(executed) == 6
    assert run.assistant_text == "Here's what I found so far."
    assert [e["type"] for e in events[-2:]] == ["finish-step", "finish"]


@pytest.mark.asyncio
async def test_execute_raising_mid_loop_emits_error_output_and_finishes(monkeypatch):
    """A tool crash surfaces as an error output chunk; the loop and stream go on
    to a clean final answer — never a hung or truncated stream."""

    async def fake_execute(name, args):
        raise RuntimeError("db down")

    llm = ScriptedLLM(
        [
            ToolCallResult(
                text=None,
                tool_calls=(ToolCall(id="c1", name="get_agent_status", arguments={}),),
            ),
            ToolCallResult(text="Something went wrong reading status.", tool_calls=()),
        ]
    )
    events, run = await _collect(
        [_user("status")], llm=llm, monkeypatch=monkeypatch, execute=fake_execute
    )

    out = next(e for e in events if e["type"] == "tool-output-available")
    assert out["output"] == {"error": "db down"}
    assert run.assistant_text == "Something went wrong reading status."
    assert [e["type"] for e in events[-2:]] == ["finish-step", "finish"]


@pytest.mark.asyncio
async def test_streaming_text_before_and_after_a_tool_round(monkeypatch):
    """Live streaming across two turns: preamble text (deltas) -> tool round ->
    final text (deltas). Each fragment surfaces as its own text-delta the moment
    it arrives, and both text segments persist as separate text parts."""
    calls: list[tuple[str, dict]] = []

    async def fake_execute(name, args):
        calls.append((name, args))
        return {"symbol": "AAPL", "quote": 123.0}

    llm = StreamingScriptedLLM(
        [
            [
                StreamTextDelta("Checking "),
                StreamTextDelta("AAPL. "),
                StreamToolCall(ToolCall(id="c1", name="get_context", arguments={"symbol": "AAPL"})),
                StreamFinish(finish_reason="tool_calls"),
            ],
            [
                StreamTextDelta("AAPL "),
                StreamTextDelta("is 123."),
                StreamFinish(finish_reason="stop"),
            ],
        ]
    )
    events, run = await _collect(
        [_user("how is AAPL")], llm=llm, monkeypatch=monkeypatch, execute=fake_execute
    )

    assert [e["type"] for e in events] == [
        "start",
        "start-step",
        "text-start",
        "text-delta",
        "text-delta",
        "text-end",
        "tool-input-start",
        "tool-input-available",
        "tool-output-available",
        "text-start",
        "text-delta",
        "text-delta",
        "text-end",
        "finish-step",
        "finish",
    ]
    # deltas arrive raw (token-live), not re-chunked post-hoc
    deltas = [e["delta"] for e in events if e["type"] == "text-delta"]
    assert deltas == ["Checking ", "AAPL. ", "AAPL ", "is 123."]
    assert calls == [("get_context", {"symbol": "AAPL"})]
    assert run.assistant_text == "Checking AAPL. AAPL is 123."

    parts = run.assistant_message["parts"]
    text_parts = [p for p in parts if p["type"] == "text"]
    assert [p["text"] for p in text_parts] == ["Checking AAPL. ", "AAPL is 123."]
    tool_parts = [p for p in parts if p["type"] == "tool-get_context"]
    assert tool_parts and tool_parts[0]["state"] == "output-available"


@pytest.mark.asyncio
async def test_streaming_midstream_error_terminates_cleanly(monkeypatch):
    """A provider disconnect after partial text closes the open text block, emits
    an error chunk, and still finishes the stream — never a hang."""

    async def fake_execute(name, args):
        return {}

    llm = StreamingScriptedLLM(
        [[StreamTextDelta("Partial ans"), RuntimeError("stream disconnected")]]
    )
    events, run = await _collect(
        [_user("hi")], llm=llm, monkeypatch=monkeypatch, execute=fake_execute
    )

    assert [e["type"] for e in events] == [
        "start",
        "start-step",
        "text-start",
        "text-delta",
        "text-end",
        "error",
        "finish-step",
        "finish",
    ]
    assert next(e for e in events if e["type"] == "text-delta")["delta"] == "Partial ans"
    assert "stream disconnected" in next(e for e in events if e["type"] == "error")["errorText"]
    assert run.assistant_text == "Partial ans"  # partial text preserved


@pytest.mark.asyncio
async def test_streaming_approval_pause_framing_matches_nonstreaming(monkeypatch):
    """Golden: a propose_order call frames byte-identically whether the model's
    output streamed or not — same chunk sequence, same approval part, and it
    still PAUSES without executing."""
    executed: list = []

    async def fake_execute(name, args):
        executed.append(name)
        return {"status": "should-not-happen"}

    propose = ToolCall(
        id="c1", name="propose_order", arguments={"symbol": "AAPL", "side": "buy", "qty": 1}
    )

    ns_llm = ScriptedLLM([ToolCallResult(text=None, tool_calls=(propose,))])
    ns_events, ns_run = await _collect(
        [_user("buy 1 AAPL")], llm=ns_llm, monkeypatch=monkeypatch, execute=fake_execute
    )

    st_llm = StreamingScriptedLLM(
        [[StreamToolCall(propose), StreamFinish(finish_reason="tool_calls")]]
    )
    st_events, st_run = await _collect(
        [_user("buy 1 AAPL")], llm=st_llm, monkeypatch=monkeypatch, execute=fake_execute
    )

    # Identical chunk-type sequence (approval framing unchanged under streaming).
    assert [e["type"] for e in st_events] == [e["type"] for e in ns_events]
    # Neither path executed the proposal.
    assert executed == []
    for events in (ns_events, st_events):
        approval = next(e for e in events if e["type"] == "tool-approval-request")
        assert set(approval) == {"type", "approvalId", "toolCallId"}
        assert approval["toolCallId"] == "c1"
        assert not any(e["type"] == "tool-output-available" for e in events)
    # Identical persisted approval part (bar the random approval id).
    ns_part = next(p for p in ns_run.assistant_message["parts"] if p["type"] == "tool-propose_order")
    st_part = next(p for p in st_run.assistant_message["parts"] if p["type"] == "tool-propose_order")
    assert ns_part["state"] == st_part["state"] == "approval-requested"
    assert ns_part["input"] == st_part["input"] == {"symbol": "AAPL", "side": "buy", "qty": 1}


def test_mockllm_endpoint_round_trip(monkeypatch, mutation_client):
    """MockLLM through the real FastAPI /api/chat endpoint: text stream + [DONE]."""
    from ai.llm.mock import MockLLM

    monkeypatch.setattr("ai.chat.web_agent.get_llm", lambda *, model=None: MockLLM())

    resp = mutation_client.post(
        "/api/chat",
        json={"messages": [_user("hello there")], "mode": "temporary"},
    )
    assert resp.status_code == 200
    assert resp.headers["x-vercel-ai-ui-message-stream"] == "v1"
    body = resp.text
    assert '"type": "start"' in body
    assert '"type": "text-delta"' in body
    assert '"type": "finish"' in body
    assert "data: [DONE]" in body


def test_memory_injection_parity_across_surfaces(monkeypatch):
    """Web (assemble_system_prompt) and mobile both render the SAME provenance-
    fenced memory + journal blocks from the same context helpers."""
    from ai.chat.context import (
        format_journal_context_block,
        format_memory_context_block,
    )
    from ai.chat.loop import assemble_system_prompt

    mems = [{"type": "preference", "content": "cap risk", "authority": "user_authored", "score": 0.9}]
    journal = {"decisions": [], "ai_stats": {"n": 0}, "user_profile": "10 trades"}
    monkeypatch.setattr("ai.chat.loop.turn_memory_context", lambda _t, **_: mems)
    monkeypatch.setattr("ai.chat.loop.turn_journal_context", lambda _t, **_: journal)

    assembled = assemble_system_prompt("how am I doing")
    mem_block = format_memory_context_block(mems)
    jrn_block = format_journal_context_block(journal)

    assert mem_block and mem_block in assembled.system
    assert jrn_block and jrn_block in assembled.system
    # Provenance chip present (founder rule vs agent note) — the injection fence.
    assert "founder-authored" in assembled.system


@pytest.mark.asyncio
async def test_saved_mode_session_persistence(monkeypatch):
    """A saved turn persists the user message + the assistant's tool/text parts,
    and they reload from chat_sessions (thread history must not regress)."""
    from ai.chat.agent import finalize_chat_turn
    from db.chat_sessions import create_chat_session, get_chat_session_with_messages

    session = create_chat_session(mode="saved", title="New chat")
    sid = session["id"]

    async def fake_execute(name, args):
        return {"symbol": "AAPL"}

    llm = ScriptedLLM(
        [
            ToolCallResult(
                text=None,
                tool_calls=(ToolCall(id="c1", name="get_context", arguments={"symbol": "AAPL"}),),
            ),
            ToolCallResult(text="AAPL looks fine.", tool_calls=()),
        ]
    )

    _events_out, run = await _collect(
        [_user("how is AAPL")],
        llm=llm,
        monkeypatch=monkeypatch,
        execute=fake_execute,
        session_id=sid,
        mode="saved",
    )
    finalize_chat_turn(
        messages=[_user("how is AAPL")],
        user_text="how is AAPL",
        assistant_text=run.assistant_text,
        session_id=sid,
        mode="saved",
        run=run,
        assistant_message=run.assistant_message,
        extract=False,
    )

    detail = get_chat_session_with_messages(sid)
    roles = [m["role"] for m in detail["messages"]]
    assert "user" in roles and "assistant" in roles
    assistant = next(m for m in detail["messages"] if m["role"] == "assistant")
    part_types = {p["type"] for p in assistant["parts"]}
    assert "tool-get_context" in part_types
    assert "text" in part_types
