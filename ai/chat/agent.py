"""Trading copilot chat agent with tool streaming."""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from ai.chat.memory_extract import extract_memories_from_turn
from ai.chat.messages import last_user_text, ui_messages_to_llm
from ai.chat.stream import UIMessageStream
from ai.chat.tools import TOOL_DEFINITIONS
from ai.llm.base import LLMMessage
from ai.llm.factory import get_llm, get_llm_provider_name
from db.accounts import list_accounts
from db.chat_sessions import (
    PERSISTED_MODES,
    get_chat_session,
    title_from_first_message,
    update_chat_session_title,
    upsert_chat_message,
)


@dataclass
class ChatRunResult:
    assistant_text: str = ""
    memories_saved: list[dict[str, Any]] = field(default_factory=list)


try:
    _ET: timezone | ZoneInfo = ZoneInfo("America/New_York")
except Exception:  # noqa: BLE001 - Windows without tzdata; date is what matters here
    _ET = timezone(timedelta(hours=-5))  # EST fallback


def _market_session_label(now: datetime) -> str:
    if now.weekday() >= 5:
        return "closed"
    t = now.time()
    if time(9, 30) <= t < time(16, 0):
        return "regular hours open"
    if time(4, 0) <= t < time(9, 30):
        return "pre-market"
    if time(16, 0) <= t < time(20, 0):
        return "post-market"
    return "closed"


def current_date_context() -> str:
    now = datetime.now(_ET)
    weekday = now.strftime("%A")
    date_str = now.date().isoformat()
    session = _market_session_label(now)
    return (
        f"Current date: {date_str} ({weekday}), market session: {session}. "
        "Use THIS date for all relative dates ('30 days from now', option expirations) — "
        "never assume a date from training. US options expire on the listed exchange date."
    )


def chat_system_prompt() -> str:
    return _system_prompt()


def _accounts_context_block() -> str:
    accounts = list_accounts()
    if not accounts:
        return "Accounts: none configured."
    parts: list[str] = []
    for account in accounts:
        label = account.get("label") or account["id"]
        broker = account.get("broker", "?")
        if account.get("execution_capable"):
            parts.append(f"'{label}' ({broker}) = PAPER (tradeable)")
        else:
            parts.append(f"'{label}' ({broker}) = REAL money, READ-ONLY")
    return "Accounts: " + "; ".join(parts) + "."


def _system_prompt() -> str:
    tools = "\n".join(
        f"- {name}: {meta['description']}"
        for name, meta in TOOL_DEFINITIONS.items()
    )
    return (
        f"{current_date_context()}\n\n"
        f"{_accounts_context_block()}\n\n"
        "Help Me Trade runs autonomous strategies (e.g. dip, earnings) that scan, "
        "propose trades to the user's phone, and execute through a guarded chokepoint "
        "on approval. You are the cockpit: use get_agent_status to report what's running.\n\n"
        "Answer clearly and concisely in markdown when helpful. Terminal aesthetic: "
        "plain professional text — NEVER use emojis or decorative unicode symbols "
        "(no ✅🚀📈⚠️ etc.); status reads as words, not icons.\n\n"
        "Behavior rules:\n"
        "- Greetings (hi, hello, hey): reply in 1–2 short friendly sentences. "
        "Do not call tools and do not mention portfolio, positions, or P&L.\n"
        "- NEVER call propose_order for a read-only/Schwab account. They are real money. "
        "If asked, explain they're read-only and offer a paper account.\n"
        "- News and public web pages: call web_search (SearXNG). Only cite returned "
        "snippets/URLs. web_search is NOT live X or Reddit — no tweet streams, no "
        "subreddit feeds, no clock-time filters (6–8am). time_range is day/week/month "
        "only. If the user asks for minute-level social sentiment, explain the limit "
        "and offer day-scoped site:reddit.com or site:x.com search instead.\n"
        "- Never write fake tool calls like [web_search: ...] or [Calling propose_order...] "
        "in text. Use the tool API and wait for results before answering.\n"
        "- Buy/sell requests: call propose_order (symbol, side, qty, order_type market|limit). "
        "Do not describe calling the tool — call it. The Approve/Reject card is the UI.\n"
        "- When a propose_order result includes a `score`, surface the calibrated win%, "
        "EV, and top factors to the user (honestly — never invent a score).\n"
        "- Option pricing/bid-ask: call get_option_quote (broker options data) — NEVER web_search "
        "for quotes. Options require a limit order; if no live options quote is available, tell "
        "the user to set the limit from their broker's option chain. Options are buy-to-open "
        "long-only for now.\n"
        "- Alert/signal/watch requests: call create_rule with a validated spec "
        "(confirm parameters first — it arms immediately). "
        "(do not narrate; the approval card is the UI).\n"
        "- Portfolio, performance, trades: only discuss after calling the matching tool "
        "in that turn. Never invent positions, prices, or closed trades.\n"
        "- Realized P&L is realized.realized_closed (net cash on closed positions). "
        "NEVER present approximate_fifo figures or incomplete_positions proceeds as "
        "realized P&L — incomplete positions (e.g. PLTR) lack cost basis and their "
        "P&L is unknown.\n"
        "- Portfolio P&L: a position's `unrealized_pl` is CUMULATIVE since entry, "
        "never today's move. For 'today'/'how'd I do today' questions, use the "
        "snapshot's per-account `day_pl`/`day_pl_pct` and aggregate `total_day_pl`. "
        "Always say whether a number is today's or since-entry; never present "
        "cumulative unrealized P&L as a daily result.\n"
        "- Do NOT invent goals, targets, rules, thresholds, or preferences the user "
        "never stated and that are not in the provided data or memories "
        "(e.g. never claim a '10 trades/week goal'). If you do not have a fact, say so.\n"
        "- start_research queues a background job for deep multi-source reports — "
        "not for quick news or social questions (use web_search instead).\n\n"
        f"Available tools:\n{tools}"
    )


def _build_final_messages(
    history: list[LLMMessage],
    *,
    user_text: str,
    tool_results: list[dict[str, Any]],
    extra_system_blocks: list[str] | None = None,
) -> list[LLMMessage]:
    context = ""
    if tool_results:
        context = (
            "\n\nTool results for this turn:\n"
            + json.dumps(tool_results, indent=2, default=str)
        )
    system_content = _system_prompt()
    if extra_system_blocks:
        system_content += "".join(block for block in extra_system_blocks if block)
    return [
        LLMMessage(
            role="system",
            content=system_content,
        ),
        *history,
        LLMMessage(
            role="user",
            content=f"{user_text}{context}",
        ),
    ]


def run_chat_stream(
    messages: list[dict[str, Any]],
    *,
    session_id: str | None = None,
    mode: str = "quick",
    model: str | None = None,
    result: ChatRunResult | None = None,
) -> Iterator[str]:
    stream = UIMessageStream()
    run = result or ChatRunResult()
    yield stream.start()
    yield stream.start_step()

    user_text = last_user_text(messages)
    if not user_text:
        text = "Send a message to start chatting with your trading copilot."
        yield from stream.text_chunks(text)
        run.assistant_text = text
        yield stream.finish_step()
        yield stream.finish()
        yield stream.done()
        return

    history = ui_messages_to_llm(messages[:-1] if messages else [])
    tool_results: list[dict[str, Any]] = []

    try:
        llm = get_llm(model=model)
        final_messages = _build_final_messages(
            history, user_text=user_text, tool_results=tool_results
        )
        response = llm.complete(final_messages, max_tokens=1200)
    except Exception as exc:
        yield stream.error(str(exc))
        response = (
            f"Unable to reach the configured LLM ({get_llm_provider_name()}): {exc}"
        )

    run.assistant_text = response
    yield from stream.text_chunks(response)
    yield stream.finish_step()
    yield stream.finish()
    yield stream.done()


def finalize_chat_turn(
    *,
    messages: list[dict[str, Any]],
    user_text: str,
    assistant_text: str,
    session_id: str | None,
    mode: str,
    model: str | None = None,
    run: ChatRunResult,
    assistant_message: dict[str, Any] | None = None,
    extract: bool = True,
) -> None:
    _persist_turn(
        messages=messages,
        user_text=user_text,
        assistant_text=assistant_text,
        session_id=session_id,
        mode=mode,
        model=model,
        run=run,
        assistant_message=assistant_message,
        extract=extract,
    )


def _last_message_by_role(
    messages: list[dict[str, Any]], role: str
) -> dict[str, Any] | None:
    for msg in reversed(messages):
        if isinstance(msg, dict) and msg.get("role") == role:
            return msg
    return None


def _assistant_parts_for_persist(
    assistant_message: dict[str, Any] | None,
    assistant_text: str,
) -> list[dict[str, Any]]:
    if assistant_message and isinstance(assistant_message.get("parts"), list):
        parts = assistant_message["parts"]
        if parts:
            return parts
    if assistant_text.strip():
        return [{"type": "text", "text": assistant_text}]
    return []


def _persist_turn(
    *,
    messages: list[dict[str, Any]],
    user_text: str,
    assistant_text: str,
    session_id: str | None,
    mode: str,
    model: str | None = None,
    run: ChatRunResult,
    assistant_message: dict[str, Any] | None = None,
    extract: bool = True,
) -> None:
    if not session_id or mode not in PERSISTED_MODES:
        return

    session = get_chat_session(session_id)
    if session is None:
        return

    # Persist the latest user message of the turn. Do NOT gate on the array's LAST
    # message being a user message — on tool-calling turns the context ends with an
    # assistant/tool message, which previously dropped the user turn from the saved
    # thread entirely (only AI responses survived a reload). The upsert is idempotent
    # by the message's stable id, so re-running across tool rounds is harmless.
    user_msg = _last_message_by_role(messages, "user")
    if user_msg:
        user_parts = user_msg.get("parts")
        if not isinstance(user_parts, list):
            user_parts = [{"type": "text", "text": user_text}]
        user_id = str(user_msg.get("id") or f"msg_{uuid.uuid4().hex[:12]}")
        upsert_chat_message(
            session_id=session_id,
            role="user",
            parts=user_parts,
            message_id=user_id,
        )

    assistant_parts = _assistant_parts_for_persist(
        assistant_message, assistant_text
    )
    if not assistant_parts:
        return

    assistant_id = (
        str(assistant_message.get("id"))
        if isinstance(assistant_message, dict) and assistant_message.get("id")
        else f"msg_{uuid.uuid4().hex[:12]}"
    )
    upsert_chat_message(
        session_id=session_id,
        role="assistant",
        parts=assistant_parts,
        message_id=assistant_id,
    )

    if session.get("title") == "New chat" and user_text:
        update_chat_session_title(session_id, title_from_first_message(user_text))

    if extract and mode == "standard":
        run.memories_saved = extract_memories_from_turn(
            user_text=user_text,
            assistant_text=assistant_text,
            session_id=session_id,
            model=model,
        )
