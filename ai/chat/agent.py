"""Trading copilot chat agent with tool streaming."""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterator
from typing import Any

from ai.chat.messages import last_user_text, ui_messages_to_llm
from ai.chat.stream import UIMessageStream
from ai.chat.tools import TOOL_DEFINITIONS, execute_tool, plan_tools
from ai.llm.base import LLMMessage
from ai.llm.factory import get_llm, get_llm_provider_name


def _system_prompt() -> str:
    tools = "\n".join(
        f"- {name}: {meta['description']}"
        for name, meta in TOOL_DEFINITIONS.items()
    )
    return (
        "You are Help Me Trade, a proactive trading copilot. "
        "Answer clearly and concisely in markdown when helpful. "
        "You may receive tool results from portfolio snapshots, memories, "
        "or research runs — synthesize them into actionable insight. "
        "Never invent positions or prices not present in tool output.\n\n"
        f"Available tools:\n{tools}"
    )


def _build_final_messages(
    history: list[LLMMessage],
    *,
    user_text: str,
    tool_results: list[dict[str, Any]],
) -> list[LLMMessage]:
    context = ""
    if tool_results:
        context = (
            "\n\nTool results for this turn:\n"
            + json.dumps(tool_results, indent=2, default=str)
        )
    return [
        LLMMessage(role="system", content=_system_prompt()),
        *history,
        LLMMessage(
            role="user",
            content=f"{user_text}{context}",
        ),
    ]


def run_chat_stream(messages: list[dict[str, Any]]) -> Iterator[str]:
    stream = UIMessageStream()
    yield stream.start()
    yield stream.start_step()

    user_text = last_user_text(messages)
    if not user_text:
        yield from stream.text_chunks(
            "Send a message to start chatting with your trading copilot."
        )
        yield stream.finish_step()
        yield stream.finish()
        yield stream.done()
        return

    history = ui_messages_to_llm(messages[:-1] if messages else [])
    planned = plan_tools(user_text)
    tool_results: list[dict[str, Any]] = []

    if planned:
        reasoning = (
            f"Planning {len(planned)} tool call(s) for: {user_text[:120]}"
        )
        yield from stream.reasoning_chunks(reasoning)

    for tool_name, tool_input in planned:
        tool_call_id = f"call_{uuid.uuid4().hex[:12]}"
        try:
            output = execute_tool(tool_name, tool_input)
        except Exception as exc:
            output = {"error": str(exc)}
        tool_results.append(
            {"tool": tool_name, "input": tool_input, "output": output}
        )
        yield from stream.tool_call(
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            tool_input=tool_input,
            output=output,
        )

    try:
        llm = get_llm()
        final_messages = _build_final_messages(
            history, user_text=user_text, tool_results=tool_results
        )
        response = llm.complete(final_messages, max_tokens=1200)
    except Exception as exc:
        yield stream.error(str(exc))
        response = (
            f"Unable to reach the configured LLM ({get_llm_provider_name()}): {exc}"
        )

    yield from stream.text_chunks(response)
    yield stream.finish_step()
    yield stream.finish()
    yield stream.done()
