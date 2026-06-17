"""Tests for chat brain helpers (Next.js route host)."""

from __future__ import annotations

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


def test_tool_catalog_includes_propose_order():
    names = {item["name"] for item in tool_catalog()}
    assert "propose_order" in names
    assert "get_portfolio_snapshot" in names
    assert "web_search" in names


def test_run_tool_web_search_requires_query():
    result = run_tool("web_search", {})
    assert "error" in result


def test_build_web_query_scopes_sites() -> None:
    from ai.research.sources import _build_web_query

    q = _build_web_query("stocks trending", ["reddit.com", "x.com"])
    assert "site:reddit.com" in q
    assert "site:x.com" in q
    assert "stocks trending" in q


def test_run_tool_web_search_unconfigured_returns_error(monkeypatch):
    monkeypatch.setattr(
        "ai.research.sources.get_searxng_url",
        lambda: "",
    )
    result = run_tool("web_search", {"query": "NVDA reddit"})
    assert result.get("error") == "searxng_not_configured"
    assert result.get("results") == []
    assert "limitations" in result


def test_run_tool_propose_order_requires_symbol():
    result = run_tool("propose_order", {"side": "buy", "qty": 1})
    assert "error" in result


def test_run_tool_propose_order_ok():
    result = run_tool(
        "propose_order",
        {"symbol": "AAPL", "side": "buy", "qty": 2, "order_type": "market"},
    )
    assert result["status"] == "recorded"
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
    from db.chat_sessions import (
        create_chat_session,
        get_chat_session,
        get_chat_session_with_messages,
        upsert_chat_message,
    )
    from db.paper_orders import record_paper_order_proposal

    db = tmp_path / "chat.db"
    monkeypatch.setattr(
        "ai.chat.agent.get_chat_session",
        lambda sid: get_chat_session(sid, db_path=db),
    )
    monkeypatch.setattr(
        "ai.chat.agent.upsert_chat_message",
        lambda **kw: upsert_chat_message(**kw, db_path=db),
    )
    monkeypatch.setattr(
        "ai.chat.agent.record_paper_order_proposal",
        lambda **kw: record_paper_order_proposal(**kw, db_path=db),
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
                    "status": "recorded",
                    "proposal": {
                        "symbol": "NVDA",
                        "side": "buy",
                        "qty": 100,
                        "order_type": "market",
                    },
                    "message": "Recorded paper intent",
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
    assert asst_parts[0]["output"]["status"] == "recorded"


def test_finalize_turn_skips_memory_extract(monkeypatch):
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
    assert captured.get("extract") is False


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
