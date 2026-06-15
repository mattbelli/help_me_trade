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

    def reasoning_chunks(self, text: str, *, chunk_size: int = 16) -> Iterator[str]:
        rid = _new_id("reasoning")
        yield self._event({"type": "reasoning-start", "id": rid})
        for i in range(0, len(text), chunk_size):
            yield self._event(
                {
                    "type": "reasoning-delta",
                    "id": rid,
                    "delta": text[i : i + chunk_size],
                }
            )
        yield self._event({"type": "reasoning-end", "id": rid})

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
