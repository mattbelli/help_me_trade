"""Chat turn context injection."""

from __future__ import annotations

from ai.chat.context import format_memory_context_block


def test_format_memory_context_block_empty() -> None:
    assert format_memory_context_block([]) == ""


def test_format_memory_context_block_includes_memories() -> None:
    block = format_memory_context_block(
        [{"type": "preference", "content": "Risk cap 20%", "score": 0.8}]
    )
    assert "Relevant memories" in block
    assert "Risk cap 20%" in block
