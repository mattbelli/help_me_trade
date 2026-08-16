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
    monkeypatch.setattr("api.workspace_routes.BOARD_JSON_PATH", board_path)
    monkeypatch.setattr("api.workspace_routes.BOARD_ARCHIVE_PATH", tmp_path / "no-archive.json")

    from api.main import app

    with TestClient(app, base_url="http://127.0.0.1") as client:
        resp = client.get("/api/board")

    assert resp.status_code == 200
    assert resp.json() == {"board": board}


def test_board_api_appends_archived_cards(tmp_path: Path, monkeypatch) -> None:
    """Settled `done` cards live in board_archive.json but still reach /board."""
    board = _sample_board()
    board_path = tmp_path / "board.json"
    board_path.write_text(json.dumps(board), encoding="utf-8")
    archived = {"id": "old", "title": "Ancient", "lane": "done", "notes": ""}
    archive_path = tmp_path / "board_archive.json"
    archive_path.write_text(json.dumps({"cards": [archived]}), encoding="utf-8")
    monkeypatch.setattr("api.workspace_routes.BOARD_JSON_PATH", board_path)
    monkeypatch.setattr("api.workspace_routes.BOARD_ARCHIVE_PATH", archive_path)

    from api.main import app

    with TestClient(app, base_url="http://127.0.0.1") as client:
        resp = client.get("/api/board")

    assert resp.json()["board"]["cards"] == board["cards"] + [archived]


def test_board_api_survives_corrupt_archive(tmp_path: Path, monkeypatch) -> None:
    """A broken archive must not take the whole board down — live cards still serve."""
    board = _sample_board()
    board_path = tmp_path / "board.json"
    board_path.write_text(json.dumps(board), encoding="utf-8")
    archive_path = tmp_path / "board_archive.json"
    archive_path.write_text("{not json", encoding="utf-8")
    monkeypatch.setattr("api.workspace_routes.BOARD_JSON_PATH", board_path)
    monkeypatch.setattr("api.workspace_routes.BOARD_ARCHIVE_PATH", archive_path)

    from api.main import app

    with TestClient(app, base_url="http://127.0.0.1") as client:
        resp = client.get("/api/board")

    assert resp.status_code == 200
    assert resp.json()["board"]["cards"] == board["cards"]


def test_board_api_missing_file_returns_empty_shape(tmp_path: Path, monkeypatch) -> None:
    missing = tmp_path / "does-not-exist.json"
    monkeypatch.setattr("api.workspace_routes.BOARD_JSON_PATH", missing)

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
    monkeypatch.setattr("api.workspace_routes.BOARD_JSON_PATH", board_path)
    monkeypatch.setattr("api.workspace_routes.time.time", lambda: 1_719_964_800.0)

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


def test_board_inbox_post_preserves_canonical_format(
    tmp_path: Path, monkeypatch, mutation_client: TestClient
) -> None:
    """The write path must not reformat the board.

    indent=1 + ensure_ascii=False is the on-disk format shared with tools/board.py. This
    endpoint once wrote indent=2 with escaped unicode, which rewrote every line of the
    file and made every open branch conflict on merge.
    """
    board = _sample_board()
    board["cards"][0]["notes"] = "em dash — arrow → warn ⚠"
    board_path = tmp_path / "board.json"
    board_path.write_text(
        json.dumps(board, indent=1, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    monkeypatch.setattr("api.workspace_routes.BOARD_JSON_PATH", board_path)

    resp = mutation_client.post("/api/board/inbox", json={"title": "Quick idea"})
    assert resp.status_code == 200

    raw = board_path.read_text(encoding="utf-8")
    saved = json.loads(raw)
    assert raw == json.dumps(saved, indent=1, ensure_ascii=False) + "\n"
    assert "—" in raw and "\\u2014" not in raw


def test_board_inbox_post_requires_title(
    tmp_path: Path, monkeypatch, mutation_client: TestClient
) -> None:
    board_path = tmp_path / "board.json"
    board_path.write_text(json.dumps(_sample_board()), encoding="utf-8")
    monkeypatch.setattr("api.workspace_routes.BOARD_JSON_PATH", board_path)

    for payload in ({}, {"title": ""}, {"title": "   "}):
        resp = mutation_client.post("/api/board/inbox", json=payload)
        assert resp.status_code == 400
        assert resp.json()["detail"] == "title is required"


def test_board_inbox_post_corrupt_file_returns_409(
    tmp_path: Path, monkeypatch, mutation_client: TestClient
) -> None:
    board_path = tmp_path / "board.json"
    board_path.write_text("{not json", encoding="utf-8")
    monkeypatch.setattr("api.workspace_routes.BOARD_JSON_PATH", board_path)

    resp = mutation_client.post("/api/board/inbox", json={"title": "hello"})

    assert resp.status_code == 409
    assert "corrupt" in resp.json()["detail"].lower()


def test_board_inbox_post_missing_file_returns_409(
    tmp_path: Path, monkeypatch, mutation_client: TestClient
) -> None:
    missing = tmp_path / "missing-board.json"
    monkeypatch.setattr("api.workspace_routes.BOARD_JSON_PATH", missing)

    resp = mutation_client.post("/api/board/inbox", json={"title": "hello"})

    assert resp.status_code == 409
    assert "missing" in resp.json()["detail"].lower()
