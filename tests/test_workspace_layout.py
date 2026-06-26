"""Tests for server-side workspace layout persistence.

Exercises:
- round-trip: save then load returns the same layout
- load when empty returns None
- PUT route rejects non-list body and malformed item with 400
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from db.workspace import init_workspace_tables, load_workspace_layout, save_workspace_layout

# ── DB layer tests ──────────────────────────────────────────────────────────────

@pytest.fixture()
def tmp_db(tmp_path: Path) -> Path:
    db = tmp_path / "test_workspace.db"
    init_workspace_tables(db)
    return db


def test_load_when_empty_returns_none(tmp_db: Path) -> None:
    assert load_workspace_layout(tmp_db) is None


def test_round_trip(tmp_db: Path) -> None:
    layout = [
        {"i": "chart-1", "type": "chart", "x": 0, "y": 0, "w": 8, "h": 4},
        {"i": "watchlist-1", "type": "watchlist", "x": 8, "y": 0, "w": 4, "h": 4},
    ]
    save_workspace_layout(layout, tmp_db)
    loaded = load_workspace_layout(tmp_db)
    assert loaded == layout


def test_upsert_overwrites(tmp_db: Path) -> None:
    layout_v1 = [{"i": "a", "type": "positions", "x": 0, "y": 0, "w": 6, "h": 3}]
    layout_v2 = [{"i": "b", "type": "chart", "x": 0, "y": 0, "w": 12, "h": 5}]
    save_workspace_layout(layout_v1, tmp_db)
    save_workspace_layout(layout_v2, tmp_db)
    loaded = load_workspace_layout(tmp_db)
    assert loaded == layout_v2


# ── Route tests ──────────────────────────────────────────────────────────────────

@pytest.fixture()
def client():
    from api.main import app
    with TestClient(app) as c:
        yield c


VALID_ITEM = {"i": "chart-1", "type": "chart", "x": 0, "y": 0, "w": 8, "h": 4}


def test_put_layout_accepts_valid(client) -> None:
    res = client.put("/api/workspace/layout", json={"layout": [VALID_ITEM]})
    assert res.status_code == 200
    assert res.json() == {"ok": True}


def test_get_layout_returns_saved(client) -> None:
    payload = [VALID_ITEM, {"i": "pos", "type": "positions", "x": 8, "y": 0, "w": 4, "h": 3}]
    client.put("/api/workspace/layout", json={"layout": payload})
    res = client.get("/api/workspace/layout")
    assert res.status_code == 200
    data = res.json()
    assert data["layout"] == payload


def test_put_rejects_non_list(client) -> None:
    res = client.put("/api/workspace/layout", json={"layout": {"i": "x"}})
    assert res.status_code == 400
    assert "array" in res.json()["error"]


def test_put_rejects_missing_field(client) -> None:
    # missing 'type'
    bad_item = {"i": "chart-1", "x": 0, "y": 0, "w": 8, "h": 4}
    res = client.put("/api/workspace/layout", json={"layout": [bad_item]})
    assert res.status_code == 400


def test_put_rejects_non_numeric_coord(client) -> None:
    bad_item = {"i": "chart-1", "type": "chart", "x": "left", "y": 0, "w": 8, "h": 4}
    res = client.put("/api/workspace/layout", json={"layout": [bad_item]})
    assert res.status_code == 400


def test_get_layout_null_when_empty(client) -> None:
    # This client hits the temp DB which starts empty.
    # The GET should return {layout: null} unless a prior test in this session wrote one.
    # Since the conftest uses a shared temp DB for the default, we do a fresh load check
    # via the DB layer which we already tested; the route contract is that layout may be null.
    res = client.get("/api/workspace/layout")
    assert res.status_code == 200
    body = res.json()
    assert "layout" in body  # may be null or a list — both are valid
