"""Tests for server-side workspaces (named groups of pages) persistence."""

from __future__ import annotations

from pathlib import Path

import pytest

from db.workspace import (
    CANVAS_PAGE_ID,
    DEFAULT_WORKSPACE_ID,
    init_workspace_tables,
    load_workspaces,
    save_workspace_pages,
    save_workspaces,
)

VALID_ITEM = {"i": "chart-1", "type": "chart", "x": 0, "y": 0, "w": 8, "h": 4}

VALID_PAGES_STATE = {
    "pages": {
        CANVAS_PAGE_ID: {
            "id": CANVAS_PAGE_ID,
            "name": "Page 1",
            "layout": [VALID_ITEM],
        }
    },
    "activePageId": CANVAS_PAGE_ID,
}

VALID_WORKSPACES_STATE = {
    "workspaces": {
        "ws-default": {
            "id": "ws-default",
            "name": "Main",
            "pages_state": VALID_PAGES_STATE,
        },
        "ws-2": {
            "id": "ws-2",
            "name": "Laptop",
            "pages_state": VALID_PAGES_STATE,
        },
    },
    "defaultWorkspaceId": "ws-default",
}


@pytest.fixture()
def tmp_db(tmp_path: Path) -> Path:
    db = tmp_path / "test_workspaces.db"
    init_workspace_tables(db)
    return db


def test_load_workspaces_migrates_pages_blob(tmp_db: Path) -> None:
    save_workspace_pages(VALID_PAGES_STATE, tmp_db)
    loaded = load_workspaces(tmp_db)
    assert loaded is not None
    assert loaded["defaultWorkspaceId"] == DEFAULT_WORKSPACE_ID
    ws = loaded["workspaces"][DEFAULT_WORKSPACE_ID]
    assert ws["name"] == "Main"
    assert ws["pages_state"] == VALID_PAGES_STATE


def test_load_workspaces_when_empty_wraps_default_pages(tmp_db: Path) -> None:
    # No pages blob either — load_workspace_pages migrates to the empty canvas
    # page, and workspaces wraps that.
    loaded = load_workspaces(tmp_db)
    assert loaded is not None
    ws = loaded["workspaces"][DEFAULT_WORKSPACE_ID]
    assert ws["pages_state"]["activePageId"] == CANVAS_PAGE_ID


def test_save_and_load_workspaces_round_trip(tmp_db: Path) -> None:
    save_workspaces(VALID_WORKSPACES_STATE, tmp_db)
    assert load_workspaces(tmp_db) == VALID_WORKSPACES_STATE


@pytest.fixture()
def client(mutation_client):
    yield mutation_client


def test_put_workspaces_accepts_valid(client) -> None:
    res = client.put(
        "/api/workspace/workspaces",
        json={"workspaces_state": VALID_WORKSPACES_STATE},
    )
    assert res.status_code == 200
    assert res.json() == {"ok": True}


def test_get_workspaces_returns_saved(client) -> None:
    client.put(
        "/api/workspace/workspaces",
        json={"workspaces_state": VALID_WORKSPACES_STATE},
    )
    res = client.get("/api/workspace/workspaces")
    assert res.status_code == 200
    assert res.json()["workspaces_state"] == VALID_WORKSPACES_STATE


def test_get_workspaces_migrates_pages(client) -> None:
    client.put("/api/workspace/pages", json={"pages_state": VALID_PAGES_STATE})
    res = client.get("/api/workspace/workspaces")
    assert res.status_code == 200
    state = res.json()["workspaces_state"]
    assert state["defaultWorkspaceId"] == DEFAULT_WORKSPACE_ID
    assert (
        state["workspaces"][DEFAULT_WORKSPACE_ID]["pages_state"]
        == VALID_PAGES_STATE
    )


def test_put_workspaces_rejects_missing_default(client) -> None:
    bad = {
        "workspaces": VALID_WORKSPACES_STATE["workspaces"],
        "defaultWorkspaceId": "missing",
    }
    res = client.put("/api/workspace/workspaces", json={"workspaces_state": bad})
    assert res.status_code == 400


def test_put_workspaces_rejects_over_cap(client) -> None:
    many = {
        f"ws-{n}": {
            "id": f"ws-{n}",
            "name": f"W{n}",
            "pages_state": VALID_PAGES_STATE,
        }
        for n in range(9)
    }
    bad = {"workspaces": many, "defaultWorkspaceId": "ws-0"}
    res = client.put("/api/workspace/workspaces", json={"workspaces_state": bad})
    assert res.status_code == 400


def test_put_workspaces_rejects_invalid_pages_state(client) -> None:
    bad = {
        "workspaces": {
            "ws-1": {
                "id": "ws-1",
                "name": "Broken",
                "pages_state": {"pages": {}, "activePageId": "x"},
            }
        },
        "defaultWorkspaceId": "ws-1",
    }
    res = client.put("/api/workspace/workspaces", json={"workspaces_state": bad})
    assert res.status_code == 400
