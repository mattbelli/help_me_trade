"""Tests for server-side multi-page workspace persistence."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from db.base import connect
from db.workspace import (
    CANVAS_PAGE_ID,
    init_workspace_tables,
    load_workspace_layout,
    load_workspace_pages,
    save_workspace_layout,
    save_workspace_pages,
)

VALID_ITEM = {"i": "chart-1", "type": "chart", "x": 0, "y": 0, "w": 8, "h": 4}

VALID_PAGES_STATE = {
    "pages": {
        CANVAS_PAGE_ID: {
            "id": CANVAS_PAGE_ID,
            "name": "Canvas",
            "layout": [VALID_ITEM],
        }
    },
    "activePageId": CANVAS_PAGE_ID,
}


@pytest.fixture()
def tmp_db(tmp_path: Path) -> Path:
    db = tmp_path / "test_workspace_pages.db"
    init_workspace_tables(db)
    return db


def test_load_pages_when_empty_migrates_to_canvas(tmp_db: Path) -> None:
    loaded = load_workspace_pages(tmp_db)
    assert loaded is not None
    assert loaded["activePageId"] == CANVAS_PAGE_ID
    assert loaded["pages"][CANVAS_PAGE_ID]["layout"] == []

    with connect(tmp_db) as conn:
        row = conn.execute(
            "SELECT value FROM workspace_state WHERE key = 'pages'",
        ).fetchone()
    assert row is not None
    persisted = json.loads(row[0])
    assert persisted["activePageId"] == CANVAS_PAGE_ID


def test_load_pages_migrates_existing_layout_row(tmp_db: Path) -> None:
    layout = [
        VALID_ITEM,
        {"i": "pos", "type": "positions", "x": 8, "y": 0, "w": 4, "h": 3},
    ]
    save_workspace_layout(layout, tmp_db)

    loaded = load_workspace_pages(tmp_db)
    assert loaded is not None
    assert loaded["pages"][CANVAS_PAGE_ID]["layout"] == layout
    assert loaded["activePageId"] == CANVAS_PAGE_ID

    # Legacy layout row remains untouched.
    assert load_workspace_layout(tmp_db) == layout


def test_save_and_load_pages_round_trip(tmp_db: Path) -> None:
    save_workspace_pages(VALID_PAGES_STATE, tmp_db)
    loaded = load_workspace_pages(tmp_db)
    assert loaded == VALID_PAGES_STATE


def test_load_pages_prefers_pages_row_over_layout(tmp_db: Path) -> None:
    save_workspace_layout(
        [{"i": "legacy", "type": "chart", "x": 0, "y": 0, "w": 4, "h": 3}],
        tmp_db,
    )
    save_workspace_pages(VALID_PAGES_STATE, tmp_db)
    loaded = load_workspace_pages(tmp_db)
    assert loaded == VALID_PAGES_STATE


@pytest.fixture()
def client():
    from api.main import app

    with TestClient(app) as c:
        yield c


def test_get_pages_auto_migrates_layout(client) -> None:
    layout = [VALID_ITEM]
    client.put("/api/workspace/layout", json={"layout": layout})
    res = client.get("/api/workspace/pages")
    assert res.status_code == 200
    state = res.json()["pages_state"]
    assert state["activePageId"] == CANVAS_PAGE_ID
    assert state["pages"][CANVAS_PAGE_ID]["layout"] == layout


def test_put_pages_accepts_valid(client) -> None:
    res = client.put("/api/workspace/pages", json={"pages_state": VALID_PAGES_STATE})
    assert res.status_code == 200
    assert res.json() == {"ok": True}


def test_get_pages_returns_saved(client) -> None:
    client.put("/api/workspace/pages", json={"pages_state": VALID_PAGES_STATE})
    res = client.get("/api/workspace/pages")
    assert res.status_code == 200
    assert res.json()["pages_state"] == VALID_PAGES_STATE


def test_put_pages_rejects_invalid_active_page(client) -> None:
    bad_state = {
        "pages": VALID_PAGES_STATE["pages"],
        "activePageId": "missing",
    }
    res = client.put("/api/workspace/pages", json={"pages_state": bad_state})
    assert res.status_code == 400
