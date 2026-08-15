"""The one chat brain: a provider-agnostic, iterative, act->observe->act loop.

Phase 1b — this is the single conversational loop the web (via the Python SSE
proxy) runs. It drives a provider's native tool-calling (``complete_tools``)
against the shared ``TOOL_DEFINITIONS`` registry, yielding **wire-agnostic**
events (text, tool call, tool result, approval request). A thin per-surface
encoder turns those events into the surface's stream framing — the web encoder
emits the AI-SDK UIMessageStream so assistant-ui renders identically to the old
in-route TS loop.

Safety invariants baked in here (not left to a surface):
- Approval tools (``propose_order``/``create_rule``) are NEVER executed by the
  loop. They surface as an ``ApprovalRequest`` and the turn pauses. Execution
  happens only on the human's approval, on the resume turn, in the surface.
- Memory + journal blocks are injected ONCE at assembly (web parity:
  ``injectJournal=true``), through the same fenced ``context.py`` helpers every
  surface uses, so the provenance-chip / pending-proposal fences apply here too.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from ai.chat.agent import _system_prompt
from ai.chat.alert_intent import alert_intent_prompt_block
from ai.chat.context import (
    format_journal_context_block,
    format_memory_context_block,
    turn_journal_context,
    turn_memory_context,
)
from ai.chat.intent import order_intent_prompt_block
from ai.chat.tool_specs import APPROVAL_TOOLS, chat_tool_specs
from ai.llm.base import (
    LLMMessage,
    StreamFinish,
    StreamTextDelta,
    StreamToolCall,
    ToolCall,
    ToolSpec,
)
from ai.perception.resources import fit_to_cap

logger = logging.getLogger(__name__)

# Mirrors the web route's stepCountIs(6) and tool_loop.DEFAULT_MAX_ITERATIONS.
MAX_ITERATIONS = 6

# One tool result must not eat the turn. The book surfaces capped from the start; the
# web/mobile chat — the surface the user actually talks to — never did, and it showed:
# a live DeepSeek turn on 2026-08-11 pulled 39,367 + 31,199 chars from two `query`
# calls, hit finish_reason=length mid-stream, and answered the question with one
# sentence. Capping here rather than in each executor covers web, mobile and the book
# drivers at the one place a tool result enters the conversation.
TOOL_RESULT_CHAR_CAP = 20_000

ToolExecutor = Callable[[str, dict[str, Any]], Awaitable[Any]]


# --- Wire-agnostic loop events -------------------------------------------------
@dataclass
class TextChunk:
    """Final (or interim) assistant text. Encoders chunk it into text-deltas."""

    text: str


@dataclass
class TextDelta:
    """A token-live fragment of assistant text (streaming providers only).

    The encoder opens a text block on the first delta of a segment, forwards
    each fragment as a real-time text-delta, and closes it on ``TextEnd``.
    """

    text: str


@dataclass
class TextEnd:
    """Marks the end of a streamed text segment so the encoder closes its block."""


@dataclass
class ToolCallStarted:
    tool_call_id: str
    tool_name: str
    input: dict[str, Any]


@dataclass
class ToolResult:
    tool_call_id: str
    tool_name: str
    input: dict[str, Any]
    output: Any
    ok: bool


@dataclass
class ApprovalRequest:
    """An approval tool the model asked for — the turn pauses here; the surface
    renders an Approve/Reject card and executes only on approval."""

    tool_call_id: str
    tool_name: str
    input: dict[str, Any]


@dataclass
class LoopError:
    message: str


LoopEvent = (
    TextChunk | TextDelta | TextEnd | ToolCallStarted | ToolResult | ApprovalRequest | LoopError
)


@dataclass
class AssembledPrompt:
    system: str
    memories: list[dict[str, Any]] = field(default_factory=list)
    journal: dict[str, Any] = field(default_factory=dict)


def assemble_system_prompt(
    user_text: str,
    *,
    inject_memories: bool = True,
    inject_journal: bool = True,
) -> AssembledPrompt:
    """Build the turn's system prompt once (memories + journal + intent blocks).

    Fail-soft: a failure in the memory/journal retrieval never blocks the chat —
    the turn proceeds with the base prompt.

    The two intent blocks are per-turn supplements the retired TS route had and
    this loop silently lost when it took over: they carry the options long-only
    rules and the MOO limitation on an order turn, the valid metric/operator
    vocabulary for ``create_rule`` on an alert turn, and — in both — the "do NOT
    narrate the tool or write fake calls like [Calling propose_order...]"
    instruction. Without them the model has to invent metric names and is free
    to describe a tool call instead of making one.
    """
    memories: list[dict[str, Any]] = []
    journal: dict[str, Any] = {}
    if inject_memories:
        try:
            memories = turn_memory_context(user_text)
        except Exception:
            logger.debug("turn_memory_context failed", exc_info=True)
    if inject_journal:
        try:
            journal = turn_journal_context(user_text)
        except Exception:
            logger.debug("turn_journal_context failed", exc_info=True)
    system = (
        _system_prompt()
        + order_intent_prompt_block(user_text)
        + alert_intent_prompt_block(user_text)
        + format_memory_context_block(memories)
        + format_journal_context_block(journal)
    )
    return AssembledPrompt(system=system, memories=memories, journal=journal)


def _assistant_toolcall_message(text: str, calls: tuple[ToolCall, ...]) -> LLMMessage:
    return LLMMessage(role="assistant", content=text or "", tool_calls=calls)


def _tool_result_message(call: ToolCall, output: Any) -> LLMMessage:
    if isinstance(output, str):
        content = output
    else:
        content = json.dumps(output, default=str)
    return LLMMessage(
        role="tool",
        content=content,
        tool_call_id=call.id,
        name=call.name,
    )


async def _model_turn(
    llm: Any,
    convo: list[LLMMessage],
    tools: list[ToolSpec],
    *,
    max_tokens: int,
    holder: dict[str, Any],
) -> AsyncIterator[LoopEvent]:
    """Run one provider turn, live-streaming text when supported.

    Providers exposing ``stream_tools`` surface text token-live (yielding
    ``TextDelta`` per fragment, then ``TextEnd`` if any text opened) while tool
    calls arrive fully reassembled; others make one blocking ``complete_tools``
    call and stream nothing here. Either way the turn's result is written to
    ``holder`` (``text`` preamble/answer, ``calls`` tuple, ``streamed`` flag) for
    the driver to act on — the tool round-trip is identical for both paths.
    """
    stream_tools = getattr(llm, "stream_tools", None)
    if callable(stream_tools):
        text_parts: list[str] = []
        calls: list[ToolCall] = []
        opened = False
        async for ev in stream_tools(
            convo, tools, tool_choice="auto", max_tokens=max_tokens
        ):
            if isinstance(ev, StreamTextDelta):
                text_parts.append(ev.text)
                opened = True
                yield TextDelta(ev.text)
            elif isinstance(ev, StreamToolCall):
                calls.append(ev.call)
            elif isinstance(ev, StreamFinish):
                pass  # finish_reason logging lives in the provider
        if opened:
            yield TextEnd()
        holder["text"] = "".join(text_parts)
        holder["calls"] = tuple(calls)
        holder["streamed"] = opened
        return

    result = await asyncio.to_thread(
        llm.complete_tools,
        convo,
        tools,
        tool_choice="auto",
        max_tokens=max_tokens,
    )
    holder["text"] = result.text or ""
    holder["calls"] = tuple(result.tool_calls)
    holder["streamed"] = False


async def run_chat_loop(
    llm: Any,
    convo: list[LLMMessage],
    tools: list[ToolSpec],
    *,
    execute: ToolExecutor,
    approval_tools: frozenset[str] = APPROVAL_TOOLS,
    max_iterations: int = MAX_ITERATIONS,
    # Chat stays at 8000 (raised 1200 -> 4000 -> 8000 on measured truncations).
    # The Deep Seek *book* passes BOOK_MAX_TOKENS (384k, V4 output ceiling)
    # explicitly — do not "sync" this default up to the book, and do not drop
    # the book back to 8000; that clamp ate the nightly report (07-31..08-14).
    # Applies per model turn; tool-call turns are tiny, so it only ever bites
    # the final answer. Central on purpose: mobile INHERITS this default.
    max_tokens: int = 8000,
) -> AsyncIterator[LoopEvent]:
    """Drive one chat turn to a final text answer (or an approval pause).

    ``convo`` is the full LLM message list (system + history + user), mutated
    in place as the loop appends assistant/tool turns. ``execute`` runs a
    non-approval tool. On an approval tool the loop yields ``ApprovalRequest``
    and returns without executing — the pause.

    Providers without native tool-calling (``ollama``) degrade to a single
    no-tools completion (parity with the retired legacy path, but WITH memory/
    journal already in the system prompt) so the endpoint never 500s for them.
    """
    if not callable(getattr(llm, "complete_tools", None)):
        text = await _degrade_no_tools(llm, convo, max_tokens=max_tokens)
        yield TextChunk(text)
        return

    for _iteration in range(max_iterations):
        holder: dict[str, Any] = {}
        text_open = False
        try:
            async for event in _model_turn(llm, convo, tools, max_tokens=max_tokens, holder=holder):
                if isinstance(event, TextDelta):
                    text_open = True
                elif isinstance(event, TextEnd):
                    text_open = False
                yield event
        except Exception as exc:
            logger.exception("model turn failed")
            if text_open:  # close the partially-streamed block before erroring
                yield TextEnd()
            yield LoopError(str(exc))
            return

        text = holder["text"]
        calls = holder["calls"]
        streamed = holder["streamed"]

        if not calls:
            # Final answer. Streamed providers already emitted live deltas + a
            # TextEnd; non-streaming (or empty) turns emit the whole block here.
            if not streamed:
                yield TextChunk(text)
            return

        convo.append(_assistant_toolcall_message(text, calls))

        for call in calls:
            if call.name in approval_tools:
                # Pause: emit the input + an approval request, execute nothing.
                yield ToolCallStarted(call.id, call.name, call.arguments)
                yield ApprovalRequest(call.id, call.name, call.arguments)
                return
            yield ToolCallStarted(call.id, call.name, call.arguments)
            try:
                output = fit_to_cap(await execute(call.name, call.arguments),
                                    cap=TOOL_RESULT_CHAR_CAP)
            except Exception as exc:
                logger.exception("tool %s failed", call.name)
                output = {"error": str(exc)}
            ok = not (isinstance(output, dict) and output.get("error"))
            yield ToolResult(call.id, call.name, call.arguments, output, ok)
            convo.append(_tool_result_message(call, output))

    # Iteration cap without a final answer: ask once more, no tools, for a wrap-up
    # (mirrors the web loop settling after stepCountIs; never leave the user hanging).
    text = await _degrade_no_tools(llm, convo, max_tokens=max_tokens)
    yield TextChunk(text or "I wasn't able to finish that — please try rephrasing.")


async def _degrade_no_tools(llm: Any, convo: list[LLMMessage], *, max_tokens: int) -> str:
    try:
        return await asyncio.to_thread(llm.complete, convo, max_tokens=max_tokens)
    except Exception as exc:
        logger.exception("no-tools completion failed")
        return f"Unable to reach the language model: {exc}"


def build_tools(enabled: set[str] | None = None) -> list[ToolSpec]:
    """The turn's tool specs (subset = web toolbar parity)."""
    return chat_tool_specs(enabled)
