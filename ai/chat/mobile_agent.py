"""Mobile copilot chat orchestrator — the unified iterative brain, six-event SSE.

Mobile now runs the SAME engine as web: ``ai.chat.loop.run_chat_loop`` drives a
provider's native, iterative tool-calling (act -> observe -> act, up to
``MAX_ITERATIONS`` rounds) so the copilot can see a tool result and decide the
next call in the same turn — the thing the retired ``plan_tools`` one-shot
planner could not do. A thin encoder here translates the loop's wire-agnostic
events into the six mobile SSE events (``start`` / ``text-delta`` / ``tool-call``
/ ``tool-result`` / ``proposal`` / ``error`` / ``done``) — the exact contract the
RN client already parses (``References/MOBILE_CHAT_BRIEF.md``). Mobile must NOT
emit web's AI-SDK ``UIMessageStream``; the RN parser only understands these six.

Safety on this surface (unchanged from the pre-unification path, and mission-
critical):
- ``propose_order`` NEVER executes here. The loop's ``execute`` callback diverts
  it to ``trading.proposals.create_phone_proposal`` (a PENDING-only phone
  proposal for the biometric Approve/Reject flow) and the encoder emits a
  ``proposal`` event. It never reaches the real ``execute_tool`` / the guarded
  ``ExecutionService.submit`` chokepoint. This is the phone-approval unlock.
- ``create_rule`` (and any other ``APPROVAL_TOOLS`` member) is REFUSED — mobile
  has no in-chat approval step, so arming a live rule from here is blocked; the
  model tells the user to arm it from the web cockpit.

Because both diversions live in the ``execute`` callback, the loop is run with an
empty ``approval_tools`` set: it routes approval tools to ``execute`` (where we
intercept) instead of pausing on them (web's HITL card has no mobile equivalent).
The "approval tools never hit a broker" invariant is upheld by this wrapper, not
by the loop's pause — see the report's seam note.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Any

from ai.chat.agent import ChatRunResult
from ai.chat.loop import (
    ApprovalRequest,
    LoopError,
    TextChunk,
    TextDelta,
    TextEnd,
    ToolCallStarted,
    ToolResult,
    assemble_system_prompt,
    build_tools,
    run_chat_loop,
)
from ai.chat.messages import last_user_text, ui_messages_to_llm
from ai.chat.tool_specs import APPROVAL_TOOLS
from ai.chat.tools import CHAT_SESSION_ID, execute_tool
from ai.llm.base import LLMMessage
from ai.llm.factory import get_llm

_EMPTY_GREETING = "Send a message to start chatting with your trading copilot."
_TEXT_CHUNK_SIZE = 12
_CREATE_RULE_REFUSAL = (
    "Arming alerts/rules isn't available in mobile chat yet — "
    "create it from the web cockpit (create_rule arms immediately and "
    "has no mobile approval step)."
)


def _text_delta_events(text: str) -> list[dict[str, Any]]:
    if not text:
        return [{"type": "text-delta", "delta": ""}]
    return [
        {"type": "text-delta", "delta": text[i : i + _TEXT_CHUNK_SIZE]}
        for i in range(0, len(text), _TEXT_CHUNK_SIZE)
    ]


async def run_mobile_chat_stream(
    messages: list[dict[str, Any]],
    *,
    session_id: str | None = None,
    mode: str = "temporary",
    model: str | None = None,
    result: ChatRunResult,
) -> AsyncIterator[dict[str, Any]]:
    del mode  # persistence handled by the route via finalize_chat_turn

    # Ambient session id for per-session tool fences (e.g. the quote-save cap).
    # Task-scoped: each request streams in its own task, so sessions can't bleed.
    CHAT_SESSION_ID.set(session_id)

    message_id = uuid.uuid4().hex
    yield {"type": "start"}

    user_text = last_user_text(messages)
    if not user_text:
        result.assistant_text = _EMPTY_GREETING
        for event in _text_delta_events(_EMPTY_GREETING):
            yield event
        yield {"type": "done", "sessionId": session_id, "messageId": message_id}
        return

    assembled = assemble_system_prompt(
        user_text, inject_memories=True, inject_journal=True
    )
    convo: list[LLMMessage] = [
        LLMMessage(role="system", content=assembled.system),
        *ui_messages_to_llm(messages),
    ]
    tools = build_tools()
    llm = get_llm(model=model)
    text_parts: list[str] = []

    async def _execute(name: str, args: dict[str, Any]) -> Any:
        """Mobile tool executor: divert the approval tools, run the rest normally.

        SAFETY-CRITICAL: propose_order becomes a PENDING phone proposal and every
        other approval tool is refused, so the real ``execute_tool`` (and thus the
        ``ExecutionService.submit`` chokepoint) is only ever reached for read/
        non-approval tools on this surface.
        """
        if name == "propose_order":
            from trading.proposals import create_phone_proposal

            row = await create_phone_proposal(
                args, session_id=session_id, source="mobile_chat"
            )
            if not isinstance(row, dict) or row.get("error") or not row.get("id"):
                reason = row.get("error") if isinstance(row, dict) else None
                return {"error": reason or "proposal could not be created"}
            return row
        if name in APPROVAL_TOOLS:
            # create_rule et al. arm immediately and have no mobile approval step.
            return {"error": _CREATE_RULE_REFUSAL}
        return await execute_tool(name, args)

    # approval_tools=frozenset(): route approval tools to _execute (where they are
    # diverted) rather than pausing on them — mobile has no in-chat HITL card.
    # max_tokens inherits run_chat_loop's default (raised to 4000 serv-side to stop
    # report truncation) — keep it central so web + mobile stay in lockstep.
    async for event in run_chat_loop(
        llm, convo, tools, execute=_execute, approval_tools=frozenset()
    ):
        if isinstance(event, TextDelta):
            # Real token-live fragment (streaming providers) — forward verbatim.
            text_parts.append(event.text)
            yield {"type": "text-delta", "delta": event.text}
        elif isinstance(event, TextEnd):
            pass  # mobile has no text-block framing to close
        elif isinstance(event, TextChunk):
            # Non-streaming final/interim answer — chunk it for an incremental feel.
            text_parts.append(event.text)
            for delta in _text_delta_events(event.text):
                yield delta
        elif isinstance(event, ToolCallStarted):
            if event.tool_name in APPROVAL_TOOLS:
                # propose_order -> proposal event, create_rule -> refusal: both
                # surface on the ToolResult, so suppress the raw tool-call chip.
                continue
            yield {
                "type": "tool-call",
                "toolCallId": event.tool_call_id,
                "name": event.tool_name,
                "args": event.input,
            }
        elif isinstance(event, ToolResult):
            if event.tool_name == "propose_order":
                out = event.output if isinstance(event.output, dict) else {}
                if event.ok and out.get("id"):
                    yield {
                        "type": "proposal",
                        "proposalId": out["id"],
                        "summary": out.get("summary"),
                        "symbol": out.get("symbol"),
                        "side": out.get("side"),
                        "qty": out.get("qty"),
                    }
                else:  # proposal could not be created — surface a failed tool-result
                    yield {
                        "type": "tool-result",
                        "toolCallId": event.tool_call_id,
                        "name": event.tool_name,
                        "ok": False,
                    }
            elif event.tool_name in APPROVAL_TOOLS:
                # create_rule refused — always a failed tool-result.
                yield {
                    "type": "tool-result",
                    "toolCallId": event.tool_call_id,
                    "name": event.tool_name,
                    "ok": False,
                }
            else:
                yield {
                    "type": "tool-result",
                    "toolCallId": event.tool_call_id,
                    "name": event.tool_name,
                    "ok": event.ok,
                }
        elif isinstance(event, ApprovalRequest):
            # Unreachable with approval_tools=frozenset(); mapped defensively so a
            # future config change can never silently drop the model's intent.
            yield {
                "type": "tool-result",
                "toolCallId": event.tool_call_id,
                "name": event.tool_name,
                "ok": False,
            }
        elif isinstance(event, LoopError):
            yield {"type": "error", "message": event.message}

    result.assistant_text = "".join(text_parts)
    yield {"type": "done", "sessionId": session_id, "messageId": message_id}
