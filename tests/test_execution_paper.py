"""ExecutionService paper mode — broker submit, rejection, live guard."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from brokers.alpaca import AlpacaError
from brokers.base import Account, PlaceOrderResponse, Position
from db import order_executions as order_executions_mod
from db import sqlite as sqlite_mod
from imports.option_symbol import OptionSymbol, to_occ_symbol
from trading.execution_service import ExecutionService
from trading.models import OrderIntent, RiskRejectedError


def _intent(**overrides) -> OrderIntent:
    base = dict(
        proposal_id="paper_test123",
        session_id=None,
        symbol="AAPL",
        side="buy",
        qty=Decimal("10"),
        order_type="market",
        limit_price=None,
        broker="alpaca",
    )
    base.update(overrides)
    return OrderIntent(**base)


def _option_intent(**overrides) -> OrderIntent:
    occ = to_occ_symbol(
        OptionSymbol(
            underlying="TSLA",
            expiration=date.today() + timedelta(days=30),
            strike=Decimal("410"),
            right="C",
        )
    )
    base = dict(
        proposal_id="paper_opt123",
        session_id=None,
        symbol="TSLA",
        side="buy",
        qty=Decimal("1"),
        order_type="limit",
        limit_price=Decimal("3.50"),
        broker="alpaca",
        asset_class="us_option",
        occ_symbol=occ,
        multiplier=100,
    )
    base.update(overrides)
    return OrderIntent(**base)


def _save_fresh_snapshot(db_path: Path) -> None:
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


def _patch_passing_env(monkeypatch, db_path: Path, *, execution_mode: str) -> None:
    from brokers.alpaca import AlpacaAdapter
    from db import base as db_base

    monkeypatch.setattr(db_base, "DEFAULT_DB_PATH", db_path)
    monkeypatch.setattr(sqlite_mod, "DEFAULT_DB_PATH", db_path)
    monkeypatch.setattr(order_executions_mod, "DEFAULT_DB_PATH", db_path)
    _save_fresh_snapshot(db_path)

    stub = object.__new__(AlpacaAdapter)
    stub.symbol_exists = lambda _symbol: True  # type: ignore[method-assign]
    stub.option_contract_exists = lambda _occ: True  # type: ignore[method-assign]
    stub.aplace_order = AsyncMock(  # type: ignore[method-assign]
        return_value=PlaceOrderResponse(
            broker_order_id="broker-ord-99",
            status="accepted",
            raw={"id": "broker-ord-99", "status": "accepted"},
        )
    )

    monkeypatch.setattr(
        "trading.risk_engine.get_broker",
        lambda _name: stub,
    )
    monkeypatch.setattr(
        "trading.execution_service.get_broker",
        lambda _name: stub,
    )
    monkeypatch.setattr(
        "trading.risk_engine.market_hub.quote_snapshot",
        lambda _symbols: {"AAPL": {"price": 150.0}},
    )
    trading_cfg = {
        "execution_mode": execution_mode,
        "kill_switch_active": False,
        "max_order_value_usd": 50_000,
        "max_position_pct": 30,
        "max_open_orders": 10,
        "max_daily_orders": 20,
    }
    monkeypatch.setattr("trading.risk_engine.is_kill_switch_active", lambda: False)
    monkeypatch.setattr("trading.risk_engine.trading_config", lambda: trading_cfg)
    monkeypatch.setattr("trading.execution_service.is_kill_switch_active", lambda: False)
    monkeypatch.setattr("trading.execution_service.trading_config", lambda: trading_cfg)
    monkeypatch.setattr(
        "trading.execution_service.market_hub.broadcast_alert",
        AsyncMock(),
    )


@pytest.mark.asyncio
async def test_execution_paper_submits_to_broker(monkeypatch, tmp_path: Path) -> None:
    db = tmp_path / "exec.db"
    _patch_passing_env(monkeypatch, db, execution_mode="paper")

    result = await ExecutionService.submit(_intent(), db_path=db)

    assert result.status == "submitted"
    assert result.broker_order_id == "broker-ord-99"
    row = order_executions_mod.get_execution(result.execution_id, db_path=db)
    assert row is not None
    assert row["status"] == "submitted"
    assert row["broker_order_id"] == "broker-ord-99"
    assert row["submitted_at"] is not None


@pytest.mark.asyncio
async def test_execution_paper_broker_error_rejected(
    monkeypatch, tmp_path: Path
) -> None:
    db = tmp_path / "exec.db"
    _patch_passing_env(monkeypatch, db, execution_mode="paper")

    from brokers.alpaca import AlpacaAdapter

    stub = object.__new__(AlpacaAdapter)
    stub.aplace_order = AsyncMock(  # type: ignore[method-assign]
        side_effect=AlpacaError("insufficient buying power")
    )
    monkeypatch.setattr("trading.execution_service.get_broker", lambda _n: stub)

    with pytest.raises(RiskRejectedError, match="insufficient buying power"):
        await ExecutionService.submit(_intent(), db_path=db)

    import sqlite3

    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT status, risk_checks_json FROM order_executions ORDER BY created_at DESC LIMIT 1"
    ).fetchone()
    conn.close()
    assert row is not None
    assert row["status"] == "rejected"
    assert "insufficient buying power" in row["risk_checks_json"]


@pytest.mark.asyncio
async def test_execution_live_raises_and_places_nothing(
    monkeypatch, tmp_path: Path
) -> None:
    db = tmp_path / "exec.db"
    _patch_passing_env(monkeypatch, db, execution_mode="live")

    from brokers.alpaca import AlpacaAdapter

    stub = object.__new__(AlpacaAdapter)
    place_mock = AsyncMock()
    stub.aplace_order = place_mock  # type: ignore[method-assign]
    monkeypatch.setattr("trading.execution_service.get_broker", lambda _n: stub)

    with pytest.raises(RiskRejectedError, match="live execution not enabled"):
        await ExecutionService.submit(_intent(), db_path=db)

    place_mock.assert_not_called()
    import sqlite3

    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT status FROM order_executions ORDER BY created_at DESC LIMIT 1"
    ).fetchone()
    conn.close()
    assert row is not None
    assert row["status"] == "rejected"


@pytest.mark.asyncio
async def test_execution_paper_option_routes_occ_symbol(
    monkeypatch, tmp_path: Path
) -> None:
    db = tmp_path / "exec.db"
    _patch_passing_env(monkeypatch, db, execution_mode="paper")

    from brokers.alpaca import AlpacaAdapter

    stub = object.__new__(AlpacaAdapter)
    stub.symbol_exists = lambda _symbol: True  # type: ignore[method-assign]
    stub.option_contract_exists = lambda _occ: True  # type: ignore[method-assign]
    place_mock = AsyncMock(
        return_value=PlaceOrderResponse(
            broker_order_id="broker-opt-1",
            status="accepted",
            raw={"id": "broker-opt-1", "status": "accepted"},
        )
    )
    stub.aplace_order = place_mock  # type: ignore[method-assign]
    monkeypatch.setattr("trading.risk_engine.get_broker", lambda _n: stub)
    monkeypatch.setattr("trading.execution_service.get_broker", lambda _n: stub)

    intent = _option_intent()
    result = await ExecutionService.submit(intent, db_path=db)

    assert result.status == "submitted"
    place_mock.assert_called_once()
    req = place_mock.call_args[0][0]
    assert req.symbol == intent.occ_symbol


@pytest.mark.asyncio
async def test_execution_live_option_raises(monkeypatch, tmp_path: Path) -> None:
    db = tmp_path / "exec.db"
    _patch_passing_env(monkeypatch, db, execution_mode="live")

    from brokers.alpaca import AlpacaAdapter

    stub = object.__new__(AlpacaAdapter)
    stub.option_contract_exists = lambda _occ: True  # type: ignore[method-assign]
    place_mock = AsyncMock()
    stub.aplace_order = place_mock  # type: ignore[method-assign]
    monkeypatch.setattr("trading.risk_engine.get_broker", lambda _n: stub)
    monkeypatch.setattr("trading.execution_service.get_broker", lambda _n: stub)

    with pytest.raises(RiskRejectedError, match="live execution not enabled"):
        await ExecutionService.submit(_option_intent(), db_path=db)

    place_mock.assert_not_called()
