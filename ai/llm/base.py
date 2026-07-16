"""LLM client protocol and shared types."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True)
class ToolCall:
    """A single native tool-call request emitted by the model.

    ``arguments`` is the parsed JSON object; ``raw_arguments`` preserves the
    provider's original argument string so an assistant tool-call message can be
    round-tripped back verbatim.
    """

    id: str
    name: str
    arguments: dict[str, Any]
    raw_arguments: str = ""


@dataclass(frozen=True)
class LLMMessage:
    role: str
    content: str
    # Populated only on assistant messages that carry native tool-call requests.
    tool_calls: tuple[ToolCall, ...] = ()
    # Populated only on ``role="tool"`` result messages.
    tool_call_id: str | None = None
    name: str | None = None


@dataclass(frozen=True)
class ToolSpec:
    """Provider-agnostic function/tool definition (name + JSON-Schema params)."""

    name: str
    description: str
    parameters: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ToolCallResult:
    """One turn of a native tool-call round-trip.

    Either the model asked to call one or more tools (``tool_calls`` non-empty)
    or it produced its final answer (``text`` set, ``tool_calls`` empty).
    """

    text: str | None
    tool_calls: tuple[ToolCall, ...] = ()
    finish_reason: str | None = None

    @property
    def is_final(self) -> bool:
        return not self.tool_calls


# --- Streaming tool-call events -----------------------------------------------
# Typed events yielded by a provider's optional ``stream_tools()`` async
# generator so text surfaces token-live while native tool-calling still works.
# ``ToolCall``s are emitted fully reassembled (id/name on the first SSE fragment,
# arguments accumulated across fragments) — never partial.
@dataclass(frozen=True)
class StreamTextDelta:
    text: str


@dataclass(frozen=True)
class StreamToolCall:
    call: ToolCall


@dataclass(frozen=True)
class StreamFinish:
    finish_reason: str | None = None
    usage: dict[str, Any] | None = None


StreamEvent = StreamTextDelta | StreamToolCall | StreamFinish


class ToolArgumentError(ValueError):
    """Raised when a tool-call's arguments are not a valid JSON object.

    Surfaced as a typed error (rather than a bare ``JSONDecodeError``/crash) so
    the loop driver and callers can handle a malformed model response cleanly.
    """

    def __init__(self, *, tool_name: str, raw_arguments: str, message: str) -> None:
        self.tool_name = tool_name
        self.raw_arguments = raw_arguments
        super().__init__(message)


@runtime_checkable
class LLMClient(Protocol):
    provider: str

    def complete(self, messages: list[LLMMessage], *, max_tokens: int = 1024) -> str: ...

    def stream(
        self, messages: list[LLMMessage], *, max_tokens: int = 1024
    ) -> Iterator[str]: ...

    async def acomplete(
        self, messages: list[LLMMessage], *, max_tokens: int = 1024
    ) -> str: ...

    async def astream(
        self, messages: list[LLMMessage], *, max_tokens: int = 1024
    ) -> AsyncIterator[str]: ...


@runtime_checkable
class ToolCallingLLM(Protocol):
    """Providers that support the native tool-calling round-trip.

    Optional capability layered on top of ``LLMClient``: providers without
    function-calling (e.g. ollama) remain valid ``LLMClient``s and fall back to
    ``ai.llm.structured.parse_structured``.
    """

    provider: str

    def complete_tools(
        self,
        messages: list[LLMMessage],
        tools: list[ToolSpec],
        *,
        tool_choice: str = "auto",
        max_tokens: int = 1024,
        temperature: float | None = None,
    ) -> ToolCallResult: ...
