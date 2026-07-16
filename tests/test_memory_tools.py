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


async def test_forget_memory_refuses_user_confirmed_rows(tool_db: Path) -> None:
    """HIGH-2 regression: the copilot cannot archive a founder-confirmed rule on a
    hallucinated 'forget that' — only the founder can, from the widget."""
    confirmed = ai_tables.create_memory(
        memory_type="directive",
        content="Never trade earnings weeks",
        authority="user_confirmed",
        source="widget_confirm",
        db_path=tool_db,
    )
    result = await execute_tool("forget_memory", {"id": confirmed["id"]})
    assert "error" in result
    row = ai_tables.get_memory(confirmed["id"], db_path=tool_db)
    assert row["status"] == "active"


async def test_save_memory_with_quote_still_runs_the_judge(
    tool_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """HIGH-3 regression: a quote keeps never-NOOP semantics but does NOT skip the
    judge — the judge's LLM call must fire so dedup/UPDATE containment still runs.
    Here the judge NOOPs; never-NOOP still stores it, and the judge WAS invoked."""
    calls: list[int] = []

    class RecordingJudgeLLM:
        provider = "recording"

        def complete_json(self, messages, *, max_tokens=1024, temperature=0.1) -> str:
            calls.append(1)
            return '{"decisions": [{"index": 0, "action": "NOOP"}]}'

        def complete(self, messages, *, max_tokens=1024) -> str:
            calls.append(1)
            return '{"decisions": [{"index": 0, "action": "NOOP"}]}'

    monkeypatch.setattr("ai.memories.judge.get_llm", lambda *a, **k: RecordingJudgeLLM())
    result = await execute_tool(
        "save_memory",
        {
            "content": "Never trade the first 15 minutes",
            "type": "directive",
            "quote": "remember I never trade the first 15 minutes",
        },
    )
    # never-NOOP: the explicit save still lands even though the judge said NOOP...
    assert result["status"] == "saved"
    # ...but the judge's LLM was invoked — the quote did not bypass it.
    assert calls, "judge LLM was not called for a quote-carrying save_memory"


async def test_quote_save_capped_per_session(
    tool_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """M-2 regression: quote-backed saves ride the never-NOOP privilege lane, so
    they are capped at 5 per session — the 6th is rejected with a clear tool
    result and nothing stored. A different session is unaffected, and non-quote
    (judge-mediated) saves are never capped."""
    import ai.chat.tools as tools_mod

    monkeypatch.setattr(tools_mod, "_quote_save_counts", {})
    token = tools_mod.CHAT_SESSION_ID.set("session-a")
    try:
        for i in range(5):
            result = await execute_tool(
                "save_memory",
                {
                    "content": f"Directive number {i}: avoid overnight holds",
                    "type": "directive",
                    "quote": f"remember rule {i}",
                },
            )
            assert result["status"] == "saved", f"save {i} unexpectedly blocked"

        # 6th quote-save in the same session: rejected, nothing stored.
        before = len(ai_tables.list_memories(db_path=tool_db))
        result = await execute_tool(
            "save_memory",
            {
                "content": "Directive number 6: flooded row",
                "type": "directive",
                "quote": "remember rule 6",
            },
        )
        assert "error" in result
        assert "limit" in result["error"]
        assert len(ai_tables.list_memories(db_path=tool_db)) == before

        # A different session has its own allowance.
        tools_mod.CHAT_SESSION_ID.set("session-b")
        result = await execute_tool(
            "save_memory",
            {
                "content": "Directive from another chat: size down on Fridays",
                "type": "directive",
                "quote": "size down on fridays",
            },
        )
        assert result["status"] == "saved"

        # Non-quote saves (judge-mediated lane) are not capped, even past the cap.
        tools_mod.CHAT_SESSION_ID.set("session-a")
        result = await execute_tool(
            "save_memory",
            {"content": "SLB thesis: watching 43 support", "type": "thesis"},
        )
        assert result["status"] == "saved"
    finally:
        tools_mod.CHAT_SESSION_ID.reset(token)


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
