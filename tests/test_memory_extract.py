"""Tests for POST /api/memory/extract."""

from __future__ import annotations

from fastapi.testclient import TestClient


def test_memory_extract_requires_user_and_assistant_text() -> None:
    from api.main import app

    with TestClient(app, base_url="http://127.0.0.1") as client:
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


def test_memory_extract_calls_extractor(monkeypatch) -> None:
    from api.main import app

    def fake_extract(**_kwargs):
        return [{"type": "preference", "content": "20% cap", "id": 1}]

    monkeypatch.setattr(
        "api.ai_routes.extract_memories_from_turn",
        fake_extract,
    )

    with TestClient(app, base_url="http://127.0.0.1") as client:
        res = client.post(
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
