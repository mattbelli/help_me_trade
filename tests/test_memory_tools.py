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


async def test_save_memory_with_quote_never_mints_authority(tool_db: Path) -> None:
    """GATING-1 regression: the quote is a model-controlled argument — a
    hallucinated quote must not create a user_authored (proposal-shaping)
    row. Promotion happens only via the founder's Confirm tap."""
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
    assert row["authority"] == "agent_inferred"
    assert row["source"] == "user_explicit"
    assert row["quote"] == "remember I never trade the first 15 minutes"
    assert row["type"] == "directive"


async def test_forget_memory_refuses_protected_rows(tool_db: Path) -> None:
    """GATING-2 regression: the copilot cannot archive pinned or
    user_authored rows — that's the founder's widget-only power."""
    protected = ai_tables.create_memory(
        memory_type="directive",
        content="Never propose shorts",
        authority="user_authored",
        source="widget_manual",
        db_path=tool_db,
    )
    result = await execute_tool("forget_memory", {"id": protected["id"]})
    assert "error" in result
    row = ai_tables.get_memory(protected["id"], db_path=tool_db)
    assert row["status"] == "active"


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


def test_patch_authority_transitions_locked_down(mutation_client) -> None:
    """GATING-3 regression: the ONLY authority transition the API accepts is
    the founder's Confirm tap (agent_inferred -> user_confirmed).

    Rows are created in the process-default DB (conftest redirects it to a
    pytest temp file) — the same DB the API's memory service reads."""
    inferred = ai_tables.create_memory(
        memory_type="preference",
        content="Prefers batched fill pushes",
    )
    authored = ai_tables.create_memory(
        memory_type="directive",
        content="Never propose shorts",
        authority="user_authored",
        source="widget_manual",
    )

    # Minting user_authored over HTTP: rejected.
    r = mutation_client.patch(
        f"/api/memories/{inferred['id']}", json={"authority": "user_authored"}
    )
    assert r.status_code == 400

    # Confirming an agent_inferred row: the one legal transition.
    r = mutation_client.patch(
        f"/api/memories/{inferred['id']}", json={"authority": "user_confirmed"}
    )
    assert r.status_code == 200
    assert (
        ai_tables.get_memory(inferred["id"])["authority"] == "user_confirmed"
    )

    # Re-confirming a non-inferred row (incl. downgrades): rejected.
    r = mutation_client.patch(
        f"/api/memories/{authored['id']}", json={"authority": "user_confirmed"}
    )
    assert r.status_code == 400
    assert (
        ai_tables.get_memory(authored["id"])["authority"] == "user_authored"
    )
