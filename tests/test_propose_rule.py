"""Tests for create_rule chat tool and alert intent detection."""

from __future__ import annotations

import pytest

import db.ai_tables as ai_tables
from ai.chat.alert_intent import looks_like_alert_intent
from ai.chat.memory_extract import extract_memories_from_turn
from ai.chat.tools import execute_tool


@pytest.mark.asyncio
async def test_create_rule_valid_spec(monkeypatch, tmp_path) -> None:
    db = tmp_path / "rules.db"
    monkeypatch.setattr(ai_tables, "DEFAULT_DB_PATH", db)

    spec = {
        "name": "AAPL RSI low",
        "condition": {
            "metric": "rsi_14",
            "symbol": "AAPL",
            "operator": "lessThan",
            "value": 30,
        },
        "action": {"type": "alert", "message": "AAPL RSI dropped below 30"},
    }
    result = await execute_tool("create_rule", {"spec": spec})

    assert result["status"] == "created"
    assert "rule_id" in result
    rules = ai_tables.list_rules(db_path=db)
    assert len(rules) == 1
    assert rules[0]["conditions"] == {
        "fact": "AAPL_rsi_14",
        "operator": "lessThan",
        "value": 30,
    }
    assert rules[0]["enabled"] is True


@pytest.mark.asyncio
async def test_create_rule_invalid_spec_no_rule_created(monkeypatch, tmp_path) -> None:
    db = tmp_path / "rules.db"
    monkeypatch.setattr(ai_tables, "DEFAULT_DB_PATH", db)

    spec = {
        "name": "Bad rule",
        "condition": {
            "metric": "bogus_metric",
            "symbol": "AAPL",
            "operator": "lessThan",
            "value": 30,
        },
    }
    result = await execute_tool("create_rule", {"spec": spec})

    assert "error" in result
    assert ai_tables.list_rules(db_path=db) == []


def test_looks_like_alert_intent() -> None:
    assert looks_like_alert_intent("alert me when NVDA crosses its 200 day") is True
    assert looks_like_alert_intent("what's my balance") is False


def test_alert_turn_does_not_create_memory(monkeypatch, tmp_path) -> None:
    # A rule is not a memory: creating an alert must NOT also store a "preference".
    db = tmp_path / "ai.db"
    monkeypatch.setattr(ai_tables, "DEFAULT_DB_PATH", db)

    saved = extract_memories_from_turn(
        user_text="alert me when NVDA crosses below its 200-day moving average",
        assistant_text="Armed: NVDA below 200-day SMA.",
        session_id="sess_test",
    )
    assert saved == []
