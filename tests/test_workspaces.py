"""Tests for server-side workspaces (page library + linked workspaces) persistence."""

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

# v1 shape (embedded pages_state per workspace) — kept for migration tests.
V1_WORKSPACES_STATE = {
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

VALID_WORKSPACES_STATE = {
    "version": 2,
    "pages": {
        "page-1": {"id": "page-1", "name": "Trading", "layout": [VALID_ITEM]},
        "page-2": {"id": "page-2", "name": "Research", "layout": []},
    },
    "workspaces": {
        "ws-default": {
            "id": "ws-default",
            "name": "Main",
            "pageIds": ["page-1", "page-2"],
            "activePageId": "page-1",
        },
        "ws-2": {
            "id": "ws-2",
            "name": "Laptop",
            # page-1 is SHARED with ws-default (linked pages).
            "pageIds": ["page-1"],
            "activePageId": "page-1",
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
    assert loaded["version"] == 2
    assert loaded["defaultWorkspaceId"] == DEFAULT_WORKSPACE_ID
    ws = loaded["workspaces"][DEFAULT_WORKSPACE_ID]
    assert ws["pageIds"] == [CANVAS_PAGE_ID]
    assert ws["activePageId"] == CANVAS_PAGE_ID
    assert loaded["pages"][CANVAS_PAGE_ID]["layout"] == [VALID_ITEM]


def test_load_workspaces_migrates_v1_blob(tmp_db: Path) -> None:
    save_workspaces(V1_WORKSPACES_STATE, tmp_db)
    loaded = load_workspaces(tmp_db)
    assert loaded is not None
    assert loaded["version"] == 2
    # Two workspaces each had a "canvas" page — ids must not alias.
    assert len(loaded["pages"]) == 2
    ids_main = loaded["workspaces"]["ws-default"]["pageIds"]
    ids_laptop = loaded["workspaces"]["ws-2"]["pageIds"]
    assert ids_main != ids_laptop
    for pid in ids_main + ids_laptop:
        assert loaded["pages"][pid]["id"] == pid
    # Migration persisted: second load returns the same v2 blob.
    assert load_workspaces(tmp_db) == loaded


def test_load_workspaces_when_empty_wraps_default_pages(tmp_db: Path) -> None:
    # No pages blob either — load_workspace_pages migrates to the empty canvas
    # page, and workspaces wraps that.
    loaded = load_workspaces(tmp_db)
    assert loaded is not None
    assert loaded["workspaces"][DEFAULT_WORKSPACE_ID]["pageIds"] == [
        CANVAS_PAGE_ID
    ]


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
    # The endpoints share the test process's DB — an earlier test may have
    # stored a valid workspaces blob, which (by design) short-circuits the
    # pages-blob migration. Clear it so the migration path actually runs.
    from db.base import DEFAULT_DB_PATH
    from db.base import connect as _connect

    with _connect(DEFAULT_DB_PATH) as conn:
        conn.execute("DELETE FROM workspace_state WHERE key = 'workspaces'")
        conn.commit()

    client.put("/api/workspace/pages", json={"pages_state": VALID_PAGES_STATE})
    res = client.get("/api/workspace/workspaces")
    assert res.status_code == 200
    state = res.json()["workspaces_state"]
    assert state["version"] == 2
    assert state["defaultWorkspaceId"] == DEFAULT_WORKSPACE_ID
    page_ids = state["workspaces"][DEFAULT_WORKSPACE_ID]["pageIds"]
    assert [state["pages"][pid]["layout"] for pid in page_ids] == [[VALID_ITEM]]


def test_put_workspaces_rejects_v1_shape(client) -> None:
    res = client.put(
        "/api/workspace/workspaces",
        json={"workspaces_state": V1_WORKSPACES_STATE},
    )
    assert res.status_code == 400


def test_put_workspaces_rejects_missing_default(client) -> None:
    bad = {**VALID_WORKSPACES_STATE, "defaultWorkspaceId": "missing"}
    res = client.put("/api/workspace/workspaces", json={"workspaces_state": bad})
    assert res.status_code == 400


def test_put_workspaces_rejects_over_workspace_cap(client) -> None:
    many = {
        f"ws-{n}": {
            "id": f"ws-{n}",
            "name": f"W{n}",
            "pageIds": ["page-1"],
            "activePageId": "page-1",
        }
        for n in range(9)
    }
    bad = {
        "version": 2,
        "pages": {"page-1": {"id": "page-1", "name": "P", "layout": []}},
        "workspaces": many,
        "defaultWorkspaceId": "ws-0",
    }
    res = client.put("/api/workspace/workspaces", json={"workspaces_state": bad})
    assert res.status_code == 400


def test_put_workspaces_rejects_over_library_cap(client) -> None:
    pages = {
        f"page-{n}": {"id": f"page-{n}", "name": f"P{n}", "layout": []}
        for n in range(25)
    }
    bad = {
        "version": 2,
        "pages": pages,
        "workspaces": {
            "ws-1": {
                "id": "ws-1",
                "name": "W",
                "pageIds": ["page-0"],
                "activePageId": "page-0",
            }
        },
        "defaultWorkspaceId": "ws-1",
    }
    res = client.put("/api/workspace/workspaces", json={"workspaces_state": bad})
    assert res.status_code == 400


def test_put_workspaces_rejects_unknown_page_ref(client) -> None:
    bad = {
        "version": 2,
        "pages": {"page-1": {"id": "page-1", "name": "P", "layout": []}},
        "workspaces": {
            "ws-1": {
                "id": "ws-1",
                "name": "W",
                "pageIds": ["page-1", "ghost"],
                "activePageId": "page-1",
            }
        },
        "defaultWorkspaceId": "ws-1",
    }
    res = client.put("/api/workspace/workspaces", json={"workspaces_state": bad})
    assert res.status_code == 400


def test_put_workspaces_rejects_active_not_in_page_ids(client) -> None:
    bad = {
        "version": 2,
        "pages": {
            "page-1": {"id": "page-1", "name": "P", "layout": []},
            "page-2": {"id": "page-2", "name": "Q", "layout": []},
        },
        "workspaces": {
            "ws-1": {
                "id": "ws-1",
                "name": "W",
                "pageIds": ["page-1"],
                "activePageId": "page-2",
            }
        },
        "defaultWorkspaceId": "ws-1",
    }
    res = client.put("/api/workspace/workspaces", json={"workspaces_state": bad})
    assert res.status_code == 400


def test_put_workspaces_rejects_workspace_with_no_pages(client) -> None:
    bad = {
        "version": 2,
        "pages": {"page-1": {"id": "page-1", "name": "P", "layout": []}},
        "workspaces": {
            "ws-1": {"id": "ws-1", "name": "W", "pageIds": [], "activePageId": ""}
        },
        "defaultWorkspaceId": "ws-1",
    }
    res = client.put("/api/workspace/workspaces", json={"workspaces_state": bad})
    assert res.status_code == 400
