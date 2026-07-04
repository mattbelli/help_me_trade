"""GET /api/board and POST /api/board/inbox — project kanban from References/board.json."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from fastapi.testclient import TestClient


def _sample_board() -> dict:
    return {
        "updated_at": "2026-07-04T02:30:00Z",
        "lanes": ["founder", "in_progress", "next", "backlog", "done"],
        "cards": [
            {
                "id": "x",
                "title": "Test card",
                "lane": "next",
                "type": "feature",
                "priority": "high",
                "notes": "",
                "owner": "serv",
                "updated_at": "2026-07-04",
            }
        ],
    }


def test_board_api_returns_parsed_file(tmp_path: Path, monkeypatch) -> None:
    board = _sample_board()
    board_path = tmp_path / "board.json"
    board_path.write_text(json.dumps(board), encoding="utf-8")
    monkeypatch.setattr("api.main.BOARD_JSON_PATH", board_path)

    from api.main import app

    with TestClient(app, base_url="http://127.0.0.1") as client:
        resp = client.get("/api/board")

    assert resp.status_code == 200
    assert resp.json() == {"board": board}


def test_board_api_missing_file_returns_empty_shape(tmp_path: Path, monkeypatch) -> None:
    missing = tmp_path / "does-not-exist.json"
    monkeypatch.setattr("api.main.BOARD_JSON_PATH", missing)

    from api.main import app

    with TestClient(app, base_url="http://127.0.0.1") as client:
        resp = client.get("/api/board")

    assert resp.status_code == 200
    assert resp.json() == {
        "board": {"updated_at": None, "lanes": [], "cards": []},
    }


def test_board_inbox_post_adds_card(
    tmp_path: Path, monkeypatch, mutation_client: TestClient
) -> None:
    board = _sample_board()
    board_path = tmp_path / "board.json"
    board_path.write_text(json.dumps(board), encoding="utf-8")
    monkeypatch.setattr("api.main.BOARD_JSON_PATH", board_path)
    monkeypatch.setattr("api.main.time.time", lambda: 1_719_964_800.0)

    resp = mutation_client.post(
        "/api/board/inbox",
        json={"title": "  Quick idea  ", "notes": " some context "},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["card"] == {
        "id": "fr-1719964800",
        "title": "Quick idea",
        "lane": "inbox",
        "type": "idea",
        "priority": "normal",
        "notes": "some context",
        "owner": "founder",
        "updated_at": date.today().isoformat(),
    }

    saved = json.loads(board_path.read_text(encoding="utf-8"))
    assert saved["lanes"][0] == "inbox"
    assert saved["lanes"][1:] == board["lanes"]
    assert len(saved["cards"]) == len(board["cards"]) + 1
    assert saved["cards"][:-1] == board["cards"]
    assert saved["cards"][-1] == body["card"]
    assert saved["updated_at"] != board["updated_at"]
    assert saved["updated_at"].endswith("Z")
    assert list(tmp_path.glob(".board-*.tmp")) == []


def test_board_inbox_post_requires_title(
    tmp_path: Path, monkeypatch, mutation_client: TestClient
) -> None:
    board_path = tmp_path / "board.json"
    board_path.write_text(json.dumps(_sample_board()), encoding="utf-8")
    monkeypatch.setattr("api.main.BOARD_JSON_PATH", board_path)

    for payload in ({}, {"title": ""}, {"title": "   "}):
        resp = mutation_client.post("/api/board/inbox", json=payload)
        assert resp.status_code == 400
        assert resp.json()["detail"] == "title is required"


def test_board_inbox_post_corrupt_file_returns_409(
    tmp_path: Path, monkeypatch, mutation_client: TestClient
) -> None:
    board_path = tmp_path / "board.json"
    board_path.write_text("{not json", encoding="utf-8")
    monkeypatch.setattr("api.main.BOARD_JSON_PATH", board_path)

    resp = mutation_client.post("/api/board/inbox", json={"title": "hello"})

    assert resp.status_code == 409
    assert "corrupt" in resp.json()["detail"].lower()


def test_board_inbox_post_missing_file_returns_409(
    tmp_path: Path, monkeypatch, mutation_client: TestClient
) -> None:
    missing = tmp_path / "missing-board.json"
    monkeypatch.setattr("api.main.BOARD_JSON_PATH", missing)

    resp = mutation_client.post("/api/board/inbox", json={"title": "hello"})

    assert resp.status_code == 409
    assert "missing" in resp.json()["detail"].lower()
