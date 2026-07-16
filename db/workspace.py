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
                "name": "Page 1",
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


MAX_WORKSPACES = 8
MAX_PAGES_PER_WORKSPACE = 5
MAX_LIBRARY_PAGES = 24
DEFAULT_WORKSPACE_ID = "ws-default"


def _is_valid_page(page: object) -> bool:
    if not isinstance(page, dict):
        return False
    if not isinstance(page.get("id"), str) or not isinstance(page.get("name"), str):
        return False
    layout = page.get("layout")
    if not isinstance(layout, list):
        return False
    return all(_is_valid_layout_item(item) for item in layout)


def _is_valid_workspaces_state(state: object) -> bool:
    """v2: a shared page LIBRARY + workspaces referencing pages by id (linked)."""
    if not isinstance(state, dict) or state.get("version") != 2:
        return False
    pages = state.get("pages")
    workspaces = state.get("workspaces")
    default_id = state.get("defaultWorkspaceId")
    if (
        not isinstance(pages, dict)
        or not isinstance(workspaces, dict)
        or not isinstance(default_id, str)
    ):
        return False
    if not pages or len(pages) > MAX_LIBRARY_PAGES:
        return False
    if not all(_is_valid_page(page) for page in pages.values()):
        return False
    if not workspaces or len(workspaces) > MAX_WORKSPACES:
        return False
    for ws in workspaces.values():
        if not isinstance(ws, dict):
            return False
        if not isinstance(ws.get("id"), str) or not isinstance(ws.get("name"), str):
            return False
        page_ids = ws.get("pageIds")
        if not isinstance(page_ids, list) or not page_ids:
            return False
        if len(page_ids) > MAX_PAGES_PER_WORKSPACE:
            return False
        if len(set(page_ids)) != len(page_ids):
            return False
        if not all(isinstance(pid, str) and pid in pages for pid in page_ids):
            return False
        if ws.get("activePageId") not in page_ids:
            return False
    return default_id in workspaces


def _is_valid_workspaces_state_v1(state: object) -> bool:
    """v1 (lived one night): each workspace embedded its own pages_state."""
    if not isinstance(state, dict) or "version" in state:
        return False
    workspaces = state.get("workspaces")
    default_id = state.get("defaultWorkspaceId")
    if not isinstance(workspaces, dict) or not isinstance(default_id, str):
        return False
    if not workspaces or len(workspaces) > MAX_WORKSPACES:
        return False
    for ws in workspaces.values():
        if not isinstance(ws, dict):
            return False
        if not isinstance(ws.get("id"), str) or not isinstance(ws.get("name"), str):
            return False
        if not _is_valid_pages_state(ws.get("pages_state")):
            return False
    return default_id in workspaces


def _migrate_workspaces_v1(v1: dict) -> dict:
    """Hoist v1 embedded pages into the shared library; ids kept where unique."""
    library: dict = {}
    workspaces: dict = {}
    counter = 1
    for ws in v1["workspaces"].values():
        id_map: dict = {}
        for old_id, page in ws["pages_state"]["pages"].items():
            new_id = old_id
            while new_id in library:
                new_id = f"pg-{counter}"
                counter += 1
            id_map[old_id] = new_id
            library[new_id] = {**page, "id": new_id}
        page_ids = [id_map[pid] for pid in ws["pages_state"]["pages"]]
        active = id_map.get(ws["pages_state"]["activePageId"], page_ids[0])
        workspaces[ws["id"]] = {
            "id": ws["id"],
            "name": ws["name"],
            "pageIds": page_ids,
            "activePageId": active,
        }
    return {
        "version": 2,
        "pages": library,
        "workspaces": workspaces,
        "defaultWorkspaceId": v1["defaultWorkspaceId"],
    }


def _wrap_pages_as_workspaces(pages_state: dict) -> dict:
    return _migrate_workspaces_v1(
        {
            "workspaces": {
                DEFAULT_WORKSPACE_ID: {
                    "id": DEFAULT_WORKSPACE_ID,
                    "name": "Main",
                    "pages_state": pages_state,
                }
            },
            "defaultWorkspaceId": DEFAULT_WORKSPACE_ID,
        }
    )


def load_workspaces(db_path: Path = DEFAULT_DB_PATH) -> dict | None:
    """Return stored workspaces state (v2), auto-migrating older blobs.

    Migration chain: v2 blob → v1 blob (hoist embedded pages into the library)
    → legacy pages blob (wrap as a single "Main" workspace). The migrated
    result is persisted so the chain runs once.
    """
    try:
        init_workspace_tables(db_path)
        with _connect(db_path) as conn:
            row = conn.execute(
                "SELECT value FROM workspace_state WHERE key = 'workspaces'",
            ).fetchone()
        if row is not None:
            parsed = json.loads(row[0])
            if _is_valid_workspaces_state(parsed):
                return parsed
            if _is_valid_workspaces_state_v1(parsed):
                state = _migrate_workspaces_v1(parsed)
                save_workspaces(state, db_path)
                return state

        pages_state = load_workspace_pages(db_path)
        if pages_state is None:
            return None
        state = _wrap_pages_as_workspaces(pages_state)
        save_workspaces(state, db_path)
        return state
    except Exception:
        return None


def save_workspaces(state: dict, db_path: Path = DEFAULT_DB_PATH) -> None:
    """Upsert the workspaces state. Last-write-wins."""
    init_workspace_tables(db_path)
    now = datetime.now(UTC).isoformat()
    value = json.dumps(state)
    with _connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO workspace_state (key, value, updated_at)
            VALUES ('workspaces', ?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value,
                                           updated_at = excluded.updated_at
            """,
            (value, now),
        )
        conn.commit()


MAX_CUSTOM_THEMES = 24


def _is_valid_custom_theme(theme: object) -> bool:
    if not isinstance(theme, dict):
        return False
    if not isinstance(theme.get("id"), str) or not isinstance(theme.get("name"), str):
        return False
    variables = theme.get("vars")
    if not isinstance(variables, dict):
        return False
    return all(
        isinstance(k, str) and isinstance(v, str) for k, v in variables.items()
    )


def is_valid_custom_themes(themes: object) -> bool:
    if not isinstance(themes, list):
        return False
    if len(themes) > MAX_CUSTOM_THEMES:
        return False
    return all(_is_valid_custom_theme(theme) for theme in themes)


def load_custom_themes(db_path: Path = DEFAULT_DB_PATH) -> list:
    """Return the stored custom themes list, or [] if absent/invalid (fail-soft)."""
    try:
        init_workspace_tables(db_path)
        with _connect(db_path) as conn:
            row = conn.execute(
                "SELECT value FROM workspace_state WHERE key = 'custom_themes'",
            ).fetchone()
        if row is None:
            return []
        parsed = json.loads(row[0])
        if not is_valid_custom_themes(parsed):
            return []
        return parsed
    except Exception:
        return []


def save_custom_themes(themes: list, db_path: Path = DEFAULT_DB_PATH) -> None:
    """Upsert the custom themes list. Last-write-wins."""
    init_workspace_tables(db_path)
    now = datetime.now(UTC).isoformat()
    value = json.dumps(themes)
    with _connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO workspace_state (key, value, updated_at)
            VALUES ('custom_themes', ?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value,
                                           updated_at = excluded.updated_at
            """,
            (value, now),
        )
        conn.commit()
