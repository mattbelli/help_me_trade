"""Mobile copilot chat orchestrator — plan, execute tools, stream simple SSE events."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from typing import Any

from ai.chat.agent import ChatRunResult, _build_final_messages
from ai.chat.context import (
    format_journal_context_block,
    format_memory_context_block,
    turn_journal_context,
    turn_memory_context,
)
from ai.chat.messages import last_user_text, ui_messages_to_llm
from ai.chat.tools import CHAT_SESSION_ID, execute_tool, plan_tools
from ai.llm.factory import get_llm

_EMPTY_GREETING = "Send a message to start chatting with your trading copilot."
_TEXT_CHUNK_SIZE = 12


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

    history = ui_messages_to_llm(messages[:-1] if messages else [])
    # allow_propose_order=True: the planner may pick propose_order; this path intercepts it
    # into a PENDING phone proposal (create_phone_proposal) and never routes it to execute_tool.
    plan = await asyncio.to_thread(
        plan_tools, user_text, model=model, allow_propose_order=True
    )
    tool_results: list[dict[str, Any]] = []

    for index, (name, args) in enumerate(plan):
        tool_call_id = f"t{index}"

        if name == "propose_order":
            from trading.proposals import create_phone_proposal

            # PENDING only — never executes (no ExecutionService on this surface).
            # Any failure (error dict, raise, or a row missing expected fields) is
            # surfaced as a tool-result error so the stream still reaches its
            # terminal `done` event instead of dying mid-flight.
            try:
                row = await create_phone_proposal(
                    args, session_id=session_id, source="mobile_chat"
                )
                if not isinstance(row, dict) or row.get("error"):
                    reason = row.get("error") if isinstance(row, dict) else None
                    raise ValueError(reason or "proposal could not be created")
                proposal_event = {
                    "type": "proposal",
                    "proposalId": row["id"],
                    "summary": row.get("summary"),
                    "symbol": row["symbol"],
                    "side": row["side"],
                    "qty": row["qty"],
                }
            except Exception as exc:
                yield {
                    "type": "tool-result",
                    "toolCallId": tool_call_id,
                    "name": name,
                    "ok": False,
                }
                tool_results.append({"tool": name, "error": str(exc)})
                continue

            yield proposal_event
            tool_results.append(
                {
                    "tool": name,
                    "status": "pending",
                    "proposal_id": proposal_event["proposalId"],
                    "summary": proposal_event["summary"],
                }
            )
            continue

        if name == "create_rule":
            # Alerts/rules arm IMMEDIATELY on create_rule, and mobile chat has no
            # approval step (only propose_order has the phone-approval flow). Refuse
            # here so mobile can never arm an unapproved live rule; the synthesis tells
            # the user to arm it from the web cockpit instead. (Re-enable with a real
            # mobile approval flow — see MAC_AGENT_HANDOFF / the create_rule follow-up.)
            yield {
                "type": "tool-result",
                "toolCallId": tool_call_id,
                "name": name,
                "ok": False,
            }
            tool_results.append(
                {
                    "tool": name,
                    "error": "Arming alerts/rules isn't available in mobile chat yet — "
                    "create it from the web cockpit (create_rule arms immediately and "
                    "has no mobile approval step).",
                }
            )
            continue

        yield {
            "type": "tool-call",
            "toolCallId": tool_call_id,
            "name": name,
            "args": args,
        }
        try:
            tool_res = await execute_tool(name, args)
        except Exception as exc:
            tool_res = {"error": str(exc)}
        yield {
            "type": "tool-result",
            "toolCallId": tool_call_id,
            "name": name,
            "ok": "error" not in (tool_res or {}),
        }
        tool_results.append({"tool": name, "result": tool_res})

    extra_blocks: list[str] = []
    try:
        memories = await asyncio.to_thread(turn_memory_context, user_text)
        extra_blocks.append(format_memory_context_block(memories))
    except Exception:
        pass
    try:
        journal = await asyncio.to_thread(turn_journal_context, user_text)
        extra_blocks.append(format_journal_context_block(journal))
    except Exception:
        pass

    final_messages = _build_final_messages(
        history,
        user_text=user_text,
        tool_results=tool_results,
        extra_system_blocks=extra_blocks or None,
    )
    try:
        response = get_llm(model=model).complete(final_messages, max_tokens=1200)
    except Exception as exc:
        yield {"type": "error", "message": f"LLM unavailable: {exc}"}
        response = "Sorry — I couldn't generate a response just now."

    result.assistant_text = response
    for event in _text_delta_events(response):
        yield event
    yield {"type": "done", "sessionId": session_id, "messageId": message_id}
