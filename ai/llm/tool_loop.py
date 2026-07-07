"""Provider-agnostic native tool-call round-trip driver.

Runs the standard act->observe->act loop: ask the model, execute any tool calls
it requests, feed the results back, repeat until a final text answer or the
iteration cap. This is the reusable round-trip primitive; no existing chat loop
uses it yet (that wiring is Phase 1b).
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field

from ai.llm.base import LLMMessage, ToolCall, ToolSpec

logger = logging.getLogger(__name__)

# Mirrors the web route's stepCountIs(6); a guard against a model that loops on
# tool calls forever (and against runaway token/tool cost).
DEFAULT_MAX_ITERATIONS = 6


class ToolLoopError(RuntimeError):
    """Raised when the loop can't proceed (no tool support, or iteration cap hit)."""


@dataclass
class ToolLoopResult:
    text: str
    messages: list[LLMMessage]
    iterations: int
    tool_calls: list[ToolCall] = field(default_factory=list)


def run_tool_loop(
    llm: object,
    messages: list[LLMMessage],
    tools: list[ToolSpec],
    execute: Callable[[ToolCall], str],
    *,
    tool_choice: str = "auto",
    max_iterations: int = DEFAULT_MAX_ITERATIONS,
    max_tokens: int = 1024,
    temperature: float | None = None,
) -> ToolLoopResult:
    """Drive an LLM through a native tool-calling conversation to a final answer.

    ``execute`` maps a requested ToolCall to its string result. The returned
    ``messages`` include the full transcript (assistant tool-call turns + tool
    results) so callers can persist or trace it.

    Raises ToolLoopError if the provider lacks ``complete_tools`` or the model
    exceeds ``max_iterations`` without producing a final text answer.
    """
    complete_tools = getattr(llm, "complete_tools", None)
    if not callable(complete_tools):
        provider = getattr(llm, "provider", type(llm).__name__)
        raise ToolLoopError(
            f"provider {provider!r} does not support native tool-calling"
        )

    convo = list(messages)
    executed: list[ToolCall] = []
    for iteration in range(1, max_iterations + 1):
        result = complete_tools(
            convo,
            tools,
            tool_choice=tool_choice,
            max_tokens=max_tokens,
            temperature=temperature,
        )
        if result.is_final:
            return ToolLoopResult(
                text=result.text or "",
                messages=convo,
                iterations=iteration,
                tool_calls=executed,
            )
        # Record the assistant's tool-call turn, then execute each requested
        # call (a single response may request several in parallel).
        convo.append(
            LLMMessage(
                role="assistant",
                content=result.text or "",
                tool_calls=tuple(result.tool_calls),
            )
        )
        for call in result.tool_calls:
            executed.append(call)
            output = execute(call)
            convo.append(
                LLMMessage(
                    role="tool",
                    content=output,
                    tool_call_id=call.id,
                    name=call.name,
                )
            )

    raise ToolLoopError(
        f"tool loop exceeded max_iterations={max_iterations} without a final answer"
    )
