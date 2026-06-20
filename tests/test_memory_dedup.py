"""Dedup-on-write for the local memory store."""

from __future__ import annotations

import db.ai_tables as ai_tables


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


def test_create_memory_keeps_distinct_content(tmp_path) -> None:
    db = tmp_path / "ai.db"
    ai_tables.create_memory(
        memory_type="preference", content="Cap single names at 20%", db_path=db
    )
    ai_tables.create_memory(
        memory_type="preference", content="Risk no more than 5% per trade", db_path=db
    )
    assert len(ai_tables.list_memories(memory_type="preference", db_path=db)) == 2


def test_dedup_is_per_type(tmp_path) -> None:
    db = tmp_path / "ai.db"
    ai_tables.create_memory(memory_type="preference", content="Same text", db_path=db)
    ai_tables.create_memory(memory_type="lesson", content="Same text", db_path=db)
    assert len(ai_tables.list_memories(db_path=db)) == 2
