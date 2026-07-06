"""Exact-match dedup and correction preservation for the local memory store."""

from __future__ import annotations

import db.ai_tables as ai_tables
from ai.chat.memory_extract import extract_memories_from_turn


def test_create_memory_exact_match_noop_preserves_updated_at(tmp_path) -> None:
    db = tmp_path / "ai.db"
    a = ai_tables.create_memory(
        memory_type="preference", content="Cap single names at 20%", db_path=db
    )
    b = ai_tables.create_memory(
        memory_type="preference", content="  cap single names at 20%.  ", db_path=db
    )
    assert a["id"] == b["id"]
    assert a["updated_at"] == b["updated_at"]
    prefs = ai_tables.list_memories(memory_type="preference", db_path=db)
    assert len(prefs) == 1


def test_create_memory_correction_not_swallowed_by_similar_content(tmp_path) -> None:
    """Regression: Jaccard dedup used to discard 25% cap when 20% cap existed."""
    db = tmp_path / "ai.db"
    first = ai_tables.create_memory(
        memory_type="directive",
        content="Cap single names at 20% of portfolio",
        db_path=db,
    )
    second = ai_tables.create_memory(
        memory_type="directive",
        content="Cap single names at 25% of portfolio",
        db_path=db,
    )
    assert first["id"] != second["id"]
    assert second["content"] == "Cap single names at 25% of portfolio"
    rows = ai_tables.list_memories(memory_type="directive", db_path=db)
    assert len(rows) == 2
    contents = {row["content"] for row in rows}
    assert "Cap single names at 20% of portfolio" in contents
    assert "Cap single names at 25% of portfolio" in contents


def test_create_memory_reworded_variants_are_distinct_rows(tmp_path) -> None:
    db = tmp_path / "ai.db"
    ai_tables.create_memory(
        memory_type="preference", content="Cap single names at 20%", db_path=db
    )
    ai_tables.create_memory(memory_type="preference", content="20% cap", db_path=db)
    ai_tables.create_memory(
        memory_type="preference",
        content="I cap single positions at 20%",
        db_path=db,
    )
    assert len(ai_tables.list_memories(memory_type="preference", db_path=db)) == 3


def test_create_memory_keeps_distinct_content(tmp_path) -> None:
    db = tmp_path / "ai.db"
    ai_tables.create_memory(
        memory_type="preference", content="Cap single names at 20%", db_path=db
    )
    ai_tables.create_memory(
        memory_type="preference", content="Risk no more than 5% per trade", db_path=db
    )
    assert len(ai_tables.list_memories(memory_type="preference", db_path=db)) == 2


def test_create_memory_does_not_merge_on_percent_only_overlap(tmp_path) -> None:
    db = tmp_path / "ai.db"
    ai_tables.create_memory(
        memory_type="preference", content="Cap positions at 20%", db_path=db
    )
    ai_tables.create_memory(
        memory_type="preference", content="Risk 20% per trade", db_path=db
    )
    assert len(ai_tables.list_memories(memory_type="preference", db_path=db)) == 2


def test_dedup_is_per_type(tmp_path) -> None:
    db = tmp_path / "ai.db"
    ai_tables.create_memory(memory_type="preference", content="Same text", db_path=db)
    ai_tables.create_memory(memory_type="lesson", content="Same text", db_path=db)
    assert len(ai_tables.list_memories(db_path=db)) == 2


def test_list_memories_excludes_archived_by_default(tmp_path) -> None:
    db = tmp_path / "ai.db"
    import db.base as db_base

    ai_tables.init_ai_tables(db)
    active = ai_tables.create_memory(
        memory_type="preference", content="Active row", db_path=db
    )
    with db_base.connect(db) as conn:
        conn.execute(
            "INSERT INTO memories (memory_type, content, metadata_json, created_at, updated_at, status) "
            "VALUES (?, ?, '{}', ?, ?, 'archived')",
            ("research", "Archived legacy row", active["created_at"], active["updated_at"]),
        )
        conn.commit()

    assert len(ai_tables.list_memories(db_path=db)) == 1
    assert ai_tables.list_memories(db_path=db)[0]["content"] == "Active row"
    assert len(ai_tables.list_memories(db_path=db, include_inactive=True)) == 2


def test_extract_saves_genuinely_new_preference(tmp_path, monkeypatch) -> None:
    db = tmp_path / "ai.db"
    import db.base as db_base

    monkeypatch.setattr(db_base, "DEFAULT_DB_PATH", db)
    import ai.memories as mem_mod

    mem_mod._memory_service = None

    ai_tables.create_memory(
        memory_type="preference", content="Cap single names at 20%", db_path=db
    )

    monkeypatch.setattr(
        "ai.chat.memory_extract.complete_for_extraction",
        lambda *_args, **_kwargs: (
            '{"memories": [{"type": "preference", '
            '"content": "Risk no more than 5% per trade"}]}'
        ),
    )

    saved = extract_memories_from_turn(
        user_text="I risk no more than 5% per trade.",
        assistant_text="Got it — 5% max risk per trade.",
        session_id="sess_new",
    )

    assert len(saved) == 1
    assert saved[0]["content"] == "Risk no more than 5% per trade"
    assert len(ai_tables.list_memories(memory_type="preference", db_path=db)) == 2


def test_extract_returns_empty_on_llm_failure(tmp_path, monkeypatch) -> None:
    db = tmp_path / "ai.db"
    import db.base as db_base

    monkeypatch.setattr(db_base, "DEFAULT_DB_PATH", db)
    import ai.memories as mem_mod

    mem_mod._memory_service = None

    def _boom(*_args, **_kwargs):
        raise RuntimeError("llm down")

    monkeypatch.setattr(
        "ai.chat.memory_extract.complete_for_extraction",
        _boom,
    )

    saved = extract_memories_from_turn(
        user_text="I prefer round-number stops.",
        assistant_text="Noted.",
        session_id="sess_fail",
    )

    assert saved == []
    assert ai_tables.list_memories(db_path=db) == []


def test_legacy_rows_survive_fts_backfill_update_delete(tmp_path) -> None:
    """Regression: the external-content FTS5 backfill must index rows that
    existed before the FTS table did. The original WHERE-NOT-EXISTS backfill
    was a silent no-op (rowid lookups on an external-content table proxy to
    the content table), leaving the index empty — the update/delete triggers
    then raised 'database disk image is malformed' on every legacy row."""
    import sqlite3

    db = tmp_path / "ai.db"
    conn = sqlite3.connect(db)
    conn.execute(
        """
        CREATE TABLE memories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            memory_type TEXT NOT NULL,
            content TEXT NOT NULL,
            metadata_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        "INSERT INTO memories (memory_type, content, metadata_json, created_at, updated_at)"
        " VALUES ('preference', 'Cap single names at 20%', '{}',"
        " '2026-06-20T00:00:00', '2026-06-20T00:00:00')"
    )
    conn.commit()
    legacy_id = conn.execute("SELECT id FROM memories").fetchone()[0]
    conn.close()

    ai_tables.init_ai_tables(db)

    check = sqlite3.connect(db)
    hits = check.execute(
        "SELECT rowid FROM memories_fts WHERE memories_fts MATCH 'cap'"
    ).fetchall()
    check.close()
    assert hits == [(legacy_id,)]

    updated = ai_tables.update_memory(
        legacy_id, content="Cap single names at 25%", db_path=db
    )
    assert updated is not None
    assert ai_tables.delete_memory(legacy_id, db_path=db) is True
