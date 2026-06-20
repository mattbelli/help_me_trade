"""Honest performance context across chat tools and shared sanitizer."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import db.ai_tables as ai_tables
from ai.chat.memory_extract import extract_memories_from_turn
from ai.chat.review_intent import looks_like_review_intent
from ai.chat.tools import execute_tool
from ai.perf_context import sanitize_perf_context
from db.trades import create_trade_account, import_trade_events
from imports.base import ParsedTradeEvent
from imports.money import parse_dollar


def _parsed(
    trade_date: str,
    symbol: str,
    side: str,
    quantity: Decimal,
    *,
    amount: Decimal,
    key: str,
) -> ParsedTradeEvent:
    from datetime import date

    return ParsedTradeEvent(
        event_type="trade",
        trade_date=date.fromisoformat(trade_date),
        symbol=symbol,
        side=side,
        quantity=quantity,
        price=None,
        fees=Decimal("0"),
        amount=amount,
        description=f"{side} {symbol}",
        external_key=key,
        raw={},
    )


def _patch_analytics_db(monkeypatch, db_path: Path) -> None:
    from db import trades

    def _with_db(fn):
        def wrapped(**kwargs):
            kwargs.setdefault("db_path", db_path)
            return fn(**kwargs)

        return wrapped

    monkeypatch.setattr(
        "db.analytics.list_trade_events",
        _with_db(trades.list_trade_events),
    )
    monkeypatch.setattr(
        "db.analytics.trade_summary_stats",
        _with_db(trades.trade_summary_stats),
    )
    monkeypatch.setattr(
        "db.analytics.daily_trade_stats",
        _with_db(trades.daily_trade_stats),
    )
    monkeypatch.setattr(
        "db.trades.symbol_trade_stats",
        _with_db(trades.symbol_trade_stats),
    )


def _fifo_trade_lists_have_no_pnl(payload: dict) -> bool:
    fifo = payload.get("approximate_fifo") or {}
    for key in ("largest_wins", "largest_losses", "recent_closed_trades"):
        trades = fifo.get(key) or []
        for trade in trades:
            if isinstance(trade, dict) and "pnl" in trade:
                return False
    return True


def test_get_performance_insights_leads_with_reliable_headline(
    tmp_path: Path,
    monkeypatch,
) -> None:
    db_path = tmp_path / "trades.db"
    _patch_analytics_db(monkeypatch, db_path)

    events = [
        _parsed("2026-01-01", "AAPL", "buy", Decimal("10"), amount=Decimal("-1000"), key="a1"),
        _parsed("2026-01-05", "AAPL", "sell", Decimal("10"), amount=Decimal("1200"), key="a2"),
        _parsed("2026-01-06", "PLTR", "sell", Decimal("20"), amount=Decimal("800"), key="p1"),
    ]
    account = create_trade_account("Test", "schwab", db_path=db_path)
    import_trade_events(account["id"], events, source="test", db_path=db_path)

    monkeypatch.setattr(
        "signals.trade_context.build_entry_style_block",
        lambda *_args, **_kwargs: {"profile": "mixed"},
    )
    monkeypatch.setattr(
        "brokers.factory.get_broker",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "config.settings.broker_is_configured",
        lambda *_args, **_kwargs: False,
    )

    payload = execute_tool(
        "get_performance_insights",
        {"period": "all", "account_id": account["id"]},
    )

    assert "headline" in payload
    assert payload["headline"]["realized_closed"] == payload["realized"]["realized_closed"]
    assert payload["headline"]["closed_win_rate"] == payload["realized"]["closed_win_rate"]
    assert parse_dollar(payload["headline"]["realized_closed"]) == Decimal("200")
    assert payload["entry_style"] == {"profile": "mixed"}
    assert payload["approximate_fifo"]["unreliable_fifo"] is True
    assert _fifo_trade_lists_have_no_pnl(payload)

    incomplete = payload["realized"]["incomplete_positions"]
    assert any(item["symbol"] == "PLTR" for item in incomplete)
    assert all("proceeds only" in item["label"] for item in incomplete)
    assert "proceeds only" in payload["realized"]["incomplete_positions_note"]


def test_sanitize_perf_context_strips_fifo_pnl() -> None:
    ctx = {
        "approximate_fifo": {
            "largest_wins": [
                {
                    "symbol": "NVDA",
                    "open_date": "2026-01-01",
                    "close_date": "2026-01-02",
                    "pnl": "56466.00",
                    "quantity": "100",
                }
            ],
            "recent_closed_trades": [
                {
                    "symbol": "PLTR",
                    "open_date": "2026-01-01",
                    "close_date": "2026-01-02",
                    "pnl": "77094.00",
                }
            ],
        }
    }
    sanitized = sanitize_perf_context(ctx)
    assert "pnl" not in sanitized["approximate_fifo"]["largest_wins"][0]
    assert "pnl" not in sanitized["approximate_fifo"]["recent_closed_trades"][0]
    assert sanitized["approximate_fifo"]["largest_wins"][0]["symbol"] == "NVDA"


def test_looks_like_review_intent() -> None:
    assert looks_like_review_intent("run a trade review") is True
    assert looks_like_review_intent("how am I doing this month?") is True
    assert looks_like_review_intent("analyze my performance") is True
    assert looks_like_review_intent("buy 10 AAPL") is False
    assert looks_like_review_intent("what's my balance") is False


def test_review_turn_does_not_create_memory(monkeypatch, tmp_path) -> None:
    db = tmp_path / "ai.db"
    monkeypatch.setattr(ai_tables, "DEFAULT_DB_PATH", db)

    saved = extract_memories_from_turn(
        user_text="run a trade review for the last month",
        assistant_text=(
            "Your goal was 10 trades/week and net realized was $40,659 with 82.5% win rate."
        ),
        session_id="sess_review",
    )
    assert saved == []


def test_get_trade_history_stats_separate_fifo(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "trades.db"
    _patch_analytics_db(monkeypatch, db_path)

    events = [
        _parsed("2026-01-01", "AAPL", "buy", Decimal("10"), amount=Decimal("-1000"), key="a1"),
        _parsed("2026-01-05", "AAPL", "sell", Decimal("10"), amount=Decimal("1200"), key="a2"),
    ]
    account = create_trade_account("Test", "schwab", db_path=db_path)
    import_trade_events(account["id"], events, source="test", db_path=db_path)

    payload = execute_tool("get_trade_history", {"limit": 10, "account_id": account["id"]})

    stats = payload["stats"]
    assert "win_rate" not in stats
    assert "wins" not in stats
    assert stats["approximate_fifo"]["unreliable_fifo"] is True
    assert "net_amount" in stats


def test_chat_system_prompt_forbids_fabrication() -> None:
    from ai.chat.agent import chat_system_prompt

    prompt = chat_system_prompt()
    assert "realized.realized_closed" in prompt
    assert "approximate_fifo" in prompt
    assert "incomplete_positions" in prompt
    assert "Do NOT invent goals" in prompt
