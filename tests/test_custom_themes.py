"""Tests for server-side custom-theme persistence."""

from __future__ import annotations

from pathlib import Path

import pytest

from db.workspace import (
    init_workspace_tables,
    load_custom_themes,
    save_custom_themes,
)

VALID_THEME = {
    "id": "custom-ab12cd34",
    "name": "Midnight",
    "base": "carbon",
    "colorScheme": "dark",
    "tokens": {"bg": "#0b0d12", "accent": "#5ad1be"},
    "vars": {"--background": "#0b0d12", "--c-accent": "#5ad1be"},
    "createdAt": "2026-06-30T00:00:00Z",
    "updatedAt": "2026-06-30T00:00:00Z",
}


@pytest.fixture()
def tmp_db(tmp_path: Path) -> Path:
    db = tmp_path / "test_custom_themes.db"
    init_workspace_tables(db)
    return db


def test_load_when_empty_returns_empty_list(tmp_db: Path) -> None:
    assert load_custom_themes(tmp_db) == []


def test_save_and_load_round_trip(tmp_db: Path) -> None:
    save_custom_themes([VALID_THEME], tmp_db)
    assert load_custom_themes(tmp_db) == [VALID_THEME]


@pytest.fixture()
def client(mutation_client):
    yield mutation_client


def test_put_accepts_valid(client) -> None:
    res = client.put(
        "/api/workspace/custom-themes",
        json={"custom_themes": [VALID_THEME]},
    )
    assert res.status_code == 200
    assert res.json() == {"ok": True}


def test_get_returns_saved(client) -> None:
    client.put("/api/workspace/custom-themes", json={"custom_themes": [VALID_THEME]})
    res = client.get("/api/workspace/custom-themes")
    assert res.status_code == 200
    assert res.json()["custom_themes"] == [VALID_THEME]


def test_put_rejects_non_list(client) -> None:
    res = client.put("/api/workspace/custom-themes", json={"custom_themes": {}})
    assert res.status_code == 400


def test_put_rejects_bad_vars(client) -> None:
    bad = {**VALID_THEME, "vars": {"--background": 123}}
    res = client.put("/api/workspace/custom-themes", json={"custom_themes": [bad]})
    assert res.status_code == 400


def test_put_rejects_missing_id(client) -> None:
    bad = {k: v for k, v in VALID_THEME.items() if k != "id"}
    res = client.put("/api/workspace/custom-themes", json={"custom_themes": [bad]})
    assert res.status_code == 400
