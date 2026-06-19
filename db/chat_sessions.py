"""SQLite persistence for agent chat sessions and messages."""

from __future__ import annotations

import json
import sqlite3
import uuid
from pathlib import Path
from typing import Any

from db.base import DEFAULT_DB_PATH
from db.base import connect as _connect
from db.base import now_iso as _now

CHAT_MODES = frozenset({"saved", "temporary", "standard", "private", "quick"})
PERSISTED_MODES = frozenset({"saved", "standard", "private"})


def init_chat_tables(db_path: Path = DEFAULT_DB_PATH) -> None:
    with _connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS chat_sessions (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL DEFAULT 'New chat',
                mode TEXT NOT NULL DEFAULT 'standard',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                pinned INTEGER NOT NULL DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS chat_messages (
                id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                role TEXT NOT NULL,
                parts_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY (session_id) REFERENCES chat_sessions(id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_chat_sessions_updated
                ON chat_sessions(updated_at DESC);
            CREATE INDEX IF NOT EXISTS idx_chat_messages_session
                ON chat_messages(session_id, created_at ASC);
            """
        )
        try:
            conn.execute(
                "ALTER TABLE chat_sessions ADD COLUMN pinned INTEGER NOT NULL DEFAULT 0"
            )
        except sqlite3.OperationalError:
            pass


def _row_to_session(row: sqlite3.Row) -> dict[str, Any]:
    keys = row.keys()
    return {
        "id": row["id"],
        "title": row["title"],
        "mode": row["mode"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "pinned": bool(row["pinned"]) if "pinned" in keys else False,
    }


def _row_to_message(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "role": row["role"],
        "parts": json.loads(row["parts_json"] or "[]"),
        "created_at": row["created_at"],
    }


def create_chat_session(
    *,
    mode: str = "standard",
    title: str = "New chat",
    session_id: str | None = None,
    db_path: Path = DEFAULT_DB_PATH,
) -> dict[str, Any]:
    if mode not in CHAT_MODES:
        raise ValueError(f"mode must be one of: {', '.join(sorted(CHAT_MODES))}")
    if mode in ("quick", "temporary"):
        raise ValueError("temporary chats are ephemeral and are not persisted")

    init_chat_tables(db_path)
    sid = session_id or f"chat_{uuid.uuid4().hex[:12]}"
    now = _now()
    with _connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO chat_sessions (id, title, mode, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (sid, title.strip() or "New chat", mode, now, now),
        )
        row = conn.execute(
            "SELECT * FROM chat_sessions WHERE id = ?", (sid,)
        ).fetchone()
        conn.commit()
    return _row_to_session(row)


def list_chat_sessions(
    *, limit: int = 50, db_path: Path = DEFAULT_DB_PATH
) -> list[dict[str, Any]]:
    init_chat_tables(db_path)
    with _connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT * FROM chat_sessions
            ORDER BY pinned DESC, updated_at DESC
            LIMIT ?
            """,
            (max(1, min(limit, 200)),),
        ).fetchall()
    return [_row_to_session(r) for r in rows]


def get_chat_session(
    session_id: str, db_path: Path = DEFAULT_DB_PATH
) -> dict[str, Any] | None:
    init_chat_tables(db_path)
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM chat_sessions WHERE id = ?", (session_id,)
        ).fetchone()
    return _row_to_session(row) if row else None


def get_chat_session_with_messages(
    session_id: str, db_path: Path = DEFAULT_DB_PATH
) -> dict[str, Any] | None:
    session = get_chat_session(session_id, db_path)
    if session is None:
        return None
    session["messages"] = list_chat_messages(session_id, db_path=db_path)
    return session


def list_chat_messages(
    session_id: str, db_path: Path = DEFAULT_DB_PATH
) -> list[dict[str, Any]]:
    init_chat_tables(db_path)
    with _connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT * FROM chat_messages
            WHERE session_id = ?
            ORDER BY created_at ASC
            """,
            (session_id,),
        ).fetchall()
    return [_row_to_message(r) for r in rows]


def save_chat_message(
    *,
    session_id: str,
    role: str,
    parts: list[dict[str, Any]],
    message_id: str | None = None,
    db_path: Path = DEFAULT_DB_PATH,
) -> dict[str, Any]:
    init_chat_tables(db_path)
    mid = message_id or f"msg_{uuid.uuid4().hex[:12]}"
    now = _now()
    with _connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO chat_messages (id, session_id, role, parts_json, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (mid, session_id, role, json.dumps(parts), now),
        )
        conn.execute(
            "UPDATE chat_sessions SET updated_at = ? WHERE id = ?",
            (now, session_id),
        )
        row = conn.execute(
            "SELECT * FROM chat_messages WHERE id = ?", (mid,)
        ).fetchone()
        conn.commit()
    return _row_to_message(row)


def upsert_chat_message(
    *,
    session_id: str,
    role: str,
    parts: list[dict[str, Any]],
    message_id: str,
    db_path: Path = DEFAULT_DB_PATH,
) -> dict[str, Any]:
    """Insert or update a chat message by id (for streaming/tool turns)."""
    init_chat_tables(db_path)
    now = _now()
    with _connect(db_path) as conn:
        existing = conn.execute(
            "SELECT id FROM chat_messages WHERE id = ?", (message_id,)
        ).fetchone()
        if existing:
            conn.execute(
                """
                UPDATE chat_messages
                SET parts_json = ?, created_at = ?
                WHERE id = ?
                """,
                (json.dumps(parts), now, message_id),
            )
        else:
            conn.execute(
                """
                INSERT INTO chat_messages (id, session_id, role, parts_json, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (message_id, session_id, role, json.dumps(parts), now),
            )
        conn.execute(
            "UPDATE chat_sessions SET updated_at = ? WHERE id = ?",
            (now, session_id),
        )
        row = conn.execute(
            "SELECT * FROM chat_messages WHERE id = ?", (message_id,)
        ).fetchone()
        conn.commit()
    return _row_to_message(row)


def set_chat_session_pinned(
    session_id: str, pinned: bool, db_path: Path = DEFAULT_DB_PATH
) -> dict[str, Any] | None:
    init_chat_tables(db_path)
    with _connect(db_path) as conn:
        conn.execute(
            "UPDATE chat_sessions SET pinned = ? WHERE id = ?",
            (1 if pinned else 0, session_id),
        )
        row = conn.execute(
            "SELECT * FROM chat_sessions WHERE id = ?", (session_id,)
        ).fetchone()
        conn.commit()
    return _row_to_session(row) if row else None


def update_chat_session_title(
    session_id: str, title: str, db_path: Path = DEFAULT_DB_PATH
) -> dict[str, Any] | None:
    init_chat_tables(db_path)
    now = _now()
    with _connect(db_path) as conn:
        conn.execute(
            "UPDATE chat_sessions SET title = ?, updated_at = ? WHERE id = ?",
            (title.strip() or "New chat", now, session_id),
        )
        row = conn.execute(
            "SELECT * FROM chat_sessions WHERE id = ?", (session_id,)
        ).fetchone()
        conn.commit()
    return _row_to_session(row) if row else None


def delete_chat_session(session_id: str, db_path: Path = DEFAULT_DB_PATH) -> bool:
    init_chat_tables(db_path)
    with _connect(db_path) as conn:
        cursor = conn.execute(
            "DELETE FROM chat_sessions WHERE id = ?", (session_id,)
        )
        conn.commit()
        return cursor.rowcount > 0


def touch_chat_session(session_id: str, db_path: Path = DEFAULT_DB_PATH) -> None:
    init_chat_tables(db_path)
    with _connect(db_path) as conn:
        conn.execute(
            "UPDATE chat_sessions SET updated_at = ? WHERE id = ?",
            (_now(), session_id),
        )
        conn.commit()


def title_from_first_message(text: str, *, max_len: int = 48) -> str:
    clean = " ".join(text.split())
    if not clean:
        return "New chat"
    if len(clean) <= max_len:
        return clean
    return clean[: max_len - 1].rstrip() + "…"
