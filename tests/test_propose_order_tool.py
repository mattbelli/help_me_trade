"""propose_order tool end-to-end through ExecutionService."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from ai.chat.tools import execute_tool
from brokers.alpaca import AlpacaAdapter
from brokers.base import Account, Position
from db import order_executions as order_executions_mod
from db import paper_orders as paper_orders_mod
from db import sqlite as sqlite_mod


def _stub_alpaca(*, tradable: bool = True) -> AlpacaAdapter:
    stub = object.__new__(AlpacaAdapter)
    stub.symbol_exists = lambda _symbol: tradable  # type: ignore[method-assign]
    return stub


def _patch_passing_env(monkeypatch, db_path: Path) -> None:
    from db import base as db_base

    monkeypatch.setattr(db_base, "DEFAULT_DB_PATH", db_path)
    monkeypatch.setattr(sqlite_mod, "DEFAULT_DB_PATH", db_path)
    monkeypatch.setattr(order_executions_mod, "DEFAULT_DB_PATH", db_path)
    monkeypatch.setattr(paper_orders_mod, "DEFAULT_DB_PATH", db_path)

    account = Account(
        id="acc1",
        status="ACTIVE",
        cash=50_000.0,
        buying_power=100_000.0,
        portfolio_value=100_000.0,
    )
    positions = [
        Position(
            symbol="AAPL",
            qty=10.0,
            side="long",
            market_value=1_500.0,
            unrealized_pl=50.0,
            avg_entry_price=145.0,
            current_price=150.0,
        )
    ]
    sqlite_mod.save_snapshot(account, positions, [], broker="alpaca", db_path=db_path)

    monkeypatch.setattr(
        "trading.risk_engine.get_broker",
        lambda _name: _stub_alpaca(),
    )
    monkeypatch.setattr(
        "trading.risk_engine.market_hub.quote_snapshot",
        lambda _symbols: {"AAPL": {"price": 150.0}},
    )
    monkeypatch.setattr("trading.risk_engine.is_kill_switch_active", lambda: False)
    monkeypatch.setattr(
        "trading.risk_engine.trading_config",
        lambda: {
            "execution_mode": "dry_run",
            "kill_switch_active": False,
            "max_order_value_usd": 50_000,
            "max_position_pct": 30,
            "max_open_orders": 10,
            "max_daily_orders": 20,
        },
    )
    monkeypatch.setattr("trading.execution_service.is_kill_switch_active", lambda: False)
    monkeypatch.setattr(
        "trading.execution_service.trading_config",
        lambda: {
            "execution_mode": "dry_run",
            "kill_switch_active": False,
            "max_order_value_usd": 50_000,
            "max_position_pct": 30,
            "max_open_orders": 10,
            "max_daily_orders": 20,
        },
    )
    monkeypatch.setattr(
        "trading.execution_service.market_hub.broadcast_alert",
        AsyncMock(),
    )


@pytest.mark.asyncio
async def test_propose_order_dry_run_creates_rows(monkeypatch, tmp_path: Path) -> None:
    db = tmp_path / "propose.db"
    _patch_passing_env(monkeypatch, db)

    result = await execute_tool(
        "propose_order",
        {"symbol": "AAPL", "side": "buy", "qty": 2, "order_type": "market"},
    )

    assert result["status"] == "dry_run"
    assert result["proposal_id"]
    assert result["execution_id"]
    assert result["execution_mode"] == "dry_run"
    assert "DRY RUN" in result["message"]

    proposals = paper_orders_mod.list_paper_order_proposals(db_path=db)
    assert len(proposals) == 1
    assert proposals[0]["id"] == result["proposal_id"]

    row = order_executions_mod.get_execution(result["execution_id"], db_path=db)
    assert row is not None
    assert row["proposal_id"] == result["proposal_id"]
    assert row["status"] == "dry_run"


@pytest.mark.asyncio
async def test_propose_order_over_value_cap_rejected(
    monkeypatch, tmp_path: Path
) -> None:
    db = tmp_path / "propose.db"
    _patch_passing_env(monkeypatch, db)
    monkeypatch.setattr(
        "trading.risk_engine.market_hub.quote_snapshot",
        lambda _symbols: {"AAPL": {"price": 6000.0}},
    )

    result = await execute_tool(
        "propose_order",
        {"symbol": "AAPL", "side": "buy", "qty": 10, "order_type": "market"},
    )

    assert result["status"] == "rejected"
    assert "max value" in result["error"]
    assert result["proposal_id"]

    proposals = paper_orders_mod.list_paper_order_proposals(db_path=db)
    assert len(proposals) == 1

    import sqlite3

    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT status FROM order_executions WHERE proposal_id = ?",
        (result["proposal_id"],),
    ).fetchall()
    conn.close()
    assert len(rows) == 1
    assert rows[0]["status"] == "rejected"


@pytest.mark.asyncio
async def test_propose_order_option_sell_builds_intent(
    monkeypatch, tmp_path: Path
) -> None:
    db = tmp_path / "propose.db"
    _patch_passing_env(monkeypatch, db)

    from datetime import date, timedelta
    from decimal import Decimal

    from imports.option_symbol import OptionSymbol, to_occ_symbol

    occ = to_occ_symbol(
        OptionSymbol(
            underlying="TSLA",
            expiration=date.today() + timedelta(days=30),
            strike=Decimal("410"),
            right="C",
        )
    )
    account = Account(
        id="acc1",
        status="ACTIVE",
        cash=50_000.0,
        buying_power=100_000.0,
        portfolio_value=100_000.0,
    )
    sqlite_mod.save_snapshot(
        account,
        [
            Position(
                symbol=occ,
                qty=2.0,
                side="long",
                market_value=700.0,
                unrealized_pl=0.0,
                avg_entry_price=3.50,
                current_price=3.50,
            )
        ],
        [],
        broker="alpaca",
        db_path=db,
    )

    stub = _stub_alpaca()
    stub.option_contract_exists = lambda _occ: True  # type: ignore[method-assign]
    monkeypatch.setattr("trading.risk_engine.get_broker", lambda _n: stub)

    result = await execute_tool(
        "propose_order",
        {
            "option_underlying": "TSLA",
            "option_expiration": (date.today() + timedelta(days=30)).isoformat(),
            "option_strike": 410,
            "option_right": "C",
            "side": "sell",
            "qty": 1,
            "limit_price": 3.50,
        },
    )

    assert "error" not in result
    assert result["proposal"]["asset_class"] == "us_option"
    assert result["proposal"]["side"] == "sell"
    assert result["proposal"]["held_qty"] == 2.0


@pytest.mark.asyncio
async def test_propose_order_equity_sell_includes_held_qty(
    monkeypatch, tmp_path: Path
) -> None:
    db = tmp_path / "propose.db"
    _patch_passing_env(monkeypatch, db)

    result = await execute_tool(
        "propose_order",
        {"symbol": "AAPL", "side": "sell", "qty": 2, "order_type": "market"},
    )

    assert result["proposal"]["held_qty"] == 10.0
    assert result["status"] == "dry_run"
