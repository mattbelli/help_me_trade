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


def _seed_partial(db_path: Path, *, execution_id: str, broker_order_id: str) -> None:
    order_executions_mod.create_execution_record(
        execution_id=execution_id, proposal_id="prop1", session_id=None, symbol="AAPL",
        side="buy", qty=Decimal("10"), order_type="market", limit_price=None,
        execution_mode="paper", status="partially_filled", broker="alpaca",
        broker_order_id=broker_order_id, submitted_at="2026-01-01T12:00:00+00:00", db_path=db_path,
    )


@pytest.mark.asyncio
async def test_reconcile_promotes_partially_filled_to_filled(monkeypatch, tmp_path: Path) -> None:
    """A partially_filled order stays in the reconcile pool and settles to filled.

    Regression: it used to exit the pool the moment it was stamped partially_filled,
    so the row never re-polled the broker and stuck there forever (momentum's 86-order
    rebalance left 40 stuck despite being fully filled at the broker).
    """
    db = tmp_path / "fill.db"
    monkeypatch.setattr(order_executions_mod, "DEFAULT_DB_PATH", db)
    _seed_partial(db, execution_id="exec_pf", broker_order_id="broker-ord-pf")

    stub_order = Order(id="broker-ord-pf", symbol="AAPL", qty=10.0, side="buy",
                       order_type="market", status="filled", filled_qty=10.0, limit_price=None)
    monkeypatch.setattr("trading.fill_tracker.get_broker",
                        lambda _n: type("S", (), {"aget_order": AsyncMock(return_value=stub_order)})())
    broadcast = AsyncMock()
    monkeypatch.setattr("trading.fill_tracker.market_hub.broadcast_alert", broadcast)

    await reconcile_open_executions("alpaca", db_path=db)

    assert order_executions_mod.get_execution("exec_pf", db_path=db)["status"] == "filled"
    broadcast.assert_awaited_once()


@pytest.mark.asyncio
async def test_reconcile_skips_unchanged_partially_filled(monkeypatch, tmp_path: Path) -> None:
    """A still-partial order is not re-written or re-broadcast (gate on status change)."""
    db = tmp_path / "fill.db"
    monkeypatch.setattr(order_executions_mod, "DEFAULT_DB_PATH", db)
    _seed_partial(db, execution_id="exec_pf2", broker_order_id="broker-ord-pf2")

    stub_order = Order(id="broker-ord-pf2", symbol="AAPL", qty=10.0, side="buy",
                       order_type="market", status="partially_filled", filled_qty=4.0, limit_price=None)
    monkeypatch.setattr("trading.fill_tracker.get_broker",
                        lambda _n: type("S", (), {"aget_order": AsyncMock(return_value=stub_order)})())
    broadcast = AsyncMock()
    monkeypatch.setattr("trading.fill_tracker.market_hub.broadcast_alert", broadcast)

    await reconcile_open_executions("alpaca", db_path=db)

    assert order_executions_mod.get_execution("exec_pf2", db_path=db)["status"] == "partially_filled"
    broadcast.assert_not_awaited()


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


def _seed_execution_with_account(
    db_path: Path,
    *,
    execution_id: str,
    status: str,
    account_id: str | None = None,
    broker_order_id: str | None = None,
    created_at: datetime | None = None,
) -> None:
    order_executions_mod.create_execution_record(
        execution_id=execution_id,
        proposal_id="prop1",
        session_id=None,
        symbol="AAPL",
        side="buy",
        qty=Decimal("10"),
        order_type="market",
        limit_price=None,
        execution_mode="paper",
        status=status,
        broker="alpaca",
        broker_order_id=broker_order_id,
        account_id=account_id,
        db_path=db_path,
    )
    if created_at is not None:
        created_iso = created_at.astimezone(UTC).isoformat()
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                "UPDATE order_executions SET created_at = ?, updated_at = ? WHERE id = ?",
                (created_iso, created_iso, execution_id),
            )
            conn.commit()


def test_execution_list_count_filters_by_account(tmp_path: Path) -> None:
    db = tmp_path / "accounts.db"
    _seed_execution_with_account(
        db, execution_id="exec_a_sub", status="submitted", account_id="acct_a"
    )
    _seed_execution_with_account(
        db,
        execution_id="exec_b_sub",
        status="submitted",
        account_id="acct_b",
        broker_order_id="ord-b",
    )
    _seed_execution_with_account(
        db,
        execution_id="exec_a_pend",
        status="pending",
        account_id="acct_a",
        created_at=datetime.now(UTC) - timedelta(minutes=5),
    )
    _seed_execution_with_account(
        db,
        execution_id="exec_b_pend",
        status="pending",
        account_id="acct_b",
        created_at=datetime.now(UTC) - timedelta(minutes=5),
    )

    assert order_executions_mod.count_submitted_executions("alpaca", db_path=db) == 2
    assert (
        order_executions_mod.count_submitted_executions(
            "alpaca", account_id="acct_a", db_path=db
        )
        == 1
    )
    assert len(order_executions_mod.list_submitted_executions("alpaca", db_path=db)) == 2
    listed_a = order_executions_mod.list_submitted_executions(
        "alpaca", account_id="acct_a", db_path=db
    )
    assert [row["id"] for row in listed_a] == ["exec_a_sub"]

    assert order_executions_mod.count_pending_paper_executions("alpaca", db_path=db) == 2
    assert (
        order_executions_mod.count_pending_paper_executions(
            "alpaca", account_id="acct_b", db_path=db
        )
        == 1
    )
    stale_a = order_executions_mod.list_stale_pending_paper_executions(
        "alpaca", account_id="acct_a", db_path=db
    )
    assert [row["id"] for row in stale_a] == ["exec_a_pend"]


@pytest.mark.asyncio
async def test_reconcile_open_uses_account_adapter_and_scope(
    monkeypatch, tmp_path: Path
) -> None:
    db = tmp_path / "fill.db"
    monkeypatch.setattr(order_executions_mod, "DEFAULT_DB_PATH", db)
    _seed_execution_with_account(
        db,
        execution_id="exec_a",
        status="submitted",
        account_id="acct_a",
        broker_order_id="ord-a",
    )
    _seed_execution_with_account(
        db,
        execution_id="exec_b",
        status="submitted",
        account_id="acct_b",
        broker_order_id="ord-b",
    )

    broker_calls: list[str] = []
    account_calls: list[str] = []

    class StubAdapter:
        async def aget_order(self, broker_order_id: str) -> Order:
            return Order(
                id=broker_order_id,
                symbol="AAPL",
                qty=10.0,
                side="buy",
                order_type="market",
                status="filled",
                filled_qty=10.0,
                limit_price=None,
            )

    def fake_get_broker(name: str) -> StubAdapter:
        broker_calls.append(name)
        return StubAdapter()

    def fake_get_broker_for_account(account_id: str) -> StubAdapter:
        account_calls.append(account_id)
        return StubAdapter()

    monkeypatch.setattr("trading.fill_tracker.get_broker", fake_get_broker)
    monkeypatch.setattr(
        "trading.fill_tracker.get_broker_for_account", fake_get_broker_for_account
    )
    monkeypatch.setattr(
        "trading.fill_tracker.market_hub.broadcast_alert",
        AsyncMock(),
    )

    await reconcile_open_executions("alpaca", account_id="acct_a", db_path=db)

    assert account_calls == ["acct_a"]
    assert broker_calls == []
    a = order_executions_mod.get_execution("exec_a", db_path=db)
    b = order_executions_mod.get_execution("exec_b", db_path=db)
    assert a is not None and a["status"] == "filled"
    assert b is not None and b["status"] == "submitted"


@pytest.mark.asyncio
async def test_reconcile_pending_orphan_reject_scoped_to_account(
    monkeypatch, tmp_path: Path
) -> None:
    db = tmp_path / "fill.db"
    monkeypatch.setattr(order_executions_mod, "DEFAULT_DB_PATH", db)
    stale = datetime.now(UTC) - timedelta(minutes=3)
    _seed_execution_with_account(
        db,
        execution_id="exec_a_orphan",
        status="pending",
        account_id="acct_a",
        created_at=stale,
    )
    _seed_execution_with_account(
        db,
        execution_id="exec_b_orphan",
        status="pending",
        account_id="acct_b",
        created_at=stale,
    )

    account_calls: list[str] = []

    class StubAdapter:
        async def aget_order_by_client_id(self, client_order_id: str) -> Order | None:
            return None

    def fake_get_broker_for_account(account_id: str) -> StubAdapter:
        account_calls.append(account_id)
        return StubAdapter()

    monkeypatch.setattr(
        "trading.fill_tracker.get_broker_for_account", fake_get_broker_for_account
    )

    await reconcile_pending_executions("alpaca", account_id="acct_a", db_path=db)

    assert account_calls == ["acct_a"]
    a = order_executions_mod.get_execution("exec_a_orphan", db_path=db)
    b = order_executions_mod.get_execution("exec_b_orphan", db_path=db)
    assert a is not None and a["status"] == "rejected"
    assert b is not None and b["status"] == "pending"
