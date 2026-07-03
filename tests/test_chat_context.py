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


def test_api_chat_context_passes_inject_journal(monkeypatch, mutation_client) -> None:
    captured: dict = {}

    def fake_build_turn_context(
        user_text: str,
        *,
        inject_memories: bool = False,
        inject_journal: bool = False,
    ):
        captured["user_text"] = user_text
        captured["inject_memories"] = inject_memories
        captured["inject_journal"] = inject_journal
        return {"system": "sys", "memories": [], "journal": {}}

    monkeypatch.setattr("api.ai_routes.build_turn_context", fake_build_turn_context)

    res = mutation_client.post(
        "/api/chat/context",
        json={"userText": "hello", "injectJournal": True, "injectMemories": False},
    )
    assert res.status_code == 200
    assert res.json()["system"] == "sys"
    assert captured["user_text"] == "hello"
    assert captured["inject_journal"] is True
    assert captured["inject_memories"] is False
