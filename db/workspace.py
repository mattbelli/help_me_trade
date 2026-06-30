"""Workspace layout server-side persistence."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from db.base import DEFAULT_DB_PATH
from db.base import connect as _connect


def init_workspace_tables(db_path: Path = DEFAULT_DB_PATH) -> None:
    with _connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS workspace_state (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            """
        )
        conn.commit()


def load_workspace_layout(db_path: Path = DEFAULT_DB_PATH) -> list | None:
    """Return the stored layout list, or None if absent/invalid (fail-soft)."""
    try:
        init_workspace_tables(db_path)
        with _connect(db_path) as conn:
            row = conn.execute(
                "SELECT value FROM workspace_state WHERE key = 'layout'",
            ).fetchone()
        if row is None:
            return None
        parsed = json.loads(row[0])
        if not isinstance(parsed, list):
            return None
        return parsed
    except Exception:
        return None


def save_workspace_layout(layout: list, db_path: Path = DEFAULT_DB_PATH) -> None:
    """Upsert the workspace layout. Last-write-wins."""
    init_workspace_tables(db_path)
    now = datetime.now(UTC).isoformat()
    value = json.dumps(layout)
    with _connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO workspace_state (key, value, updated_at)
            VALUES ('layout', ?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value,
                                           updated_at = excluded.updated_at
            """,
            (value, now),
        )
        conn.commit()


CANVAS_PAGE_ID = "canvas"


def _wrap_layout_as_pages(layout: list) -> dict:
    """Wrap a flat layout list into the multi-page state shape."""
    return {
        "pages": {
            CANVAS_PAGE_ID: {
                "id": CANVAS_PAGE_ID,
                "name": "Canvas",
                "layout": layout,
            }
        },
        "activePageId": CANVAS_PAGE_ID,
    }


def _is_valid_pages_state(state: object) -> bool:
    if not isinstance(state, dict):
        return False
    pages = state.get("pages")
    active_page_id = state.get("activePageId")
    if not isinstance(pages, dict) or not isinstance(active_page_id, str):
        return False
    if not pages:
        return False
    for page_id, page in pages.items():
        if not isinstance(page, dict):
            return False
        if not isinstance(page.get("id"), str):
            return False
        if not isinstance(page.get("name"), str):
            return False
        layout = page.get("layout")
        if not isinstance(layout, list):
            return False
        for item in layout:
            if not _is_valid_layout_item(item):
                return False
    return active_page_id in pages


def _is_valid_layout_item(item: object) -> bool:
    if not isinstance(item, dict):
        return False
    return (
        isinstance(item.get("i"), str)
        and isinstance(item.get("type"), str)
        and isinstance(item.get("x"), (int, float))
        and isinstance(item.get("y"), (int, float))
        and isinstance(item.get("w"), (int, float))
        and isinstance(item.get("h"), (int, float))
    )


def load_workspace_pages(db_path: Path = DEFAULT_DB_PATH) -> dict | None:
    """Return stored pages state, auto-migrating from legacy layout if needed."""
    try:
        init_workspace_tables(db_path)
        with _connect(db_path) as conn:
            row = conn.execute(
                "SELECT value FROM workspace_state WHERE key = 'pages'",
            ).fetchone()
        if row is not None:
            parsed = json.loads(row[0])
            if _is_valid_pages_state(parsed):
                return parsed

        legacy_layout = load_workspace_layout(db_path)
        wrapped = _wrap_layout_as_pages(legacy_layout if legacy_layout is not None else [])
        save_workspace_pages(wrapped, db_path)
        return wrapped
    except Exception:
        return None


def save_workspace_pages(pages_state: dict, db_path: Path = DEFAULT_DB_PATH) -> None:
    """Upsert the multi-page workspace state. Last-write-wins."""
    init_workspace_tables(db_path)
    now = datetime.now(UTC).isoformat()
    value = json.dumps(pages_state)
    with _connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO workspace_state (key, value, updated_at)
            VALUES ('pages', ?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value,
                                           updated_at = excluded.updated_at
            """,
            (value, now),
        )
        conn.commit()
