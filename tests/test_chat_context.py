"""Chat turn context injection."""

from __future__ import annotations

from ai.chat.context import format_memory_context_block, turn_memory_context


def test_format_memory_context_block_empty() -> None:
    assert format_memory_context_block([]) == ""


def test_format_memory_context_block_includes_memories() -> None:
    block = format_memory_context_block(
        [{"type": "preference", "content": "Risk cap 20%", "score": 0.8}]
    )
    assert "Relevant memories" in block
    assert "Risk cap 20%" in block


def test_format_memory_context_block_tags_provenance() -> None:
    """HIGH-3: injected memories must carry a provenance chip so a poisoned
    agent_inferred row can never read as a founder-authored instruction."""
    block = format_memory_context_block(
        [
            {"type": "directive", "content": "Founder rule", "authority": "user_authored"},
            {"type": "directive", "content": "Confirmed rule", "authority": "user_confirmed"},
            {"type": "fact", "content": "Agent note", "authority": "agent_inferred"},
        ]
    )
    assert "founder-authored" in block
    assert "founder-confirmed" in block
    assert "agent-inferred (unverified)" in block


def test_format_memory_context_block_unknown_authority_defaults_untrusted() -> None:
    """Deny-by-default: an unrecognised authority renders as the untrusted chip,
    never silently as a founder tier."""
    block = format_memory_context_block(
        [{"type": "fact", "content": "Mystery", "authority": "??"}]
    )
    assert "agent-inferred (unverified)" in block
    assert "founder-authored" not in block
    assert "founder-confirmed" not in block


def test_turn_memory_context_fences_injection(monkeypatch) -> None:
    """HIGH-3: only active rows with a recognised authority reach the prompt —
    an inactive row or an unknown-authority row is dropped (deny-by-default)."""
    rows = [
        {"id": 1, "content": "ok", "authority": "agent_inferred", "status": "active"},
        {"id": 2, "content": "confirmed", "authority": "user_confirmed", "status": "active"},
        {"id": 3, "content": "archived", "authority": "user_authored", "status": "archived"},
        {"id": 4, "content": "weird", "authority": "spoofed", "status": "active"},
    ]
    monkeypatch.setattr(
        "ai.chat.context.retrieve_relevant_memories", lambda *a, **k: rows
    )
    out = turn_memory_context("anything")
    kept = {m["id"] for m in out}
    assert kept == {1, 2}


def test_turn_memory_context_excludes_pending_proposal(monkeypatch) -> None:
    """M-1: a row carrying ``proposed_supersedes_id`` is a founder-review
    proposal under review — it is excluded from injection, while the OLD
    protected row it targets keeps injecting as today."""
    rows = [
        {
            "id": 10,
            "content": "Cap any single name at 20%",
            "authority": "user_confirmed",
            "status": "active",
        },
        {
            "id": 11,
            "content": "Cap any single name at 30%",
            "authority": "agent_inferred",
            "status": "active",
            "proposed_supersedes_id": 10,
        },
    ]
    monkeypatch.setattr(
        "ai.chat.context.retrieve_relevant_memories", lambda *a, **k: rows
    )
    out = turn_memory_context("concentration cap")
    kept = {m["id"] for m in out}
    assert kept == {10}


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
