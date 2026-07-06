"""The save_memory / forget_memory chat tools (design §4.4)."""

from __future__ import annotations

from pathlib import Path

import pytest

import ai.memories as mem_mod
import db.ai_tables as ai_tables
import db.base as db_base
from ai.chat.tools import execute_tool


@pytest.fixture
def tool_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    db = tmp_path / "tools.db"
    monkeypatch.setattr(db_base, "DEFAULT_DB_PATH", db)
    mem_mod._memory_service = None  # reset singleton onto the temp db
    ai_tables.init_ai_tables(db)
    return db


async def test_save_memory_with_quote_is_user_authored(tool_db: Path) -> None:
    result = await execute_tool(
        "save_memory",
        {
            "content": "Never trade the first 15 minutes",
            "type": "directive",
            "quote": "remember I never trade the first 15 minutes",
        },
    )
    assert result["status"] == "saved"
    assert result["message"] == f"Saved [mem:{result['id']}]."
    row = ai_tables.get_memory(result["id"], db_path=tool_db)
    assert row["authority"] == "user_authored"
    assert row["source"] == "user_explicit"
    assert row["quote"] == "remember I never trade the first 15 minutes"
    assert row["type"] == "directive"


async def test_save_memory_without_quote_is_agent_inferred(tool_db: Path) -> None:
    result = await execute_tool(
        "save_memory",
        {"content": "SLB: oversold on oil-capex fears", "type": "thesis"},
    )
    assert result["status"] == "saved"
    row = ai_tables.get_memory(result["id"], db_path=tool_db)
    assert row["authority"] == "agent_inferred"
    assert row["source"] == "agent_save"


async def test_save_memory_rejects_bad_type(tool_db: Path) -> None:
    result = await execute_tool(
        "save_memory", {"content": "x", "type": "research"}
    )
    assert "error" in result


async def test_forget_memory_archives_with_undo(tool_db: Path) -> None:
    row = ai_tables.create_memory(
        memory_type="preference", content="Likes round-number stops", db_path=tool_db
    )
    result = await execute_tool(
        "forget_memory", {"id": row["id"], "reason": "changed his mind"}
    )
    assert result["status"] == "archived"
    assert "undo" in result  # archive-only + an undo affordance (§7)
    archived = ai_tables.get_memory(row["id"], db_path=tool_db)
    assert archived["status"] == "archived"
    assert archived["metadata"]["forget_reason"] == "changed his mind"
    # Archived, not destroyed: it disappears from the active list but is restorable.
    assert ai_tables.list_memories(db_path=tool_db) == []
    assert len(ai_tables.list_memories(include_inactive=True, db_path=tool_db)) == 1


async def test_forget_missing_memory_errors(tool_db: Path) -> None:
    result = await execute_tool("forget_memory", {"id": 424242, "reason": "gone"})
    assert result["error"] == "not found"
