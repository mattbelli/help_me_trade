"""Chat session persistence (pin, ordering)."""

from __future__ import annotations

from pathlib import Path

from db.base import connect
from db.chat_sessions import (
    create_chat_session,
    init_chat_tables,
    list_chat_sessions,
    set_chat_session_pinned,
)


def test_new_session_pinned_false_by_default(tmp_path: Path) -> None:
    db = tmp_path / "chat.db"
    session = create_chat_session(mode="saved", title="Alpha", db_path=db)
    assert session["pinned"] is False


def test_set_chat_session_pinned_toggles(tmp_path: Path) -> None:
    db = tmp_path / "chat.db"
    session = create_chat_session(mode="saved", title="Beta", db_path=db)

    pinned = set_chat_session_pinned(session["id"], True, db_path=db)
    assert pinned is not None
    assert pinned["pinned"] is True

    unpinned = set_chat_session_pinned(session["id"], False, db_path=db)
    assert unpinned is not None
    assert unpinned["pinned"] is False


def test_list_chat_sessions_pinned_first(tmp_path: Path) -> None:
    db = tmp_path / "chat.db"
    older = create_chat_session(mode="saved", title="Older", db_path=db)
    newer = create_chat_session(mode="saved", title="Newer", db_path=db)

    set_chat_session_pinned(older["id"], True, db_path=db)

    sessions = list_chat_sessions(db_path=db)
    assert [s["id"] for s in sessions] == [older["id"], newer["id"]]


def test_set_chat_session_pinned_missing_returns_none(tmp_path: Path) -> None:
    db = tmp_path / "chat.db"
    assert set_chat_session_pinned("nonexistent", True, db_path=db) is None


def test_init_chat_tables_migrates_pinned_column_idempotently(
    tmp_path: Path,
) -> None:
    db = tmp_path / "legacy.db"
    with connect(db) as conn:
        conn.executescript(
            """
            CREATE TABLE chat_sessions (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL DEFAULT 'New chat',
                mode TEXT NOT NULL DEFAULT 'standard',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            """
        )
        conn.commit()

    init_chat_tables(db)
    init_chat_tables(db)

    with connect(db) as conn:
        columns = {
            row[1] for row in conn.execute("PRAGMA table_info(chat_sessions)")
        }
    assert "pinned" in columns
