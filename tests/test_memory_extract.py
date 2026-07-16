"""Tests for POST /api/memory/extract and session memory rehydration."""

from __future__ import annotations

from fastapi.testclient import TestClient


def test_memory_extract_requires_user_and_assistant_text(mutation_client) -> None:
    client = mutation_client
    empty = client.post(
        "/api/memory/extract",
        json={"userText": "hello", "assistantText": ""},
    )
    assert empty.status_code == 200
    assert empty.json() == {"memories": []}

    ok = client.post(
        "/api/memory/extract",
        json={
            "messages": [
                {
                    "role": "user",
                    "parts": [{"type": "text", "text": "I cap single names at 20%"}],
                }
            ],
            "assistantText": "Understood — 20% max per position.",
            "sessionId": "chat_abc",
        },
    )
    assert ok.status_code == 200
    assert "memories" in ok.json()


def test_memory_extract_calls_extractor(monkeypatch, mutation_client) -> None:
    def fake_extract(**_kwargs):
        return [{"type": "preference", "content": "20% cap", "id": 1}]

    monkeypatch.setattr(
        "api.ai_routes.extract_memories_from_turn",
        fake_extract,
    )

    res = mutation_client.post(
        "/api/memory/extract",
        json={
            "userText": "I cap at 20%",
            "assistantText": "Got it.",
            "sessionId": "chat_xyz",
            "model": "deepseek-chat",
        },
    )
    assert res.status_code == 200
    assert res.json()["memories"] == [
        {"type": "preference", "content": "20% cap", "id": 1}
    ]


def test_extract_persists_source_message_id(tmp_path, monkeypatch) -> None:
    import ai.memories as mem_mod
    import db.base as db_base
    from ai.chat.memory_extract import extract_memories_from_turn

    db = tmp_path / "ai.db"
    monkeypatch.setattr(db_base, "DEFAULT_DB_PATH", db)
    mem_mod._memory_service = None

    monkeypatch.setattr(
        "ai.chat.memory_extract.complete_for_extraction",
        lambda *_args, **_kwargs: (
            '{"memories": [{"type": "preference", '
            '"content": "Prefer low-volatility ETFs for core holdings"}]}'
        ),
    )

    saved = extract_memories_from_turn(
        user_text="I prefer low-volatility ETFs for core holdings.",
        assistant_text="Got it.",
        session_id="sess_abc",
        source_message_id="asst_msg_1",
    )

    assert len(saved) == 1
    assert saved[0]["content"] == "Prefer low-volatility ETFs for core holdings"
    assert saved[0]["metadata"]["session_id"] == "sess_abc"
    assert saved[0]["metadata"]["source_message_id"] == "asst_msg_1"
    assert saved[0]["metadata"]["source"] == "chat"


def test_session_memories_endpoint_filters_chat_session(monkeypatch) -> None:
    from api.main import app

    records = [
        {
            "id": 1,
            "type": "preference",
            "content": "Pref for session A",
            "metadata": {
                "source": "chat",
                "session_id": "sess_a",
                "source_message_id": "msg_a1",
            },
            "created_at": "2026-01-01T00:00:00+00:00",
            "updated_at": "2026-01-01T00:00:00+00:00",
        },
        {
            "id": 2,
            "type": "preference",
            "content": "Pref for session B",
            "metadata": {
                "source": "chat",
                "session_id": "sess_b",
                "source_message_id": "msg_b1",
            },
            "created_at": "2026-01-01T00:00:00+00:00",
            "updated_at": "2026-01-01T00:00:00+00:00",
        },
        {
            "id": 3,
            "type": "preference",
            "content": "Manual memory",
            "metadata": {"source": "manual", "session_id": "sess_a"},
            "created_at": "2026-01-01T00:00:00+00:00",
            "updated_at": "2026-01-01T00:00:00+00:00",
        },
    ]

    class FakeMemoryService:
        def list(self, *, limit: int = 50) -> list[dict]:
            return records[:limit]

    monkeypatch.setattr(
        "api.ai_routes.get_memory_service",
        lambda: FakeMemoryService(),
    )
    monkeypatch.setattr("api.auth.API_TOKEN", "pytest-test-token")

    with TestClient(
        app,
        base_url="http://127.0.0.1",
        headers={"X-API-Token": "pytest-test-token"},
    ) as client:
        res = client.get("/api/memory/session/sess_a")
        assert res.status_code == 200
        memories = res.json()["memories"]
        assert len(memories) == 1
        assert memories[0]["content"] == "Pref for session A"
        assert memories[0]["source_message_id"] == "msg_a1"
        assert memories[0]["metadata"]["session_id"] == "sess_a"
