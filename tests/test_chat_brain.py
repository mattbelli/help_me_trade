"""Tests for chat brain helpers (Next.js route host)."""

from __future__ import annotations

import pytest

from ai.chat.brain import (
    build_turn_context,
    finalize_turn,
    is_ephemeral_mode,
    normalize_chat_mode,
    run_tool,
    should_learn_from_turn,
    tool_catalog,
)


def test_normalize_chat_mode():
    assert normalize_chat_mode("quick") == "temporary"
    assert normalize_chat_mode("temporary") == "temporary"
    assert normalize_chat_mode("standard") == "saved"
    assert normalize_chat_mode("private") == "saved"
    assert normalize_chat_mode("saved") == "saved"


def test_is_ephemeral_mode():
    assert is_ephemeral_mode("quick") is True
    assert is_ephemeral_mode("temporary") is True
    assert is_ephemeral_mode("saved") is False


def test_should_learn_from_turn():
    assert should_learn_from_turn(mode="temporary") is False
    assert should_learn_from_turn(mode="saved") is True
    assert should_learn_from_turn(mode="private") is False


def test_build_turn_context_includes_system_prompt():
    ctx = build_turn_context("portfolio exposure")
    assert "Help Me Trade" in ctx["system"]
    assert ctx["memories"] == []
    assert "Relevant memories" not in ctx["system"]


def test_build_turn_context_injects_memories_when_enabled(monkeypatch):
    monkeypatch.setattr(
        "ai.chat.brain.turn_memory_context",
        lambda _text, **_: [{"type": "preference", "content": "Risk cap", "score": 0.9}],
    )
    ctx = build_turn_context("hello", inject_memories=True)
    assert "Relevant memories" in ctx["system"]
    assert len(ctx["memories"]) == 1


def test_injection_fence_wired_end_to_end(monkeypatch, tmp_path):
    """HIGH-3 integration: the injection fence + provenance chips are WIRED, not
    just defined. A real store (no stubs) holds an active user_confirmed row, an
    active agent_inferred row, an archived row, and an active unknown-authority
    row — build_turn_context(inject_memories=True) must emit the first two with
    their chips and never the last two."""
    import json
    import sqlite3

    import db.ai_tables as ai_tables
    import db.base as db_base

    db = tmp_path / "fence.db"
    monkeypatch.setattr(db_base, "DEFAULT_DB_PATH", db)
    ai_tables.init_ai_tables(db)

    confirmed = ai_tables.create_memory(
        memory_type="directive",
        content="Never trade earnings weeks",
        authority="user_confirmed",
        source="widget_confirm",
        db_path=db,
    )
    inferred = ai_tables.create_memory(
        memory_type="lesson",
        content="Tends to oversize earnings trades",
        authority="agent_inferred",
        source="reflection",
        db_path=db,
    )
    archived = ai_tables.create_memory(
        memory_type="directive",
        content="Earnings plays require defined risk",
        authority="user_authored",
        source="widget_manual",
        db_path=db,
    )
    ai_tables.update_memory(archived["id"], status="archived", db_path=db)
    spoofed = ai_tables.create_memory(
        memory_type="fact",
        content="Earnings volatility spoofed row",
        db_path=db,
    )
    # update_memory validates authority, so plant the unknown tier directly.
    with sqlite3.connect(db) as conn:
        conn.execute(
            "UPDATE memories SET authority = 'totally_trusted' WHERE id = ?",
            (spoofed["id"],),
        )

    ctx = build_turn_context(
        "how should I handle earnings trades this week?", inject_memories=True
    )
    system = ctx["system"]

    # The two fenced-in rows are present...
    assert confirmed["content"] in system
    assert inferred["content"] in system
    # ...the archived and unknown-authority rows never reach the prompt.
    assert archived["content"] not in system
    assert spoofed["content"] not in system

    # Provenance chips are bound to the right rows (parse the emitted JSON).
    tail = system.split("Relevant memories for this turn", 1)[1]
    emitted = json.loads(tail[tail.index("[") :])
    provenance = {item["content"]: item["provenance"] for item in emitted}
    assert provenance[confirmed["content"]] == "founder-confirmed"
    assert provenance[inferred["content"]] == "agent-inferred (unverified)"
    assert set(provenance) == {confirmed["content"], inferred["content"]}


def test_build_turn_context_injects_journal_when_enabled(monkeypatch):
    monkeypatch.setattr(
        "ai.chat.brain.turn_journal_context",
        lambda _text, **_: {
            "decisions": [
                {
                    "symbol": "NVDA",
                    "decision": "buy",
                    "outcome": "win",
                    "r_multiple": 1.2,
                }
            ],
            "ai_stats": {"n": 1, "wins": 1, "losses": 0, "avg_r": 1.2},
            "user_profile": "5 trades",
        },
    )
    ctx = build_turn_context("how did my NVDA trades go?", inject_journal=True)
    assert "Your track record for this turn" in ctx["system"]
    assert ctx["journal"]["decisions"]


def test_tool_catalog_includes_propose_order():
    names = {item["name"] for item in tool_catalog()}
    assert "propose_order" in names
    assert "get_portfolio_snapshot" in names
    assert "web_search" in names


@pytest.mark.asyncio
async def test_run_tool_web_search_requires_query():
    result = await run_tool("web_search", {})
    assert "error" in result


def test_build_web_query_scopes_sites() -> None:
    from ai.research.sources import _build_web_query

    q = _build_web_query("stocks trending", ["reddit.com", "x.com"])
    assert "site:reddit.com" in q
    assert "site:x.com" in q
    assert "stocks trending" in q


@pytest.mark.asyncio
async def test_run_tool_web_search_unconfigured_returns_error(monkeypatch):
    monkeypatch.setattr(
        "ai.research.sources.get_searxng_url",
        lambda: "",
    )
    result = await run_tool("web_search", {"query": "NVDA reddit"})
    assert result.get("error") == "searxng_not_configured"
    assert result.get("results") == []
    assert "limitations" in result


@pytest.mark.asyncio
async def test_run_tool_propose_order_requires_symbol():
    result = await run_tool("propose_order", {"side": "buy", "qty": 1})
    assert "error" in result


@pytest.mark.asyncio
async def test_run_tool_propose_order_ok(monkeypatch, tmp_path):
    from unittest.mock import AsyncMock

    from brokers.alpaca import AlpacaAdapter
    from brokers.base import Account
    from config import settings as settings_mod
    from db import base as db_base
    from db import order_executions as order_executions_mod
    from db import paper_orders as paper_orders_mod
    from db import sqlite as sqlite_mod

    db = tmp_path / "brain.db"
    monkeypatch.setattr(db_base, "DEFAULT_DB_PATH", db)
    monkeypatch.setattr("db.base.DEFAULT_DB_PATH", db)
    monkeypatch.setattr(sqlite_mod, "DEFAULT_DB_PATH", db)
    monkeypatch.setattr(order_executions_mod, "DEFAULT_DB_PATH", db)
    monkeypatch.setattr(paper_orders_mod, "DEFAULT_DB_PATH", db)

    account = Account(
        id="acc1",
        status="ACTIVE",
        cash=50_000.0,
        buying_power=100_000.0,
        portfolio_value=100_000.0,
    )
    sqlite_mod.save_snapshot(account, [], [], broker="alpaca", db_path=db)

    mock_broker = object.__new__(AlpacaAdapter)
    mock_broker.symbol_exists = lambda _s: True  # type: ignore[method-assign]
    monkeypatch.setattr("trading.risk_engine.get_broker", lambda _n: mock_broker)
    monkeypatch.setattr(
        "trading.risk_engine.market_hub.quote_snapshot",
        lambda _symbols: {"AAPL": {"price": 150.0}},
    )
    monkeypatch.setattr("trading.risk_engine.is_kill_switch_active", lambda: False)
    monkeypatch.setattr(
        "trading.risk_engine.trading_config",
        lambda: settings_mod.DEFAULT_SETTINGS["trading"],
    )
    monkeypatch.setattr("trading.execution_service.is_kill_switch_active", lambda: False)
    monkeypatch.setattr(
        "trading.execution_service.trading_config",
        lambda: settings_mod.DEFAULT_SETTINGS["trading"],
    )
    monkeypatch.setattr(
        "trading.execution_service.notify",
        AsyncMock(return_value={}),
    )

    result = await run_tool(
        "propose_order",
        {"symbol": "AAPL", "side": "buy", "qty": 2, "order_type": "market"},
    )
    assert result["status"] == "dry_run"
    assert result["proposal"]["symbol"] == "AAPL"


def test_build_turn_context_order_intent_adds_block():
    ctx = build_turn_context("Buy 100 shares of NVDA at market open")
    assert "ORDER REQUEST" in ctx["system"]
    assert "propose_order" in ctx["system"]


def test_build_turn_context_non_order_no_block():
    ctx = build_turn_context("What is a limit order?")
    assert "ORDER REQUEST" not in ctx["system"]


def test_looks_like_order_intent():
    from ai.chat.intent import looks_like_order_intent

    assert looks_like_order_intent("Buy 100 shares of NVDA") is True
    assert looks_like_order_intent("place an order for 100 nvda shares at open") is True
    assert looks_like_order_intent("sell my AAPL position") is True
    assert looks_like_order_intent("What is P/E ratio?") is False


def test_finalize_turn_persists_assistant_tool_parts(tmp_path, monkeypatch):
    from db import paper_orders as paper_orders_mod
    from db.chat_sessions import (
        create_chat_session,
        get_chat_session,
        get_chat_session_with_messages,
        upsert_chat_message,
    )

    db = tmp_path / "chat.db"
    monkeypatch.setattr(
        "ai.chat.agent.get_chat_session",
        lambda sid: get_chat_session(sid, db_path=db),
    )
    monkeypatch.setattr(
        "ai.chat.agent.upsert_chat_message",
        lambda **kw: upsert_chat_message(**kw, db_path=db),
    )

    session = create_chat_session(mode="saved", db_path=db)
    assistant_message = {
        "id": "asst_1",
        "role": "assistant",
        "parts": [
            {
                "type": "tool-propose_order",
                "toolCallId": "call_1",
                "toolName": "propose_order",
                "state": "output-available",
                "input": {
                    "symbol": "NVDA",
                    "side": "buy",
                    "qty": 100,
                    "order_type": "market",
                },
                "output": {
                    "status": "dry_run",
                    "proposal_id": "paper_existing123",
                    "execution_id": "exec_existing123",
                    "execution_mode": "dry_run",
                    "message": "DRY RUN — would place: BUY 100 NVDA market",
                },
            },
        ],
    }
    out = finalize_turn(
        messages=[
            {
                "id": "user_1",
                "role": "user",
                "parts": [{"type": "text", "text": "buy 100 nvda"}],
            },
            assistant_message,
        ],
        assistant_text="",
        session_id=session["id"],
        mode="saved",
        assistant_message=assistant_message,
    )
    assert out["ok"] is True
    detail = get_chat_session_with_messages(session["id"], db_path=db)
    assert detail is not None
    assert len(detail["messages"]) >= 1
    asst = detail["messages"][-1]
    assert asst["role"] == "assistant"
    asst_parts = asst["parts"]
    assert asst_parts[0]["type"] == "tool-propose_order"
    assert asst_parts[0]["output"]["status"] == "dry_run"
    assert paper_orders_mod.list_paper_order_proposals(db_path=db) == []


def test_persist_turn_saved_mode_actually_calls_extraction(tmp_path, monkeypatch):
    """Regression: live routes call ai.chat.agent.finalize_chat_turn directly with
    the POST-normalization mode ("saved"), never the literal "standard" — a stale
    `mode == "standard"` gate inside _persist_turn silently no-op'd extraction for
    every real chat turn (mocked-finalize tests above never caught it)."""
    from ai.chat.agent import finalize_chat_turn
    from db.chat_sessions import create_chat_session, get_chat_session, upsert_chat_message

    db = tmp_path / "chat.db"
    monkeypatch.setattr(
        "ai.chat.agent.get_chat_session",
        lambda sid: get_chat_session(sid, db_path=db),
    )
    monkeypatch.setattr(
        "ai.chat.agent.upsert_chat_message",
        lambda **kw: upsert_chat_message(**kw, db_path=db),
    )
    calls: list[dict] = []
    monkeypatch.setattr(
        "ai.chat.agent.extract_memories_from_turn",
        lambda **kw: (calls.append(kw), [])[1],
    )

    session = create_chat_session(mode="saved", db_path=db)
    from ai.chat.agent import ChatRunResult

    finalize_chat_turn(
        messages=[{"id": "user_1", "role": "user", "parts": [{"type": "text", "text": "I never hold more than 20% in one name"}]}],
        user_text="I never hold more than 20% in one name",
        assistant_text="Noted.",
        session_id=session["id"],
        mode="saved",
        run=ChatRunResult(),
        extract=True,
    )
    assert len(calls) == 1


def test_finalize_turn_saved_extracts_memories(monkeypatch):
    captured: dict = {}

    def fake_finalize(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr("ai.chat.brain.finalize_chat_turn", fake_finalize)
    finalize_turn(
        messages=[
            {
                "id": "user_1",
                "role": "user",
                "parts": [{"type": "text", "text": "I never hold more than 20% in one name"}],
            },
        ],
        assistant_text="Noted — concentration cap at 20%.",
        session_id="chat_test123",
        mode="saved",
    )
    assert captured.get("extract") is True


def test_finalize_turn_private_skips_extract(monkeypatch):
    captured: dict = {}

    def fake_finalize(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr("ai.chat.brain.finalize_chat_turn", fake_finalize)
    finalize_turn(
        messages=[
            {
                "id": "user_1",
                "role": "user",
                "parts": [{"type": "text", "text": "secret preference"}],
            },
        ],
        assistant_text="Understood.",
        session_id="chat_private123",
        mode="private",
    )
    assert captured.get("extract") is False


def test_finalize_turn_temporary_skips_extract(monkeypatch):
    captured: dict = {}

    def fake_finalize(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr("ai.chat.brain.finalize_chat_turn", fake_finalize)
    out = finalize_turn(
        messages=[{"role": "user", "parts": [{"type": "text", "text": "hi"}]}],
        assistant_text="hello",
        session_id=None,
        mode="temporary",
    )
    assert out["ok"] is True
    assert captured == {}


def test_finalize_turn_ephemeral_skips_persist(monkeypatch):
    called = {"persist": False}

    def fake_finalize(**_kwargs):
        called["persist"] = True

    monkeypatch.setattr("ai.chat.brain.finalize_chat_turn", fake_finalize)
    out = finalize_turn(
        messages=[{"role": "user", "parts": [{"type": "text", "text": "hi"}]}],
        assistant_text="hello",
        session_id=None,
        mode="temporary",
    )
    assert out["ok"] is True
    assert called["persist"] is False
