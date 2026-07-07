"""Web chat orchestrator — the one Python loop encoded as an AI-SDK UIMessageStream.

This is what the Next.js `/api/chat` route proxies to when `CHAT_BRAIN=python`
(the default). It drives `ai.chat.loop.run_chat_loop` and encodes its events as
the exact AI-SDK v6 UI-message-stream chunks (`ai.chat.stream.UIMessageStream`),
so assistant-ui renders identically to the retired in-route TS `streamText` loop
— same text streaming, same `tool-<name>` parts, same Approve/Reject cards.

The approval handshake (`propose_order` / `create_rule`) is reproduced against
the SDK's real HITL wire protocol:
  * fresh turn: emit `tool-input-*` + `tool-approval-request`, then finish and
    close the stream — the model called an approval tool, the loop paused, and
    NOTHING executed.
  * resume turn: the client re-POSTs the whole message array with the tool part
    flipped to `approval-responded`; we execute the approved tool (no fresh LLM
    call first), emit `tool-output-available` (or `tool-output-denied`), then run
    the loop once more for the model's wrap-up text.

Approval-executes-here is where `propose_order` finally runs — through the SAME
`execute_tool` path as before (records the paper proposal + the guarded
`ExecutionService.submit` chokepoint). The loop itself never executes it.
"""

from __future__ import annotations

import logging
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
from ai.chat.stream import UIMessageStream
from ai.chat.tools import CHAT_SESSION_ID, execute_tool
from ai.llm.base import LLMMessage, ToolCall
from ai.llm.factory import get_llm
from db.chat_approvals import (
    CLAIMED,
    CONSUMED,
    MISMATCH,
    claim_approval,
    record_approval_request,
)

logger = logging.getLogger(__name__)

_EMPTY_GREETING = "Send a message to start chatting with your trading copilot."

# Denial text per invalid-approval claim outcome. The verdict + input arrive
# from the CLIENT on resume, so an approval may only execute by claiming its
# server-side ledger row (db/chat_approvals) — these explain a losing claim.
_CLAIM_DENIAL_TEXT = {
    MISMATCH: (
        "The approved action no longer matches the card that was shown — "
        "nothing was executed. Please ask again to get a fresh card."
    ),
    CONSUMED: (
        "This approval was already used once — nothing was executed again."
    ),
    # UNKNOWN and anything unexpected: deny-by-default wording.
    "default": (
        "This approval was not issued by the server — nothing was executed."
    ),
}


# --- Resume detection ----------------------------------------------------------
def _find_pending_approval(messages: list[dict[str, Any]]) -> dict[str, Any] | None:
    """If the last assistant message carries a tool part the user just answered
    (`state == "approval-responded"`), return the reconstructed call + verdict.

    This is the client→server half of the AI-SDK HITL protocol: on approve/deny
    the whole `UIMessage[]` is re-POSTed with that one part mutated. We do NOT
    trust an already-executed part (`output-available`) — only a fresh verdict.
    """
    if not messages:
        return None
    last = messages[-1]
    if not isinstance(last, dict) or last.get("role") != "assistant":
        return None
    parts = last.get("parts")
    if not isinstance(parts, list):
        return None
    for part in parts:
        if not isinstance(part, dict):
            continue
        ptype = str(part.get("type", ""))
        if not ptype.startswith("tool-"):
            continue
        if part.get("state") != "approval-responded":
            continue
        approval = part.get("approval")
        if not isinstance(approval, dict) or approval.get("approved") is None:
            continue
        return {
            "tool_name": ptype[len("tool-") :],
            "tool_call_id": str(part.get("toolCallId") or ""),
            "input": part.get("input") if isinstance(part.get("input"), dict) else {},
            "approval_id": str(approval.get("id") or ""),
            "approved": bool(approval.get("approved")),
            "reason": approval.get("reason"),
        }
    return None


async def run_web_chat_stream(
    messages: list[dict[str, Any]],
    *,
    session_id: str | None = None,
    mode: str = "temporary",
    model: str | None = None,
    enabled_tools: set[str] | None = None,
    inject_memories: bool = True,
    result: ChatRunResult,
) -> AsyncIterator[str]:
    """Yield AI-SDK UIMessageStream SSE lines for one web chat turn."""
    del mode  # persistence handled by the route via finalize_chat_turn
    CHAT_SESSION_ID.set(session_id)

    stream = UIMessageStream()
    parts: list[dict[str, Any]] = []
    text_acc: list[str] = []

    yield stream.start()
    yield stream.start_step()

    try:
        pending = _find_pending_approval(messages)
        if pending is not None:
            async for chunk in _resume_after_approval(
                stream, messages, pending, parts, text_acc, model, enabled_tools
            ):
                yield chunk
        else:
            async for chunk in _fresh_turn(
                stream,
                messages,
                parts,
                text_acc,
                model=model,
                enabled_tools=enabled_tools,
                inject_memories=inject_memories,
            ):
                yield chunk
    except Exception:  # never leave the client hanging mid-stream
        logger.exception("web chat stream failed")
        yield stream.error("Chat stream failed.")

    yield stream.finish_step()
    yield stream.finish()
    yield stream.done()

    result.assistant_text = "".join(text_acc)
    result.assistant_message = {
        "id": stream.message_id,
        "role": "assistant",
        "parts": parts,
    }


async def _fresh_turn(
    stream: UIMessageStream,
    messages: list[dict[str, Any]],
    parts: list[dict[str, Any]],
    text_acc: list[str],
    *,
    model: str | None,
    enabled_tools: set[str] | None,
    inject_memories: bool,
) -> AsyncIterator[str]:
    user_text = last_user_text(messages)
    if not user_text:
        for line in _emit_text(stream, parts, text_acc, _EMPTY_GREETING):
            yield line
        return

    assembled = assemble_system_prompt(
        user_text, inject_memories=inject_memories, inject_journal=True
    )
    convo: list[LLMMessage] = [
        LLMMessage(role="system", content=assembled.system),
        *ui_messages_to_llm(messages),
    ]
    tools = build_tools(enabled_tools)
    llm = get_llm(model=model)

    async for chunk in _drive_loop(stream, llm, convo, tools, parts, text_acc):
        yield chunk


async def _resume_after_approval(
    stream: UIMessageStream,
    messages: list[dict[str, Any]],
    pending: dict[str, Any],
    parts: list[dict[str, Any]],
    text_acc: list[str],
    model: str | None,
    enabled_tools: set[str] | None,
) -> AsyncIterator[str]:
    tool_name = pending["tool_name"]
    tool_call_id = pending["tool_call_id"]
    tool_input = pending["input"]
    approval_id = pending["approval_id"]
    approved = pending["approved"]
    reason = pending["reason"]

    call = ToolCall(id=tool_call_id, name=tool_name, arguments=tool_input)

    # Single-consume ledger claim: BOTH verdicts consume the row (a rejected
    # card must not be approvable later by replaying it with approved=true).
    claim = claim_approval(
        approval_id,
        tool_call_id=tool_call_id,
        tool_name=tool_name,
        tool_input=tool_input,
    )

    if approved:
        if claim != CLAIMED:
            # Replay / mutated input / forged or unknown approval: deny, explain,
            # execute nothing. (Replays get the denial path, not a cached output
            # — simpler, and the original stream already rendered the result.)
            logger.warning(
                "approval claim rejected (%s) for %s call=%s id=%s",
                claim,
                tool_name,
                tool_call_id,
                approval_id,
            )
            yield stream.tool_output_denied(tool_call_id=tool_call_id)
            parts.append(
                {
                    "type": f"tool-{tool_name}",
                    "toolCallId": tool_call_id,
                    "state": "output-denied",
                    "input": tool_input,
                    "approval": {"id": approval_id, "approved": True},
                }
            )
            denial = _CLAIM_DENIAL_TEXT.get(claim, _CLAIM_DENIAL_TEXT["default"])
            for line in _emit_text(stream, parts, text_acc, denial):
                yield line
            return
        # Execute the approved tool HERE — this is the only place propose_order /
        # create_rule ever runs, and only after the human tapped Approve AND the
        # verdict claimed its server-issued, input-hash-bound ledger row.
        try:
            output = await execute_tool(tool_name, tool_input)
        except Exception as exc:
            logger.exception("approved tool %s failed", tool_name)
            output = {"error": str(exc)}
        yield stream.tool_output_available(tool_call_id=tool_call_id, output=output)
        parts.append(
            {
                "type": f"tool-{tool_name}",
                "toolCallId": tool_call_id,
                "state": "output-available",
                "input": tool_input,
                "output": output,
                "approval": {"id": approval_id, "approved": True},
            }
        )
        tool_content = output
    else:
        yield stream.tool_output_denied(tool_call_id=tool_call_id)
        parts.append(
            {
                "type": f"tool-{tool_name}",
                "toolCallId": tool_call_id,
                "state": "output-denied",
                "input": tool_input,
                "approval": {"id": approval_id, "approved": False, "reason": reason},
            }
        )
        tool_content = {"status": "rejected_by_user", "reason": reason or "user rejected"}

    # Feed the resolved tool call + result back so the model can write its wrap-up.
    resume_system = assemble_system_prompt(
        last_user_text(messages), inject_memories=True, inject_journal=True
    ).system
    convo: list[LLMMessage] = [
        LLMMessage(role="system", content=resume_system),
        *ui_messages_to_llm(messages),
        LLMMessage(role="assistant", content="", tool_calls=(call,)),
        LLMMessage(
            role="tool",
            content=_as_text(tool_content),
            tool_call_id=tool_call_id,
            name=tool_name,
        ),
    ]
    tools = build_tools(enabled_tools)
    llm = get_llm(model=model)
    async for chunk in _drive_loop(stream, llm, convo, tools, parts, text_acc):
        yield chunk


async def _drive_loop(
    stream: UIMessageStream,
    llm: Any,
    convo: list[LLMMessage],
    tools: list[Any],
    parts: list[dict[str, Any]],
    text_acc: list[str],
) -> AsyncIterator[str]:
    """Encode loop events → UIMessageStream chunks + persisted parts."""
    # Live-text framing state: a streamed text block spans TextDelta..TextEnd,
    # sharing one id; its fragments accumulate into a single persisted part.
    text_id: str | None = None
    text_buf: list[str] = []
    async for event in run_chat_loop(llm, convo, tools, execute=execute_tool):
        if isinstance(event, TextChunk):
            for line in _emit_text(stream, parts, text_acc, event.text):
                yield line
        elif isinstance(event, TextDelta):
            if text_id is None:
                text_id = f"text_{uuid.uuid4().hex}"
                text_buf = []
                yield stream.text_start(text_id)
            text_buf.append(event.text)
            yield stream.text_delta(text_id, event.text)
        elif isinstance(event, TextEnd):
            if text_id is not None:
                yield stream.text_end(text_id)
                full = "".join(text_buf)
                if full:
                    parts.append({"type": "text", "text": full})
                    text_acc.append(full)
                text_id = None
                text_buf = []
        elif isinstance(event, ToolCallStarted):
            yield stream.tool_input_start(
                tool_call_id=event.tool_call_id, tool_name=event.tool_name
            )
            yield stream.tool_input_available(
                tool_call_id=event.tool_call_id,
                tool_name=event.tool_name,
                tool_input=event.input,
            )
        elif isinstance(event, ToolResult):
            yield stream.tool_output_available(
                tool_call_id=event.tool_call_id, output=event.output
            )
            parts.append(
                {
                    "type": f"tool-{event.tool_name}",
                    "toolCallId": event.tool_call_id,
                    "state": "output-available",
                    "input": event.input,
                    "output": event.output,
                }
            )
        elif isinstance(event, ApprovalRequest):
            approval_id = f"apr_{uuid.uuid4().hex}"
            # Ledger first: a card must never reach the client without its
            # server-side row (resume validates against it). A failed write
            # raises → the generic stream-error path; no un-validatable card.
            record_approval_request(
                approval_id,
                tool_call_id=event.tool_call_id,
                tool_name=event.tool_name,
                tool_input=event.input,
            )
            yield stream.tool_input_start(
                tool_call_id=event.tool_call_id, tool_name=event.tool_name
            )
            yield stream.tool_input_available(
                tool_call_id=event.tool_call_id,
                tool_name=event.tool_name,
                tool_input=event.input,
            )
            yield stream.tool_approval_request(
                approval_id=approval_id, tool_call_id=event.tool_call_id
            )
            parts.append(
                {
                    "type": f"tool-{event.tool_name}",
                    "toolCallId": event.tool_call_id,
                    "state": "approval-requested",
                    "input": event.input,
                    "approval": {"id": approval_id},
                }
            )
        elif isinstance(event, LoopError):
            yield stream.error(event.message)


def _emit_text(
    stream: UIMessageStream,
    parts: list[dict[str, Any]],
    text_acc: list[str],
    text: str,
) -> list[str]:
    lines = list(stream.text_chunks(text))
    if text:
        parts.append({"type": "text", "text": text})
        text_acc.append(text)
    return lines


def _as_text(value: Any) -> str:
    import json

    if isinstance(value, str):
        return value
    return json.dumps(value, default=str)
