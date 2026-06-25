"""Copilot chat tools — multi-account + agent-aware (Batches A + B)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from ai.chat.tools import execute_tool
from brokers.alpaca import PortfolioHistoryPoint
from brokers.base import Account, Position
from db import base as db_base
from db import paper_orders as paper_orders_mod
from db import sqlite as sqlite_mod
from db.accounts import get_default_account, list_accounts
from db.earnings_calendar import upsert_earnings
from db.managed_positions import init_managed_position_tables, record_open_position
from db.multi_account import migrate_multi_account
from db.paper_orders import create_pending_proposal, list_paper_order_proposals
from db.strategies import init_strategy_tables, seed_default_strategies, update_strategy


def _account(
    *,
    cash: float = 10_000.0,
    account_id: str = "broker-acct-1",
) -> Account:
    return Account(
        id=account_id,
        status="ACTIVE",
        cash=cash,
        buying_power=cash,
        portfolio_value=cash * 2,
    )


def _position(symbol: str = "AAPL") -> Position:
    return Position(
        symbol=symbol,
        qty=10.0,
        side="long",
        market_value=1_500.0,
        unrealized_pl=50.0,
        avg_entry_price=145.0,
        current_price=150.0,
    )


def _migrated_db(tmp_path: Path, *, schwab_count: int = 1) -> Path:
    from db.order_executions import init_execution_tables

    db = tmp_path / "chat_tools.db"
    sqlite_mod.init_db(db)
    from db.trades import init_trade_tables

    init_trade_tables(db)
    init_execution_tables(db)
    paper_orders_mod.init_paper_order_tables(db)
    init_strategy_tables(db)
    init_managed_position_tables(db)

    if schwab_count:
        now = datetime.now(UTC).isoformat()
        with db_base.connect(db) as conn:
            for i in range(schwab_count):
                account_id = f"acct_schwab_{i}"
                conn.execute(
                    """
                    INSERT INTO trade_accounts (id, label, broker, created_at)
                    VALUES (?, ?, 'schwab', ?)
                    """,
                    (account_id, f"Schwab {i + 1}", now),
                )
            conn.commit()

    migrate_multi_account(db)
    seed_default_strategies(db_path=db)
    return db


def _patch_db(monkeypatch: pytest.MonkeyPatch, db: Path) -> None:
    monkeypatch.setattr(db_base, "DEFAULT_DB_PATH", db)
    monkeypatch.setattr(sqlite_mod, "DEFAULT_DB_PATH", db)
    monkeypatch.setattr(paper_orders_mod, "DEFAULT_DB_PATH", db)


def _history_point(
    *,
    profit_loss: float = 150.0,
    profit_loss_pct: float = 1.5,
) -> PortfolioHistoryPoint:
    return PortfolioHistoryPoint(
        timestamp=1_700_000_000,
        equity=10_000.0,
        profit_loss=profit_loss,
        profit_loss_pct=profit_loss_pct,
    )


def _patch_day_pl_broker(
    monkeypatch: pytest.MonkeyPatch,
    *,
    day_pl: float = 150.0,
    day_pl_pct: float = 1.5,
    fail_account_ids: frozenset[str] = frozenset(),
) -> None:
    async def fake_history(_range: str) -> list[PortfolioHistoryPoint]:
        return [_history_point(profit_loss=day_pl, profit_loss_pct=day_pl_pct)]

    def fake_get_broker_for_account(account_id: str) -> MagicMock:
        broker = MagicMock()
        if account_id in fail_account_ids:
            broker.aget_portfolio_history = AsyncMock(
                side_effect=RuntimeError("history unavailable")
            )
        else:
            broker.aget_portfolio_history = AsyncMock(side_effect=fake_history)
        return broker

    monkeypatch.setattr(
        "brokers.factory.get_broker_for_account",
        fake_get_broker_for_account,
    )


@pytest.mark.asyncio
async def test_get_portfolio_snapshot_all_accounts(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    db = _migrated_db(tmp_path, schwab_count=1)
    _patch_db(monkeypatch, db)

    alpaca = get_default_account("alpaca", db_path=db)
    assert alpaca is not None
    schwab = list_accounts(broker="schwab", db_path=db)[0]

    sqlite_mod.save_snapshot(
        _account(cash=5_000.0, account_id=alpaca["id"]),
        [_position()],
        [],
        broker="alpaca",
        account_id=alpaca["id"],
        db_path=db,
    )
    sqlite_mod.save_snapshot(
        _account(cash=50_000.0, account_id=schwab["id"]),
        [],
        [],
        broker="schwab",
        account_id=schwab["id"],
        db_path=db,
    )

    _patch_day_pl_broker(monkeypatch, day_pl=150.0, day_pl_pct=1.5)

    result = await execute_tool("get_portfolio_snapshot", {})

    assert "accounts" in result
    assert "aggregate" in result
    assert result["aggregate"]["account_count"] >= 2

    by_id = {a["account_id"]: a for a in result["accounts"]}
    assert by_id[alpaca["id"]]["tradeable"] is True
    assert by_id[alpaca["id"]]["read_only"] is False
    assert by_id[alpaca["id"]]["day_pl"] == 150.0
    assert by_id[alpaca["id"]]["day_pl_pct"] == 1.5
    assert by_id[schwab["id"]]["read_only"] is True
    assert by_id[schwab["id"]]["tradeable"] is False
    assert by_id[schwab["id"]]["day_pl"] is None
    assert by_id[schwab["id"]]["day_pl_pct"] is None
    assert result["aggregate"]["total_day_pl"] == 150.0
    assert "note" in result
    assert "CUMULATIVE" in result["note"]


@pytest.mark.asyncio
async def test_get_portfolio_snapshot_day_pl_fail_safe(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    db = _migrated_db(tmp_path, schwab_count=0)
    _patch_db(monkeypatch, db)

    alpaca = get_default_account("alpaca", db_path=db)
    assert alpaca is not None

    sqlite_mod.save_snapshot(
        _account(cash=5_000.0, account_id=alpaca["id"]),
        [_position()],
        [],
        broker="alpaca",
        account_id=alpaca["id"],
        db_path=db,
    )

    _patch_day_pl_broker(
        monkeypatch,
        fail_account_ids=frozenset({alpaca["id"]}),
    )

    result = await execute_tool("get_portfolio_snapshot", {})

    assert "accounts" in result
    by_id = {a["account_id"]: a for a in result["accounts"]}
    assert by_id[alpaca["id"]]["day_pl"] is None
    assert by_id[alpaca["id"]]["day_pl_pct"] is None
    assert result["aggregate"]["total_day_pl"] == 0.0


@pytest.mark.asyncio
async def test_propose_order_refuses_read_only_account(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    db = _migrated_db(tmp_path, schwab_count=1)
    _patch_db(monkeypatch, db)

    schwab = list_accounts(broker="schwab", db_path=db)[0]
    before = len(list_paper_order_proposals(db_path=db))

    result = await execute_tool(
        "propose_order",
        {
            "symbol": "AAPL",
            "side": "buy",
            "qty": 1,
            "order_type": "market",
            "account_id": schwab["id"],
        },
    )

    assert "error" in result
    assert "read-only" in result["error"].lower()
    assert len(list_paper_order_proposals(db_path=db)) == before


@pytest.mark.asyncio
async def test_get_trade_history_passes_account_id(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    db = _migrated_db(tmp_path, schwab_count=0)
    _patch_db(monkeypatch, db)

    captured: dict[str, object] = {}

    def fake_list_trade_events(**kwargs: object) -> list[dict[str, object]]:
        captured["events"] = kwargs
        return []

    def fake_trade_summary_stats(**kwargs: object) -> dict[str, object]:
        captured["stats"] = kwargs
        return {"trade_count": 0}

    monkeypatch.setattr("ai.chat.tools.list_trade_events", fake_list_trade_events)
    monkeypatch.setattr("ai.chat.tools.trade_summary_stats", fake_trade_summary_stats)

    await execute_tool(
        "get_trade_history",
        {"symbol": "AAPL", "account_id": "acct_test_123", "limit": 10},
    )

    assert captured["events"]["account_id"] == "acct_test_123"
    assert captured["stats"]["account_id"] == "acct_test_123"


@pytest.mark.asyncio
async def test_get_agent_status_sections(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    db = _migrated_db(tmp_path, schwab_count=0)
    _patch_db(monkeypatch, db)

    alpaca = get_default_account("alpaca", db_path=db)
    assert alpaca is not None
    account_id = alpaca["id"]
    account_label = alpaca["label"]

    update_strategy(
        "dip_buy",
        enabled=True,
        account_id=account_id,
        db_path=db,
    )
    record_open_position(
        account_id=account_id,
        symbol="NVDA",
        strategy_id="dip_buy",
        qty="5",
        exit_mode="auto",
        params={"stop_pct": -3.0},
        db_path=db,
    )
    create_pending_proposal(
        proposal={"symbol": "MSFT", "side": "buy", "qty": 2, "order_type": "market"},
        intent_fields={
            "symbol": "MSFT",
            "side": "buy",
            "qty": 2,
            "order_type": "market",
            "broker": "alpaca",
            "asset_class": "us_equity",
            "multiplier": "1",
            "account_id": account_id,
        },
        session_id=None,
        ttl_seconds=3600,
        summary="Buy MSFT dip",
        account_id=account_id,
        db_path=db,
    )

    result = await execute_tool("get_agent_status", {})

    assert any(s["id"] == "dip_buy" and s["account_label"] == account_label for s in result["strategies"])
    assert len(result["managed_positions"]) == 1
    assert result["managed_positions"][0]["account_label"] == account_label
    assert result["managed_positions"][0]["symbol"] == "NVDA"
    assert len(result["pending_approvals"]) == 1
    assert result["pending_approvals"][0]["account_label"] == account_label
    assert result["pending_approvals"][0]["summary"] == "Buy MSFT dip"


@pytest.mark.asyncio
async def test_get_earnings_dates(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    db = tmp_path / "earnings.db"
    _patch_db(monkeypatch, db)
    upsert_earnings(
        [
            {"symbol": "NVDA", "date": "2026-01-28"},
            {"symbol": "NVDA", "date": "2026-05-28"},
            {"symbol": "NVDA", "date": "2026-08-27"},
        ],
        db_path=db,
    )
    monkeypatch.setattr("ai.chat.tools.now_iso", lambda: "2026-03-15T12:00:00+00:00")

    result = await execute_tool("get_earnings_dates", {"symbol": "nvda"})

    assert result["symbol"] == "NVDA"
    assert result["next_earnings"] == "2026-05-28"
    assert result["recent"] == ["2026-01-28", "2026-05-28", "2026-08-27"]
    assert "note" in result


@pytest.mark.asyncio
async def test_get_earnings_dates_requires_symbol(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _patch_db(monkeypatch, tmp_path / "empty.db")
    result = await execute_tool("get_earnings_dates", {})
    assert result == {"error": "symbol is required"}


@pytest.mark.asyncio
async def test_get_context_requires_symbol() -> None:
    result = await execute_tool("get_context", {})
    assert result == {"error": "symbol is required"}


@pytest.mark.asyncio
async def test_get_context_delegates_to_builder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected = {"symbol": "NVDA", "quote": {"price": 900.0}}

    async def fake_build(symbol: str, *, db_path: Path) -> dict:
        assert symbol == "NVDA"
        return expected

    monkeypatch.setattr(
        "ai.perception.context.build_symbol_context",
        fake_build,
    )
    result = await execute_tool("get_context", {"symbol": "nvda"})
    assert result == expected


@pytest.mark.asyncio
async def test_list_capabilities_tool() -> None:
    result = await execute_tool("list_capabilities", {})
    assert "context_scopes" in result
    assert "symbol" in result["context_scopes"]
