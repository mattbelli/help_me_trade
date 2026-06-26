"""ExecutionService — dry_run path, kill switch, risk rejection."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from brokers.alpaca import AlpacaAdapter
from brokers.base import Account, Position
from db import order_executions as order_executions_mod
from db import sqlite as sqlite_mod
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


def _stub_alpaca(*, tradable: bool = True) -> AlpacaAdapter:
    stub = object.__new__(AlpacaAdapter)
    stub.symbol_exists = lambda _symbol: tradable  # type: ignore[method-assign]
    return stub


def _patch_passing_env(monkeypatch, db_path: Path) -> None:
    from db import base as db_base

    monkeypatch.setattr(db_base, "DEFAULT_DB_PATH", db_path)
    monkeypatch.setattr(sqlite_mod, "DEFAULT_DB_PATH", db_path)
    monkeypatch.setattr(order_executions_mod, "DEFAULT_DB_PATH", db_path)
    _save_fresh_snapshot(db_path)

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
async def test_execution_service_dry_run(monkeypatch, tmp_path: Path) -> None:
    db = tmp_path / "exec.db"
    _patch_passing_env(monkeypatch, db)

    result = await ExecutionService.submit(_intent(), db_path=db)

    assert result.status == "dry_run"
    assert result.broker_order_id is None
    assert result.dry_run_log is not None
    assert "DRY RUN" in result.dry_run_log
    row = order_executions_mod.get_execution(result.execution_id, db_path=db)
    assert row is not None
    assert row["status"] == "dry_run"
    assert row["broker_order_id"] is None


@pytest.mark.asyncio
async def test_execution_service_kill_switch(monkeypatch, tmp_path: Path) -> None:
    db = tmp_path / "exec.db"
    _patch_passing_env(monkeypatch, db)
    monkeypatch.setattr("trading.execution_service.is_kill_switch_active", lambda: True)

    with pytest.raises(RiskRejectedError, match="kill switch"):
        await ExecutionService.submit(_intent(), db_path=db)


@pytest.mark.asyncio
async def test_execution_service_stale_snapshot_rejected(
    monkeypatch, tmp_path: Path
) -> None:
    db = tmp_path / "exec.db"
    _patch_passing_env(monkeypatch, db)
    stale = (datetime.now(UTC) - timedelta(minutes=10)).isoformat()
    monkeypatch.setattr(
        "trading.risk_engine.get_latest_snapshot",
        lambda *_a, **_k: {
            "captured_at": stale,
            "account": {"buying_power": 100_000, "portfolio_value": 100_000},
            "open_orders": [],
            "positions": [],
        },
    )

    with pytest.raises(RiskRejectedError):
        await ExecutionService.submit(_intent(), db_path=db)

    import sqlite3

    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT status FROM order_executions WHERE status = 'rejected'"
    ).fetchall()
    conn.close()
    assert len(rows) >= 1


@pytest.mark.asyncio
async def test_execution_service_validate_exception_writes_error_row(
    monkeypatch, tmp_path: Path
) -> None:
    db = tmp_path / "exec.db"
    _patch_passing_env(monkeypatch, db)

    async def _boom(*_a, **_k):
        raise RuntimeError("db exploded")

    monkeypatch.setattr("trading.execution_service.RiskEngine.validate", _boom)

    with pytest.raises(RuntimeError, match="db exploded"):
        await ExecutionService.submit(_intent(), db_path=db)

    import sqlite3

    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT status, risk_checks_json FROM order_executions ORDER BY created_at DESC LIMIT 1"
    ).fetchone()
    conn.close()
    assert row is not None
    assert row["status"] == "error"
    assert "db exploded" in row["risk_checks_json"]


@pytest.mark.asyncio
async def test_execution_service_risk_failure_persists_rejected(
    monkeypatch, tmp_path: Path
) -> None:
    db = tmp_path / "exec.db"
    _patch_passing_env(monkeypatch, db)
    monkeypatch.setattr(
        "trading.risk_engine.market_hub.quote_snapshot",
        lambda _symbols: {},
    )

    async def _no_rest(_symbol):
        return None

    monkeypatch.setattr("trading.risk_engine._rest_quote_price", _no_rest)

    with pytest.raises(RiskRejectedError):
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
    assert "live_quote" in row["risk_checks_json"]
