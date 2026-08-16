"""Workspace layout / pages / workspaces / custom-theme routes + the kanban board API.

Extracted verbatim from ``api/main.py`` (2026-08-16 god-module split). Registered by
``api/main`` via ``app.include_router`` (behind SecurityMiddleware). The board reads
``References/board.json`` (+ ``board_archive.json``) — the paths are module constants
here so tests can monkeypatch ``api.workspace_routes.BOARD_JSON_PATH``.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import time
from datetime import date, datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Body, HTTPException
from fastapi.responses import JSONResponse

from db.workspace import (
    is_valid_custom_themes,
    load_custom_themes,
    load_workspace_layout,
    load_workspace_pages,
    save_custom_themes,
    save_workspace_layout,
    save_workspace_pages,
)

logger = logging.getLogger(__name__)
router = APIRouter(tags=["workspace", "board"])

ROOT = Path(__file__).resolve().parent.parent
BOARD_JSON_PATH = ROOT / "References" / "board.json"
BOARD_ARCHIVE_PATH = ROOT / "References" / "board_archive.json"
_EMPTY_BOARD: dict = {"updated_at": None, "lanes": [], "cards": []}


@router.get("/api/workspace/layout")
def api_get_workspace_layout():
    return {"layout": load_workspace_layout()}


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


@router.put("/api/workspace/layout")
def api_put_workspace_layout(body: dict = Body(...)):
    layout = body.get("layout")
    if not isinstance(layout, list):
        return JSONResponse({"error": "layout must be an array"}, status_code=400)
    for item in layout:
        if not _is_valid_layout_item(item):
            return JSONResponse(
                {"error": ("each layout item must have string i, type and numeric x, y, w, h")},
                status_code=400,
            )
    save_workspace_layout(layout)
    return {"ok": True}


def _is_valid_workspace_page(page: object) -> bool:
    if not isinstance(page, dict):
        return False
    layout = page.get("layout")
    if not isinstance(page.get("id"), str) or not isinstance(page.get("name"), str):
        return False
    if not isinstance(layout, list):
        return False
    return all(_is_valid_layout_item(item) for item in layout)


def _is_valid_pages_state(state: object) -> bool:
    if not isinstance(state, dict):
        return False
    pages = state.get("pages")
    active_page_id = state.get("activePageId")
    if not isinstance(pages, dict) or not isinstance(active_page_id, str):
        return False
    if not pages:
        return False
    if active_page_id not in pages:
        return False
    return all(_is_valid_workspace_page(page) for page in pages.values())


def _is_valid_workspaces_state(state: object) -> bool:
    from db.workspace import _is_valid_workspaces_state as _valid

    return _valid(state)


@router.get("/api/workspace/workspaces")
def api_get_workspaces():
    from db.workspace import load_workspaces

    return {"workspaces_state": load_workspaces()}


@router.put("/api/workspace/workspaces")
def api_put_workspaces(body: dict = Body(...)):
    from db.workspace import save_workspaces

    workspaces_state = body.get("workspaces_state")
    if not _is_valid_workspaces_state(workspaces_state):
        return JSONResponse(
            {
                "error": (
                    "workspaces_state must be v2: version=2, a pages library "
                    "(1-24 valid pages), a workspaces map (≤8, each with string "
                    "id, name, 1-5 unique pageIds that exist in the library and "
                    "an activePageId among them) and a defaultWorkspaceId that "
                    "exists in the map"
                )
            },
            status_code=400,
        )
    save_workspaces(workspaces_state)
    return {"ok": True}


@router.get("/api/workspace/pages")
def api_get_workspace_pages():
    return {"pages_state": load_workspace_pages()}


@router.put("/api/workspace/pages")
def api_put_workspace_pages(body: dict = Body(...)):
    pages_state = body.get("pages_state")
    if not _is_valid_pages_state(pages_state):
        return JSONResponse(
            {
                "error": (
                    "pages_state must have pages map, activePageId, and each page "
                    "must have string id, name and a layout array of valid items"
                )
            },
            status_code=400,
        )
    save_workspace_pages(pages_state)
    return {"ok": True}


def load_board() -> dict:
    """Read project kanban board from References/board.json (fresh each call).

    Settled `done` cards live in board_archive.json (swept there by tools/board.py
    archive, so the live file stays small and merges cleanly); they are appended here
    so the API response — and therefore the Archive column — is unchanged by the split.
    """
    logger = logging.getLogger(__name__)
    try:
        if not BOARD_JSON_PATH.is_file():
            logger.warning("board.json missing at %s", BOARD_JSON_PATH)
            return dict(_EMPTY_BOARD)
        parsed = json.loads(BOARD_JSON_PATH.read_text(encoding="utf-8"))
        if not isinstance(parsed, dict):
            logger.warning("board.json invalid: root is not an object")
            return dict(_EMPTY_BOARD)
        parsed["cards"] = list(parsed.get("cards") or []) + _load_archived_cards()
        return parsed
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("failed to load board.json: %s", exc)
        return dict(_EMPTY_BOARD)


def _load_archived_cards() -> list:
    """Archived `done` cards, or [] if the archive is missing/unreadable (read-only path)."""
    logger = logging.getLogger(__name__)
    try:
        if not BOARD_ARCHIVE_PATH.is_file():
            return []
        parsed = json.loads(BOARD_ARCHIVE_PATH.read_text(encoding="utf-8"))
        cards = parsed.get("cards") if isinstance(parsed, dict) else None
        return cards if isinstance(cards, list) else []
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("failed to load board_archive.json: %s", exc)
        return []


def _load_board_for_mutation() -> dict:
    """Load board.json for writes; 409 if missing or corrupt.

    Deliberately NOT load_board(): the write path must see the live file alone, or
    saving would fold every archived card back into board.json.
    """
    logger = logging.getLogger(__name__)
    if not BOARD_JSON_PATH.is_file():
        raise HTTPException(
            status_code=409,
            detail="board.json is missing; an agent must restore it before adding inbox cards",
        )
    try:
        parsed = json.loads(BOARD_JSON_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("failed to load board.json for mutation: %s", exc)
        raise HTTPException(
            status_code=409,
            detail=f"board.json is corrupt or unreadable: {exc}",
        ) from exc
    if not isinstance(parsed, dict):
        raise HTTPException(
            status_code=409,
            detail="board.json is corrupt: root is not an object",
        )
    return parsed


def _save_board_atomic(board: dict) -> None:
    """Atomically replace board.json (safe under concurrent agent edits).

    indent=1 + ensure_ascii=False is the board's canonical on-disk format, shared with
    tools/board.py. Any other dump re-writes all ~2,700 lines, and a whole-file reformat
    conflicts with every open branch — this endpoint used to do exactly that.
    """
    content = json.dumps(board, indent=1, ensure_ascii=False) + "\n"
    fd, tmp_path = tempfile.mkstemp(
        dir=BOARD_JSON_PATH.parent,
        prefix=".board-",
        suffix=".tmp",
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
        os.replace(tmp_path, BOARD_JSON_PATH)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


@router.get("/api/board")
def api_get_board():
    return {"board": load_board()}


@router.post("/api/board/inbox")
def api_post_board_inbox(body: dict = Body(...)):
    title_raw = body.get("title")
    if not isinstance(title_raw, str):
        raise HTTPException(status_code=400, detail="title is required")
    title = title_raw.strip()
    if not title:
        raise HTTPException(status_code=400, detail="title is required")
    if len(title) > 200:
        raise HTTPException(status_code=400, detail="title must be at most 200 characters")

    notes_raw = body.get("notes", "")
    if notes_raw is None:
        notes_raw = ""
    if not isinstance(notes_raw, str):
        raise HTTPException(status_code=400, detail="notes must be a string")
    notes = notes_raw.strip()
    if len(notes) > 500:
        raise HTTPException(status_code=400, detail="notes must be at most 500 characters")

    board = _load_board_for_mutation()

    lanes = board.get("lanes")
    if not isinstance(lanes, list):
        lanes = []
        board["lanes"] = lanes
    if "inbox" not in lanes:
        lanes.insert(0, "inbox")

    cards = board.get("cards")
    if not isinstance(cards, list):
        cards = []
        board["cards"] = cards

    card = {
        "id": f"fr-{int(time.time())}",
        "title": title,
        "lane": "inbox",
        "type": "idea",
        "priority": "normal",
        "notes": notes,
        "owner": "founder",
        "updated_at": date.today().isoformat(),
    }
    cards.append(card)
    board["updated_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    _save_board_atomic(board)
    return {"ok": True, "card": card}


@router.get("/api/workspace/custom-themes")
def api_get_custom_themes():
    return {"custom_themes": load_custom_themes()}


@router.put("/api/workspace/custom-themes")
def api_put_custom_themes(body: dict = Body(...)):
    themes = body.get("custom_themes")
    if not is_valid_custom_themes(themes):
        return JSONResponse(
            {
                "error": (
                    "custom_themes must be a list (≤24) of objects with string "
                    "id, name and a vars map of string→string"
                )
            },
            status_code=400,
        )
    save_custom_themes(themes)
    return {"ok": True}
