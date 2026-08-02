"""Fill tracker — reconcile submitted executions against broker status."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from brokers.alpaca import AlpacaNotFoundError
from brokers.base import Order
from db import order_executions as order_executions_mod
from trading.fill_tracker import (
    _fill_report_outcome,
    _map_broker_status,
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
    account_id: str | None = None,
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
        account_id=account_id,
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
    notify = AsyncMock(return_value={"id": "notif_fill"})
    monkeypatch.setattr("trading.fill_tracker.notify", notify)

    await reconcile_open_executions("alpaca", db_path=db)

    row = order_executions_mod.get_execution("exec_fill1", db_path=db)
    assert row is not None
    assert row["status"] == "filled"
    notify.assert_awaited_once()
    call = notify.await_args.kwargs
    assert call["kind"] == "fill"
    assert call["push"] is False
    assert call["data"]["execution_id"] == "exec_fill1"
    assert call["data"]["status"] == "filled"
    assert call["data"]["filled_qty"] == 10.0


# --- board #41: done_for_day must map to a terminal status ------------------


def test_map_broker_status_aliases_done_for_day() -> None:
    """done_for_day branches on fill progress (board #41 + adversarial review).

    Regression: it used to return None, so a day order that ended the session
    stayed 'submitted' forever. Zero-fill maps to 'expired'; ANY fill maps to
    'partially_filled' so attribution fires and the row keeps polling (Alpaca
    docs: done_for_day orders resume updates next trading day — not final).
    """
    assert _map_broker_status("done_for_day") == "expired"
    assert _map_broker_status("done_for_day", 0) == "expired"
    assert _map_broker_status("DONE_FOR_DAY", None) == "expired"
    assert _map_broker_status("  done_for_day  ", "not-a-number") == "expired"
    assert _map_broker_status("done_for_day", 4.0) == "partially_filled"
    assert _map_broker_status("done_for_day", "4") == "partially_filled"
    # Unknown statuses still map to None (non-terminal, keep polling).
    assert _map_broker_status("pending_new") is None


def test_done_for_day_outcome_semantics() -> None:
    """Zero-fill done_for_day → expired pushes 'unfilled'; a partial routes
    through 'partially_filled', which defers the push to the true terminal."""
    assert _fill_report_outcome("expired", 0) == "unfilled"
    assert _fill_report_outcome("partially_filled", 4) is None


@pytest.mark.asyncio
async def test_reconcile_done_for_day_unfilled_is_terminal(
    monkeypatch, tmp_path: Path
) -> None:
    """A done_for_day order with no fills settles terminal ('expired'), not stuck."""
    db = tmp_path / "fill.db"
    monkeypatch.setattr(order_executions_mod, "DEFAULT_DB_PATH", db)
    _seed_submitted(db, execution_id="exec_dfd", broker_order_id="ord-dfd")

    stub_order = Order(
        id="ord-dfd", symbol="AAPL", qty=10.0, side="buy", order_type="market",
        status="done_for_day", filled_qty=0.0, limit_price=None,
    )
    monkeypatch.setattr(
        "trading.fill_tracker.get_broker",
        lambda _n: type("S", (), {"aget_order": AsyncMock(return_value=stub_order)})(),
    )
    monkeypatch.setattr(
        "trading.fill_tracker.notify", AsyncMock(return_value={"id": "n"})
    )

    await reconcile_open_executions("alpaca", db_path=db)

    row = order_executions_mod.get_execution("exec_dfd", db_path=db)
    assert row is not None and row["status"] == "expired"


@pytest.mark.asyncio
async def test_reconcile_done_for_day_partial_records_managed_fill(
    monkeypatch, tmp_path: Path
) -> None:
    """A partially-filled done_for_day maps to 'partially_filled': the managed
    fill records the real shares AND the row stays in the reconcile pool until
    the broker reports the true terminal status."""
    db = tmp_path / "fill.db"
    monkeypatch.setattr(order_executions_mod, "DEFAULT_DB_PATH", db)
    _seed_submitted(db, execution_id="exec_dfd_p", broker_order_id="ord-dfd-p")

    stub_order = Order(
        id="ord-dfd-p", symbol="AAPL", qty=10.0, side="buy", order_type="market",
        status="done_for_day", filled_qty=4.0, limit_price=None,
    )
    monkeypatch.setattr(
        "trading.fill_tracker.get_broker",
        lambda _n: type("S", (), {"aget_order": AsyncMock(return_value=stub_order)})(),
    )
    monkeypatch.setattr(
        "trading.fill_tracker.notify", AsyncMock(return_value={"id": "n"})
    )
    record_strategy = MagicMock()
    record_managed = MagicMock()
    monkeypatch.setattr(
        "trading.fill_tracker.record_strategy_fill", record_strategy
    )
    monkeypatch.setattr(
        "trading.fill_tracker.record_session_node_managed_fill", record_managed
    )

    await reconcile_open_executions("alpaca", db_path=db)

    row = order_executions_mod.get_execution("exec_dfd_p", db_path=db)
    assert row is not None and row["status"] == "partially_filled"
    record_strategy.assert_called_once()
    record_managed.assert_called_once()
    assert record_managed.call_args.kwargs["status"] == "partially_filled"
    assert record_strategy.call_args.kwargs["status"] == "partially_filled"
    # partially_filled stays in the reconcile pool (polled until truly terminal).
    still_open = order_executions_mod.list_submitted_executions(
        "alpaca", db_path=db
    )
    assert any(r["id"] == "exec_dfd_p" for r in still_open)


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
    notify = AsyncMock(return_value={"id": "notif_fill"})
    monkeypatch.setattr("trading.fill_tracker.notify", notify)

    await reconcile_open_executions("alpaca", db_path=db)

    assert order_executions_mod.get_execution("exec_pf", db_path=db)["status"] == "filled"
    notify.assert_awaited_once()


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
    notify = AsyncMock(return_value={"id": "notif_fill"})
    monkeypatch.setattr("trading.fill_tracker.notify", notify)

    await reconcile_open_executions("alpaca", db_path=db)

    assert order_executions_mod.get_execution("exec_pf2", db_path=db)["status"] == "partially_filled"
    notify.assert_not_awaited()


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
        "trading.fill_tracker.notify",
        AsyncMock(return_value={"id": "notif_fill"}),
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
        account_id="acct_a",
    )

    class StubAdapter:
        async def aget_order_by_client_id(self, client_order_id: str) -> Order | None:
            return None

    monkeypatch.setattr(
        "trading.fill_tracker.get_broker_for_account",
        lambda _a: StubAdapter(),
    )

    await reconcile_pending_executions("alpaca", account_id="acct_a", db_path=db)

    row = order_executions_mod.get_execution("exec_missing", db_path=db)
    assert row is not None
    assert row["status"] == "rejected"


@pytest.mark.asyncio
async def test_an_account_less_pending_sweep_never_rejects(
    monkeypatch, tmp_path: Path
) -> None:
    """Without an account the sweep runs EVERY account's rows through the default
    adapter, so "not found" is what a foreign row looks like by construction.
    Rejecting on that drops the row's sell reservation for a live order."""
    db = tmp_path / "fill.db"
    monkeypatch.setattr(order_executions_mod, "DEFAULT_DB_PATH", db)
    _seed_pending(
        db,
        execution_id="exec_foreign_pending",
        created_at=datetime.now(UTC) - timedelta(minutes=3),
    )

    class StubAdapter:
        async def aget_order_by_client_id(self, client_order_id: str) -> Order | None:
            return None

    monkeypatch.setattr(
        "trading.fill_tracker.get_broker", lambda _name: StubAdapter()
    )

    await reconcile_pending_executions("alpaca", db_path=db)

    row = order_executions_mod.get_execution("exec_foreign_pending", db_path=db)
    assert row is not None
    assert row["status"] == "pending"


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

    assert order_executions_mod.count_pending_executions("alpaca", db_path=db) == 2
    assert (
        order_executions_mod.count_pending_executions(
            "alpaca", account_id="acct_b", db_path=db
        )
        == 1
    )
    stale_a = order_executions_mod.list_stale_pending_executions(
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
        "trading.fill_tracker.notify",
        AsyncMock(return_value={"id": "notif_fill"}),
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


# --- Stuck rows: nothing outside paper ever terminalised them ------------------
#
# `sum_inflight_sell_qty` reserves shares against non-terminal rows, so a row that
# can never reach a terminal status is a position that can never be exited or
# re-stopped. Two branches used to loop forever (no broker_order_id; the broker
# read raising) and the pending sweep only ever looked at paper rows.


def _seed_stuck(
    db_path: Path,
    *,
    execution_id: str,
    status: str,
    execution_mode: str = "paper",
    side: str = "buy",
    broker_order_id: str | None = None,
    account_id: str = "acct_a",
    age: timedelta = timedelta(minutes=5),
) -> None:
    order_executions_mod.create_execution_record(
        execution_id=execution_id,
        proposal_id="prop1",
        session_id=None,
        symbol="AAPL",
        side=side,
        qty=Decimal("10"),
        order_type="market",
        limit_price=None,
        execution_mode=execution_mode,
        status=status,
        broker="alpaca",
        broker_order_id=broker_order_id,
        account_id=account_id,
        db_path=db_path,
    )
    stamp = (datetime.now(UTC) - age).isoformat()
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "UPDATE order_executions SET created_at = ?, updated_at = ?, "
            "submitted_at = ? WHERE id = ?",
            (stamp, stamp, stamp if status != "pending" else None, execution_id),
        )
        conn.commit()


def _install(monkeypatch, adapter) -> None:
    monkeypatch.setattr("trading.fill_tracker.get_broker", lambda _n: adapter)
    monkeypatch.setattr(
        "trading.fill_tracker.get_broker_for_account", lambda _a: adapter
    )
    monkeypatch.setattr(
        "trading.fill_tracker.notify", AsyncMock(return_value={"id": "n"})
    )


@pytest.mark.asyncio
async def test_pending_sweep_reaches_a_row_that_is_not_paper(
    monkeypatch, tmp_path: Path
) -> None:
    """The sweep was `execution_mode='paper'` only — a live row stuck in the
    orphan window (crash between the broker POST and the status write) had
    nothing that could ever end it."""
    db = tmp_path / "fill.db"
    monkeypatch.setattr(order_executions_mod, "DEFAULT_DB_PATH", db)
    _seed_stuck(db, execution_id="exec_live", status="pending", execution_mode="live")

    class StubAdapter:
        async def aget_order_by_client_id(self, client_order_id: str) -> Order | None:
            return None

    _install(monkeypatch, StubAdapter())
    await reconcile_pending_executions("alpaca", account_id="acct_a", db_path=db)

    row = order_executions_mod.get_execution("exec_live", db_path=db)
    assert row is not None and row["status"] == "rejected"


@pytest.mark.asyncio
async def test_stuck_dry_run_row_is_ended_without_asking_the_broker(
    monkeypatch, tmp_path: Path
) -> None:
    """A dry_run row is `pending` between validation and its status write and
    never reaches a broker, so there is nothing to look up — asking would only
    burn a request to be told what the mode already says."""
    db = tmp_path / "fill.db"
    monkeypatch.setattr(order_executions_mod, "DEFAULT_DB_PATH", db)
    _seed_stuck(db, execution_id="exec_dry", status="pending", execution_mode="dry_run")

    lookup = AsyncMock(return_value=None)
    _install(monkeypatch, type("Stub", (), {"aget_order_by_client_id": lookup})())
    await reconcile_pending_executions("alpaca", account_id="acct_a", db_path=db)

    lookup.assert_not_called()
    row = order_executions_mod.get_execution("exec_dry", db_path=db)
    assert row is not None and row["status"] == "rejected"


@pytest.mark.asyncio
async def test_submitted_row_missing_its_broker_id_is_recovered(
    monkeypatch, tmp_path: Path
) -> None:
    """`submitted` with no broker_order_id used to log and `continue` forever.
    The client id is our own, so the order is still findable."""
    db = tmp_path / "fill.db"
    monkeypatch.setattr(order_executions_mod, "DEFAULT_DB_PATH", db)
    _seed_stuck(db, execution_id="exec_noid", status="submitted")

    class StubAdapter:
        async def aget_order_by_client_id(self, client_order_id: str) -> Order | None:
            assert client_order_id == "exec_noid"
            return Order(
                id="broker-found",
                symbol="AAPL",
                qty=10.0,
                side="buy",
                order_type="market",
                status="filled",
                filled_qty=10.0,
                limit_price=None,
            )

        async def aget_order(self, broker_order_id: str) -> Order:
            raise AssertionError("must not poll an id we do not have")

    _install(monkeypatch, StubAdapter())
    await reconcile_open_executions("alpaca", account_id="acct_a", db_path=db)

    row = order_executions_mod.get_execution("exec_noid", db_path=db)
    assert row is not None
    assert row["broker_order_id"] == "broker-found"
    assert row["status"] == "filled"


@pytest.mark.asyncio
async def test_submitted_row_the_broker_never_saw_is_ended(
    monkeypatch, tmp_path: Path
) -> None:
    db = tmp_path / "fill.db"
    monkeypatch.setattr(order_executions_mod, "DEFAULT_DB_PATH", db)
    _seed_stuck(db, execution_id="exec_ghost", status="submitted")

    class StubAdapter:
        async def aget_order_by_client_id(self, client_order_id: str) -> Order | None:
            return None

    _install(monkeypatch, StubAdapter())
    await reconcile_open_executions("alpaca", account_id="acct_a", db_path=db)

    row = order_executions_mod.get_execution("exec_ghost", db_path=db)
    assert row is not None and row["status"] == "rejected"


@pytest.mark.asyncio
async def test_broker_denying_the_order_id_ends_the_row(
    monkeypatch, tmp_path: Path
) -> None:
    """Ending a row needs BOTH routes to agree it is absent — one 404 is one
    route's answer, and Alpaca answers a missing ROUTE with the same 404."""
    db = tmp_path / "fill.db"
    monkeypatch.setattr(order_executions_mod, "DEFAULT_DB_PATH", db)
    _seed_stuck(db, execution_id="exec_404", status="submitted", broker_order_id="b-1")

    class StubAdapter:
        async def aget_order(self, broker_order_id: str) -> Order:
            raise AlpacaNotFoundError("GET /v2/orders/b-1 failed (404): order not found")

        async def aget_order_by_client_id(self, client_order_id: str) -> Order | None:
            return None

    _install(monkeypatch, StubAdapter())
    await reconcile_open_executions("alpaca", account_id="acct_a", db_path=db)

    row = order_executions_mod.get_execution("exec_404", db_path=db)
    assert row is not None and row["status"] == "rejected"


@pytest.mark.asyncio
async def test_a_404_the_client_id_route_contradicts_repoints_the_row(
    monkeypatch, tmp_path: Path
) -> None:
    """The stored broker id can be wrong — the old client-id lookup returned an
    arbitrary order and could write a stranger's id. When the id route denies it
    but our own client id resolves, the order is real: repoint, never end."""
    db = tmp_path / "fill.db"
    monkeypatch.setattr(order_executions_mod, "DEFAULT_DB_PATH", db)
    _seed_stuck(
        db, execution_id="exec_wrongid", status="submitted", broker_order_id="b-stranger"
    )

    class StubAdapter:
        async def aget_order(self, broker_order_id: str) -> Order:
            raise AlpacaNotFoundError("404: order not found")

        async def aget_order_by_client_id(self, client_order_id: str) -> Order | None:
            return Order(
                id="b-really-ours",
                symbol="AAPL",
                qty=10.0,
                side="buy",
                order_type="market",
                status="new",
                filled_qty=0.0,
                limit_price=None,
            )

    _install(monkeypatch, StubAdapter())
    await reconcile_open_executions("alpaca", account_id="acct_a", db_path=db)

    row = order_executions_mod.get_execution("exec_wrongid", db_path=db)
    assert row is not None
    assert row["status"] == "submitted"
    assert row["broker_order_id"] == "b-really-ours"


@pytest.mark.asyncio
async def test_a_404_the_second_route_cannot_confirm_leaves_the_row_open(
    monkeypatch, tmp_path: Path
) -> None:
    db = tmp_path / "fill.db"
    monkeypatch.setattr(order_executions_mod, "DEFAULT_DB_PATH", db)
    _seed_stuck(db, execution_id="exec_unsure", status="submitted", broker_order_id="b-9")

    class StubAdapter:
        async def aget_order(self, broker_order_id: str) -> Order:
            raise AlpacaNotFoundError("404: order not found")

        async def aget_order_by_client_id(self, client_order_id: str) -> Order | None:
            raise RuntimeError("read timeout")

    _install(monkeypatch, StubAdapter())
    await reconcile_open_executions("alpaca", account_id="acct_a", db_path=db)

    row = order_executions_mod.get_execution("exec_unsure", db_path=db)
    assert row is not None and row["status"] == "submitted"


@pytest.mark.asyncio
async def test_a_failed_read_never_ends_the_row(monkeypatch, tmp_path: Path) -> None:
    db = tmp_path / "fill.db"
    monkeypatch.setattr(order_executions_mod, "DEFAULT_DB_PATH", db)
    _seed_stuck(db, execution_id="exec_flaky", status="submitted", broker_order_id="b-2")

    class StubAdapter:
        async def aget_order(self, broker_order_id: str) -> Order:
            raise RuntimeError("read timeout")

        async def aget_order_by_client_id(self, client_order_id: str) -> Order | None:
            return None

    _install(monkeypatch, StubAdapter())
    await reconcile_open_executions("alpaca", account_id="acct_a", db_path=db)

    row = order_executions_mod.get_execution("exec_flaky", db_path=db)
    assert row is not None and row["status"] == "submitted"


@pytest.mark.asyncio
async def test_a_just_placed_order_is_never_ended_by_a_404(
    monkeypatch, tmp_path: Path
) -> None:
    """A 404 seconds after the place is the broker not having caught up, not an
    absent order — end the row only once it is past the orphan window."""
    db = tmp_path / "fill.db"
    monkeypatch.setattr(order_executions_mod, "DEFAULT_DB_PATH", db)
    _seed_stuck(
        db,
        execution_id="exec_fresh",
        status="submitted",
        broker_order_id="b-3",
        age=timedelta(seconds=5),
    )

    class StubAdapter:
        async def aget_order(self, broker_order_id: str) -> Order:
            raise AlpacaNotFoundError("404: order not found")

        async def aget_order_by_client_id(self, client_order_id: str) -> Order | None:
            return None

    _install(monkeypatch, StubAdapter())
    await reconcile_open_executions("alpaca", account_id="acct_a", db_path=db)

    row = order_executions_mod.get_execution("exec_fresh", db_path=db)
    assert row is not None and row["status"] == "submitted"


@pytest.mark.asyncio
async def test_a_404_from_the_wrong_account_never_ends_the_row(
    monkeypatch, tmp_path: Path
) -> None:
    """Called without an account, the reconciler polls EVERY account's rows
    through the default adapter — every foreign row then 404s. Killing live
    orders on that evidence is exactly the oversell the reservation prevents."""
    db = tmp_path / "fill.db"
    monkeypatch.setattr(order_executions_mod, "DEFAULT_DB_PATH", db)
    _seed_stuck(
        db,
        execution_id="exec_foreign",
        status="submitted",
        broker_order_id="b-4",
        account_id="acct_other",
    )

    class StubAdapter:
        async def aget_order(self, broker_order_id: str) -> Order:
            raise AlpacaNotFoundError("404: order not found")

        async def aget_order_by_client_id(self, client_order_id: str) -> Order | None:
            return None

    _install(monkeypatch, StubAdapter())
    await reconcile_open_executions("alpaca", db_path=db)

    row = order_executions_mod.get_execution("exec_foreign", db_path=db)
    assert row is not None and row["status"] == "submitted"


@pytest.mark.asyncio
async def test_ending_a_stuck_sell_frees_the_shares_it_reserved(
    monkeypatch, tmp_path: Path
) -> None:
    """A stuck sell row reserves shares against every later sell at the DB level.

    Honest scope (review A LOW-2): production also passes `snapshot_captured_at`,
    and that rule already excludes a row older than the current snapshot — so
    this is the reservation the reconcile removes at the source, not a hole the
    live sell gate had. What terminalising really buys is upstream: the row stops
    consuming daily-order and notional budget, and stops looking like a resting
    protective stop.
    """
    db = tmp_path / "fill.db"
    monkeypatch.setattr(order_executions_mod, "DEFAULT_DB_PATH", db)
    _seed_stuck(
        db,
        execution_id="exec_stuck_sell",
        status="submitted",
        side="sell",
        broker_order_id="b-stuck",
    )

    reserved = order_executions_mod.sum_inflight_sell_qty(
        symbol="AAPL", account_id="acct_a", db_path=db
    )
    assert reserved == Decimal("10")

    class StubAdapter:
        async def aget_order(self, broker_order_id: str) -> Order:
            raise AlpacaNotFoundError("404: order not found")

        async def aget_order_by_client_id(self, client_order_id: str) -> Order | None:
            return None

    _install(monkeypatch, StubAdapter())
    await reconcile_open_executions("alpaca", account_id="acct_a", db_path=db)

    assert order_executions_mod.sum_inflight_sell_qty(
        symbol="AAPL", account_id="acct_a", db_path=db
    ) == Decimal("0")


@pytest.mark.asyncio
async def test_an_unreadable_timestamp_does_not_kill_the_whole_pass(
    monkeypatch, tmp_path: Path
) -> None:
    """The age check parses a stored timestamp, and a raise here would escape the
    row loop and abandon the reconcile for every OTHER row in the pass."""
    db = tmp_path / "fill.db"
    monkeypatch.setattr(order_executions_mod, "DEFAULT_DB_PATH", db)
    _seed_stuck(db, execution_id="exec_corrupt", status="submitted", broker_order_id="b-5")
    _seed_stuck(db, execution_id="exec_healthy", status="submitted", broker_order_id="b-6")
    with sqlite3.connect(db) as conn:
        conn.execute(
            "UPDATE order_executions SET created_at = 'not-a-date', "
            "submitted_at = NULL WHERE id = 'exec_corrupt'"
        )
        conn.commit()

    class StubAdapter:
        async def aget_order_by_client_id(self, client_order_id: str) -> Order | None:
            return None

        async def aget_order(self, broker_order_id: str) -> Order:
            if broker_order_id == "b-5":
                raise AlpacaNotFoundError("404: order not found")
            return Order(
                id="b-6",
                symbol="AAPL",
                qty=10.0,
                side="buy",
                order_type="market",
                status="filled",
                filled_qty=10.0,
                limit_price=None,
            )

    _install(monkeypatch, StubAdapter())
    await reconcile_open_executions("alpaca", account_id="acct_a", db_path=db)

    corrupt = order_executions_mod.get_execution("exec_corrupt", db_path=db)
    healthy = order_executions_mod.get_execution("exec_healthy", db_path=db)
    assert corrupt is not None and corrupt["status"] == "submitted"
    assert healthy is not None and healthy["status"] == "filled"


@pytest.mark.asyncio
async def test_ending_a_row_keeps_its_risk_check_trail(
    monkeypatch, tmp_path: Path
) -> None:
    """risk_checks_json is the only record of which gates let a real-money order
    through. The orphan reason is merged in, never written over it."""
    db = tmp_path / "fill.db"
    monkeypatch.setattr(order_executions_mod, "DEFAULT_DB_PATH", db)
    _seed_stuck(db, execution_id="exec_trail", status="submitted", broker_order_id="b-t")
    order_executions_mod.update_execution_status(
        "exec_trail",
        "submitted",
        risk_checks_json={"sell_position": "pass: closing 50 of 50 long"},
        db_path=db,
    )

    class StubAdapter:
        async def aget_order(self, broker_order_id: str) -> Order:
            raise AlpacaNotFoundError("404: order not found")

        async def aget_order_by_client_id(self, client_order_id: str) -> Order | None:
            return None

    _install(monkeypatch, StubAdapter())
    await reconcile_open_executions("alpaca", account_id="acct_a", db_path=db)

    row = order_executions_mod.get_execution("exec_trail", db_path=db)
    assert row is not None and row["status"] == "rejected"
    assert row["risk_checks_json"]["sell_position"] == "pass: closing 50 of 50 long"
    assert "orphan" in row["risk_checks_json"]["orphan_reason"]


@pytest.mark.asyncio
async def test_a_recovered_row_gets_a_submitted_at(monkeypatch, tmp_path: Path) -> None:
    """sum_inflight_sell_qty bounds an unplaced row by age and a placed one by
    the snapshot timestamp — a row with a broker id but no submitted_at escapes
    both, and reserves forever."""
    db = tmp_path / "fill.db"
    monkeypatch.setattr(order_executions_mod, "DEFAULT_DB_PATH", db)
    _seed_stuck(db, execution_id="exec_recovered", status="submitted")
    with sqlite3.connect(db) as conn:
        conn.execute(
            "UPDATE order_executions SET submitted_at = NULL WHERE id = 'exec_recovered'"
        )
        conn.commit()

    class StubAdapter:
        async def aget_order_by_client_id(self, client_order_id: str) -> Order | None:
            return Order(
                id="b-found",
                symbol="AAPL",
                qty=10.0,
                side="buy",
                order_type="market",
                status="new",
                filled_qty=0.0,
                limit_price=None,
            )

    _install(monkeypatch, StubAdapter())
    await reconcile_open_executions("alpaca", account_id="acct_a", db_path=db)

    row = order_executions_mod.get_execution("exec_recovered", db_path=db)
    assert row is not None
    assert row["broker_order_id"] == "b-found"
    assert row["submitted_at"]
