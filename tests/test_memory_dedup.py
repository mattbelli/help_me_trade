"""Dedup-on-write for the local memory store."""

from __future__ import annotations

import ai.memories as mem_mod
import db.ai_tables as ai_tables
from ai.chat.memory_extract import extract_memories_from_turn


def test_create_memory_dedups_near_identical(tmp_path) -> None:
    db = tmp_path / "ai.db"
    a = ai_tables.create_memory(
        memory_type="preference", content="Cap single names at 20%", db_path=db
    )
    # Same fact, different case/punctuation/whitespace -> refreshed, not duplicated.
    b = ai_tables.create_memory(
        memory_type="preference", content="  cap single names at 20%.  ", db_path=db
    )
    assert a["id"] == b["id"]
    prefs = ai_tables.list_memories(memory_type="preference", db_path=db)
    assert len(prefs) == 1


def test_create_memory_merges_reworded_variants(tmp_path) -> None:
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
    assert len(ai_tables.list_memories(memory_type="preference", db_path=db)) == 1


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


def test_extract_drops_candidate_matching_existing_memory(
    tmp_path, monkeypatch
) -> None:
    db = tmp_path / "ai.db"
    import db.base as db_base

    monkeypatch.setattr(db_base, "DEFAULT_DB_PATH", db)
    mem_mod._memory_service = None

    ai_tables.create_memory(
        memory_type="preference", content="Cap single names at 20%", db_path=db
    )

    monkeypatch.setattr(
        "ai.chat.memory_extract.complete_for_extraction",
        lambda *_args, **_kwargs: (
            '{"memories": [{"type": "preference", "content": "20% cap"}]}'
        ),
    )

    saved = extract_memories_from_turn(
        user_text="Thanks for the reminder.",
        assistant_text="You cap single names at 20%.",
        session_id="sess_echo",
    )

    assert saved == []
    assert len(ai_tables.list_memories(memory_type="preference", db_path=db)) == 1


def test_extract_saves_genuinely_new_preference(tmp_path, monkeypatch) -> None:
    db = tmp_path / "ai.db"
    import db.base as db_base

    monkeypatch.setattr(db_base, "DEFAULT_DB_PATH", db)
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
