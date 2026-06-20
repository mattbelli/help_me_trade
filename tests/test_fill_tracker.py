"""Fill tracker — reconcile submitted executions against broker status."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from brokers.base import Order
from db import order_executions as order_executions_mod
from trading.fill_tracker import (
    reconcile_open_executions,
    reconcile_pending_executions,
)


def _seed_submitted(
    db_path: Path,
    *,
    execution_id: str = "exec_fill1",
    broker_order_id: str = "broker-ord-1",
    symbol: str = "AAPL",
) -> None:
    order_executions_mod.create_execution_record(
        execution_id=execution_id,
        proposal_id="prop1",
        session_id=None,
        symbol=symbol,
        side="buy",
        qty=Decimal("10"),
        order_type="market",
        limit_price=None,
        execution_mode="paper",
        status="submitted",
        broker="alpaca",
        broker_order_id=broker_order_id,
        submitted_at="2026-01-01T12:00:00+00:00",
        db_path=db_path,
    )


def _seed_pending(
    db_path: Path,
    *,
    execution_id: str = "exec_pending1",
    created_at: datetime,
    symbol: str = "AAPL",
) -> None:
    order_executions_mod.create_execution_record(
        execution_id=execution_id,
        proposal_id="prop1",
        session_id=None,
        symbol=symbol,
        side="buy",
        qty=Decimal("10"),
        order_type="market",
        limit_price=None,
        execution_mode="paper",
        status="pending",
        broker="alpaca",
        db_path=db_path,
    )
    created_iso = created_at.astimezone(UTC).isoformat()
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "UPDATE order_executions SET created_at = ?, updated_at = ? WHERE id = ?",
            (created_iso, created_iso, execution_id),
        )
        conn.commit()


@pytest.mark.asyncio
async def test_reconcile_updates_filled_and_broadcasts(
    monkeypatch, tmp_path: Path
) -> None:
    db = tmp_path / "fill.db"
    monkeypatch.setattr(order_executions_mod, "DEFAULT_DB_PATH", db)
    _seed_submitted(db)

    stub_order = Order(
        id="broker-ord-1",
        symbol="AAPL",
        qty=10.0,
        side="buy",
        order_type="market",
        status="filled",
        filled_qty=10.0,
        limit_price=None,
    )

    class StubAdapter:
        async def aget_order(self, broker_order_id: str) -> Order:
            assert broker_order_id == "broker-ord-1"
            return stub_order

    monkeypatch.setattr(
        "trading.fill_tracker.get_broker",
        lambda _name: StubAdapter(),
    )
    broadcast = AsyncMock()
    monkeypatch.setattr("trading.fill_tracker.market_hub.broadcast_alert", broadcast)

    await reconcile_open_executions("alpaca", db_path=db)

    row = order_executions_mod.get_execution("exec_fill1", db_path=db)
    assert row is not None
    assert row["status"] == "filled"
    broadcast.assert_awaited_once()
    payload = broadcast.await_args.args[0]
    assert payload["type"] == "fill"
    assert payload["execution_id"] == "exec_fill1"
    assert payload["status"] == "filled"
    assert payload["filled_qty"] == 10.0


@pytest.mark.asyncio
async def test_reconcile_continues_after_lookup_error(
    monkeypatch, tmp_path: Path
) -> None:
    db = tmp_path / "fill.db"
    monkeypatch.setattr(order_executions_mod, "DEFAULT_DB_PATH", db)
    _seed_submitted(db, execution_id="exec_bad", broker_order_id="bad-ord")
    _seed_submitted(db, execution_id="exec_ok", broker_order_id="ok-ord", symbol="MSFT")

    calls: list[str] = []

    class StubAdapter:
        async def aget_order(self, broker_order_id: str) -> Order:
            calls.append(broker_order_id)
            if broker_order_id == "bad-ord":
                raise RuntimeError("broker timeout")
            return Order(
                id="ok-ord",
                symbol="MSFT",
                qty=5.0,
                side="buy",
                order_type="market",
                status="filled",
                filled_qty=5.0,
                limit_price=None,
            )

    monkeypatch.setattr(
        "trading.fill_tracker.get_broker",
        lambda _name: StubAdapter(),
    )
    monkeypatch.setattr(
        "trading.fill_tracker.market_hub.broadcast_alert",
        AsyncMock(),
    )

    await reconcile_open_executions("alpaca", db_path=db)

    assert calls == ["bad-ord", "ok-ord"]
    bad = order_executions_mod.get_execution("exec_bad", db_path=db)
    ok = order_executions_mod.get_execution("exec_ok", db_path=db)
    assert bad is not None and bad["status"] == "submitted"
    assert ok is not None and ok["status"] == "filled"


@pytest.mark.asyncio
async def test_reconcile_pending_promotes_found_order(
    monkeypatch, tmp_path: Path
) -> None:
    db = tmp_path / "fill.db"
    monkeypatch.setattr(order_executions_mod, "DEFAULT_DB_PATH", db)
    _seed_pending(
        db,
        execution_id="exec_orphan",
        created_at=datetime.now(UTC) - timedelta(seconds=60),
    )

    stub_order = Order(
        id="broker-recovered",
        symbol="AAPL",
        qty=10.0,
        side="buy",
        order_type="market",
        status="new",
        filled_qty=0.0,
        limit_price=None,
    )

    class StubAdapter:
        async def aget_order_by_client_id(self, client_order_id: str) -> Order | None:
            assert client_order_id == "exec_orphan"
            return stub_order

    monkeypatch.setattr(
        "trading.fill_tracker.get_broker",
        lambda _name: StubAdapter(),
    )

    await reconcile_pending_executions("alpaca", db_path=db)

    row = order_executions_mod.get_execution("exec_orphan", db_path=db)
    assert row is not None
    assert row["status"] == "submitted"
    assert row["broker_order_id"] == "broker-recovered"
    assert row["submitted_at"] is not None


@pytest.mark.asyncio
async def test_reconcile_pending_rejects_stale_not_found(
    monkeypatch, tmp_path: Path
) -> None:
    db = tmp_path / "fill.db"
    monkeypatch.setattr(order_executions_mod, "DEFAULT_DB_PATH", db)
    _seed_pending(
        db,
        execution_id="exec_missing",
        created_at=datetime.now(UTC) - timedelta(minutes=3),
    )

    class StubAdapter:
        async def aget_order_by_client_id(self, client_order_id: str) -> Order | None:
            return None

    monkeypatch.setattr(
        "trading.fill_tracker.get_broker",
        lambda _name: StubAdapter(),
    )

    await reconcile_pending_executions("alpaca", db_path=db)

    row = order_executions_mod.get_execution("exec_missing", db_path=db)
    assert row is not None
    assert row["status"] == "rejected"
    assert row["risk_checks_json"]["reason"] == "orphan: not found at broker"


@pytest.mark.asyncio
async def test_reconcile_pending_skips_recent_rows(
    monkeypatch, tmp_path: Path
) -> None:
    db = tmp_path / "fill.db"
    monkeypatch.setattr(order_executions_mod, "DEFAULT_DB_PATH", db)
    _seed_pending(
        db,
        execution_id="exec_recent",
        created_at=datetime.now(UTC) - timedelta(seconds=10),
    )

    lookup = AsyncMock()
    monkeypatch.setattr(
        "trading.fill_tracker.get_broker",
        lambda _name: type("Stub", (), {"aget_order_by_client_id": lookup})(),
    )

    await reconcile_pending_executions("alpaca", db_path=db)

    lookup.assert_not_called()
    row = order_executions_mod.get_execution("exec_recent", db_path=db)
    assert row is not None
    assert row["status"] == "pending"


@pytest.mark.asyncio
async def test_reconcile_pending_continues_after_lookup_error(
    monkeypatch, tmp_path: Path
) -> None:
    db = tmp_path / "fill.db"
    monkeypatch.setattr(order_executions_mod, "DEFAULT_DB_PATH", db)
    _seed_pending(
        db,
        execution_id="exec_bad_lookup",
        created_at=datetime.now(UTC) - timedelta(seconds=60),
    )
    _seed_pending(
        db,
        execution_id="exec_ok_lookup",
        created_at=datetime.now(UTC) - timedelta(seconds=60),
        symbol="MSFT",
    )

    calls: list[str] = []

    class StubAdapter:
        async def aget_order_by_client_id(self, client_order_id: str) -> Order | None:
            calls.append(client_order_id)
            if client_order_id == "exec_bad_lookup":
                raise RuntimeError("broker timeout")
            return Order(
                id="broker-msft",
                symbol="MSFT",
                qty=5.0,
                side="buy",
                order_type="market",
                status="new",
                filled_qty=0.0,
                limit_price=None,
            )

    monkeypatch.setattr(
        "trading.fill_tracker.get_broker",
        lambda _name: StubAdapter(),
    )

    await reconcile_pending_executions("alpaca", db_path=db)

    assert calls == ["exec_bad_lookup", "exec_ok_lookup"]
    bad = order_executions_mod.get_execution("exec_bad_lookup", db_path=db)
    ok = order_executions_mod.get_execution("exec_ok_lookup", db_path=db)
    assert bad is not None and bad["status"] == "pending"
    assert ok is not None and ok["status"] == "submitted"
    assert ok["broker_order_id"] == "broker-msft"
