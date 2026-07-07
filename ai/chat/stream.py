"""UI message stream encoder (AI SDK v1 protocol)."""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterator
from typing import Any


def _new_id(prefix: str = "msg") -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


class UIMessageStream:
    def __init__(self, *, message_id: str | None = None) -> None:
        self.message_id = message_id or _new_id()

    def _event(self, payload: dict[str, Any]) -> str:
        return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"

    def start(self) -> str:
        return self._event({"type": "start", "messageId": self.message_id})

    def start_step(self) -> str:
        return self._event({"type": "start-step"})

    def finish_step(self) -> str:
        return self._event({"type": "finish-step"})

    def finish(self) -> str:
        return self._event({"type": "finish"})

    def done(self) -> str:
        return "data: [DONE]\n\n"

    def error(self, error_text: str) -> str:
        return self._event({"type": "error", "errorText": error_text})

    def text_chunks(self, text: str, *, chunk_size: int = 12) -> Iterator[str]:
        text_id = _new_id("text")
        yield self._event({"type": "text-start", "id": text_id})
        if not text:
            yield self._event({"type": "text-end", "id": text_id})
            return
        for i in range(0, len(text), chunk_size):
            yield self._event(
                {"type": "text-delta", "id": text_id, "delta": text[i : i + chunk_size]}
            )
        yield self._event({"type": "text-end", "id": text_id})

    def tool_call(
        self,
        *,
        tool_call_id: str,
        tool_name: str,
        tool_input: dict[str, Any],
        output: Any,
    ) -> Iterator[str]:
        yield self._event(
            {
                "type": "tool-input-start",
                "toolCallId": tool_call_id,
                "toolName": tool_name,
                "dynamic": True,
            }
        )
        yield self._event(
            {
                "type": "tool-input-available",
                "toolCallId": tool_call_id,
                "toolName": tool_name,
                "input": tool_input,
                "dynamic": True,
            }
        )
        yield self._event(
            {
                "type": "tool-output-available",
                "toolCallId": tool_call_id,
                "output": output,
                "dynamic": True,
            }
        )

    # --- Static-tool chunks (the one Python loop) ---------------------------
    # These mirror the AI-SDK `streamText().toUIMessageStreamResponse()` wire
    # chunks for *statically* declared tools (the web chat's tools are static —
    # `frontend/lib/chat-tools.ts` — so the client builds `tool-<name>` parts and
    # `makeAssistantToolUI({toolName})` matches them). Therefore NO `dynamic`
    # flag (that would produce `dynamic-tool` parts the custom cards don't match).
    # Field sets are exact: the client validates each chunk with `z.strictObject`,
    # so an extra key rejects the whole stream (AI SDK v6, verified in node_modules).

    def tool_input_start(self, *, tool_call_id: str, tool_name: str) -> str:
        return self._event(
            {
                "type": "tool-input-start",
                "toolCallId": tool_call_id,
                "toolName": tool_name,
            }
        )

    def tool_input_available(
        self, *, tool_call_id: str, tool_name: str, tool_input: dict[str, Any]
    ) -> str:
        return self._event(
            {
                "type": "tool-input-available",
                "toolCallId": tool_call_id,
                "toolName": tool_name,
                "input": tool_input,
            }
        )

    def tool_approval_request(self, *, approval_id: str, tool_call_id: str) -> str:
        return self._event(
            {
                "type": "tool-approval-request",
                "approvalId": approval_id,
                "toolCallId": tool_call_id,
            }
        )

    def tool_output_available(self, *, tool_call_id: str, output: Any) -> str:
        # UI chunks are JSON — `undefined` would drop; the SDK coerces to null.
        return self._event(
            {
                "type": "tool-output-available",
                "toolCallId": tool_call_id,
                "output": output if output is not None else None,
            }
        )

    def tool_output_denied(self, *, tool_call_id: str) -> str:
        # Deliberately toolCallId-only — the SDK drops toolName on this chunk.
        return self._event(
            {"type": "tool-output-denied", "toolCallId": tool_call_id}
        )
