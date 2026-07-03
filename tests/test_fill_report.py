"""fill_report push — phone-approved proposal terminal fill outcomes (H3)."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from brokers.base import Order
from db import order_executions as order_executions_mod
from db import paper_orders as paper_orders_mod
from trading.fill_tracker import reconcile_open_executions


def _phone_proposal(
    db_path: Path,
    *,
    proposal_id: str = "paper_abc123",
    symbol: str = "AAPL",
    qty: float = 2.0,
) -> None:
    paper_orders_mod.create_pending_proposal(
        proposal={"symbol": symbol, "side": "buy", "qty": qty, "order_type": "market"},
        intent_fields={
            "symbol": symbol,
            "side": "buy",
            "qty": qty,
            "order_type": "market",
        },
        session_id=None,
        ttl_seconds=300,
        summary=f"BUY {int(qty)} {symbol} @ market",
        db_path=db_path,
    )
    with __import__("sqlite3").connect(db_path) as conn:
        conn.execute(
            "UPDATE paper_order_proposals SET id = ? WHERE id LIKE 'paper_%'",
            (proposal_id,),
        )
        conn.commit()


def _seed_submitted(
    db_path: Path,
    *,
    execution_id: str,
    proposal_id: str,
    broker_order_id: str,
    symbol: str = "AAPL",
    qty: Decimal = Decimal("2"),
    status: str = "submitted",
) -> None:
    order_executions_mod.create_execution_record(
        execution_id=execution_id,
        proposal_id=proposal_id,
        session_id=None,
        symbol=symbol,
        side="buy",
        qty=qty,
        order_type="market",
        limit_price=None,
        execution_mode="paper",
        status=status,
        broker="alpaca",
        broker_order_id=broker_order_id,
        submitted_at="2026-01-01T12:00:00+00:00",
        db_path=db_path,
    )


def _stub_adapter(order: Order):
    return type("S", (), {"aget_order": AsyncMock(return_value=order)})()


def _fill_report_calls(notify: AsyncMock) -> list[dict]:
    return [
        c.kwargs
        for c in notify.await_args_list
        if c.kwargs.get("kind") == "fill_report"
    ]


@pytest.mark.asyncio
async def test_fill_report_on_phone_proposal_filled(monkeypatch, tmp_path: Path) -> None:
    db = tmp_path / "fill.db"
    monkeypatch.setattr(order_executions_mod, "DEFAULT_DB_PATH", db)
    monkeypatch.setattr(paper_orders_mod, "DEFAULT_DB_PATH", db)
    _phone_proposal(db, proposal_id="paper_fill1", qty=1.0)
    _seed_submitted(
        db,
        execution_id="exec_fill1",
        proposal_id="paper_fill1",
        broker_order_id="broker-1",
        qty=Decimal("1"),
    )

    order = Order(
        id="broker-1",
        symbol="AAPL",
        qty=1.0,
        side="buy",
        order_type="market",
        status="filled",
        filled_qty=1.0,
        filled_avg_price=231.44,
        limit_price=None,
    )
    monkeypatch.setattr("trading.fill_tracker.get_broker", lambda _n: _stub_adapter(order))
    notify = AsyncMock(return_value={"id": "notif"})
    monkeypatch.setattr("trading.fill_tracker.notify", notify)

    await reconcile_open_executions("alpaca", db_path=db)

    fill_broadcasts = [
        c.kwargs for c in notify.await_args_list if c.kwargs.get("kind") == "fill"
    ]
    assert len(fill_broadcasts) == 1
    reports = _fill_report_calls(notify)
    assert len(reports) == 1
    call = reports[0]
    assert call["push"] is True
    assert call["severity"] == "info"
    assert call["data"]["type"] == "fill_report"
    assert call["data"]["proposal_id"] == "paper_fill1"
    assert call["data"]["execution_id"] == "exec_fill1"
    assert call["data"]["outcome"] == "filled"
    assert call["data"]["filled_qty"] == "1"
    assert call["data"]["requested_qty"] == "1"
    assert call["data"]["avg_price"] == "231.44"
    assert "status_reason" not in call["data"]
    assert call["body"] == "Filled 1 @ $231.44"


@pytest.mark.asyncio
async def test_fill_report_skipped_for_autonomous_execution(
    monkeypatch, tmp_path: Path
) -> None:
    db = tmp_path / "fill.db"
    monkeypatch.setattr(order_executions_mod, "DEFAULT_DB_PATH", db)
    _seed_submitted(
        db,
        execution_id="exec_auto",
        proposal_id="auto_entry|momentum|1234567890",
        broker_order_id="broker-auto",
    )

    order = Order(
        id="broker-auto",
        symbol="AAPL",
        qty=2.0,
        side="buy",
        order_type="market",
        status="filled",
        filled_qty=2.0,
        limit_price=None,
    )
    monkeypatch.setattr("trading.fill_tracker.get_broker", lambda _n: _stub_adapter(order))
    notify = AsyncMock(return_value={"id": "notif"})
    monkeypatch.setattr("trading.fill_tracker.notify", notify)

    await reconcile_open_executions("alpaca", db_path=db)

    assert len(_fill_report_calls(notify)) == 0
    fill_broadcasts = [
        c.kwargs for c in notify.await_args_list if c.kwargs.get("kind") == "fill"
    ]
    assert len(fill_broadcasts) == 1


@pytest.mark.asyncio
async def test_fill_report_canceled_partial(monkeypatch, tmp_path: Path) -> None:
    db = tmp_path / "fill.db"
    monkeypatch.setattr(order_executions_mod, "DEFAULT_DB_PATH", db)
    monkeypatch.setattr(paper_orders_mod, "DEFAULT_DB_PATH", db)
    _phone_proposal(db, proposal_id="paper_partial", qty=2.0)
    _seed_submitted(
        db,
        execution_id="exec_partial",
        proposal_id="paper_partial",
        broker_order_id="broker-partial",
        qty=Decimal("2"),
    )

    order = Order(
        id="broker-partial",
        symbol="AAPL",
        qty=2.0,
        side="buy",
        order_type="market",
        status="canceled",
        filled_qty=1.0,
        filled_avg_price=100.0,
        limit_price=None,
    )
    monkeypatch.setattr("trading.fill_tracker.get_broker", lambda _n: _stub_adapter(order))
    notify = AsyncMock(return_value={"id": "notif"})
    monkeypatch.setattr("trading.fill_tracker.notify", notify)

    await reconcile_open_executions("alpaca", db_path=db)

    reports = _fill_report_calls(notify)
    assert len(reports) == 1
    data = reports[0]["data"]
    assert data["outcome"] == "partial"
    assert data["filled_qty"] == "1"
    assert data["requested_qty"] == "2"
    assert data["status_reason"] == "partial fill — 1 of 2"
    assert reports[0]["severity"] == "warning"


@pytest.mark.asyncio
async def test_fill_report_expired_unfilled(monkeypatch, tmp_path: Path) -> None:
    db = tmp_path / "fill.db"
    monkeypatch.setattr(order_executions_mod, "DEFAULT_DB_PATH", db)
    monkeypatch.setattr(paper_orders_mod, "DEFAULT_DB_PATH", db)
    _phone_proposal(db, proposal_id="paper_unfilled")
    _seed_submitted(
        db,
        execution_id="exec_unfilled",
        proposal_id="paper_unfilled",
        broker_order_id="broker-unfilled",
    )

    order = Order(
        id="broker-unfilled",
        symbol="AAPL",
        qty=2.0,
        side="buy",
        order_type="market",
        status="expired",
        filled_qty=0.0,
        limit_price=None,
    )
    monkeypatch.setattr("trading.fill_tracker.get_broker", lambda _n: _stub_adapter(order))
    notify = AsyncMock(return_value={"id": "notif"})
    monkeypatch.setattr("trading.fill_tracker.notify", notify)

    await reconcile_open_executions("alpaca", db_path=db)

    reports = _fill_report_calls(notify)
    assert len(reports) == 1
    assert reports[0]["data"]["outcome"] == "unfilled"
    assert reports[0]["data"]["filled_qty"] == "0"
    assert reports[0]["data"]["avg_price"] == ""
    assert reports[0]["data"]["status_reason"] == "unfilled at session close"


@pytest.mark.asyncio
async def test_fill_report_canceled_zero_fill(monkeypatch, tmp_path: Path) -> None:
    db = tmp_path / "fill.db"
    monkeypatch.setattr(order_executions_mod, "DEFAULT_DB_PATH", db)
    monkeypatch.setattr(paper_orders_mod, "DEFAULT_DB_PATH", db)
    _phone_proposal(db, proposal_id="paper_canceled")
    _seed_submitted(
        db,
        execution_id="exec_canceled",
        proposal_id="paper_canceled",
        broker_order_id="broker-canceled",
    )

    order = Order(
        id="broker-canceled",
        symbol="AAPL",
        qty=2.0,
        side="buy",
        order_type="market",
        status="canceled",
        filled_qty=0.0,
        limit_price=None,
    )
    monkeypatch.setattr("trading.fill_tracker.get_broker", lambda _n: _stub_adapter(order))
    notify = AsyncMock(return_value={"id": "notif"})
    monkeypatch.setattr("trading.fill_tracker.notify", notify)

    await reconcile_open_executions("alpaca", db_path=db)

    reports = _fill_report_calls(notify)
    assert len(reports) == 1
    assert reports[0]["data"]["outcome"] == "canceled"
    assert reports[0]["data"]["status_reason"] == "canceled before fill"


@pytest.mark.asyncio
async def test_fill_report_at_most_once(monkeypatch, tmp_path: Path) -> None:
    db = tmp_path / "fill.db"
    monkeypatch.setattr(order_executions_mod, "DEFAULT_DB_PATH", db)
    monkeypatch.setattr(paper_orders_mod, "DEFAULT_DB_PATH", db)
    _phone_proposal(db, proposal_id="paper_once")
    _seed_submitted(
        db,
        execution_id="exec_once",
        proposal_id="paper_once",
        broker_order_id="broker-once",
    )

    order = Order(
        id="broker-once",
        symbol="AAPL",
        qty=2.0,
        side="buy",
        order_type="market",
        status="filled",
        filled_qty=2.0,
        filled_avg_price=150.0,
        limit_price=None,
    )
    monkeypatch.setattr("trading.fill_tracker.get_broker", lambda _n: _stub_adapter(order))
    notify = AsyncMock(return_value={"id": "notif"})
    monkeypatch.setattr("trading.fill_tracker.notify", notify)

    await reconcile_open_executions("alpaca", db_path=db)
    await reconcile_open_executions("alpaca", db_path=db)

    assert len(_fill_report_calls(notify)) == 1
    assert order_executions_mod.get_execution("exec_once", db_path=db)["status"] == "filled"
    assert len(order_executions_mod.list_submitted_executions("alpaca", db_path=db)) == 0


@pytest.mark.asyncio
async def test_fill_report_push_failure_isolated(monkeypatch, tmp_path: Path) -> None:
    db = tmp_path / "fill.db"
    monkeypatch.setattr(order_executions_mod, "DEFAULT_DB_PATH", db)
    monkeypatch.setattr(paper_orders_mod, "DEFAULT_DB_PATH", db)
    _phone_proposal(db, proposal_id="paper_fail")
    _seed_submitted(
        db,
        execution_id="exec_fail",
        proposal_id="paper_fail",
        broker_order_id="broker-fail",
    )

    order = Order(
        id="broker-fail",
        symbol="AAPL",
        qty=2.0,
        side="buy",
        order_type="market",
        status="filled",
        filled_qty=2.0,
        limit_price=None,
    )
    monkeypatch.setattr("trading.fill_tracker.get_broker", lambda _n: _stub_adapter(order))

    call_count = 0

    async def flaky_notify(**kwargs):
        nonlocal call_count
        call_count += 1
        if kwargs.get("kind") == "fill_report":
            raise RuntimeError("push down")
        return {"id": "notif"}

    monkeypatch.setattr("trading.fill_tracker.notify", flaky_notify)

    await reconcile_open_executions("alpaca", db_path=db)

    assert call_count == 2
    assert order_executions_mod.get_execution("exec_fail", db_path=db)["status"] == "filled"


@pytest.mark.asyncio
async def test_fill_report_not_sent_on_partially_filled(
    monkeypatch, tmp_path: Path
) -> None:
    db = tmp_path / "fill.db"
    monkeypatch.setattr(order_executions_mod, "DEFAULT_DB_PATH", db)
    monkeypatch.setattr(paper_orders_mod, "DEFAULT_DB_PATH", db)
    _phone_proposal(db, proposal_id="paper_pf")
    _seed_submitted(
        db,
        execution_id="exec_pf",
        proposal_id="paper_pf",
        broker_order_id="broker-pf",
        qty=Decimal("10"),
        status="submitted",
    )

    order = Order(
        id="broker-pf",
        symbol="AAPL",
        qty=10.0,
        side="buy",
        order_type="market",
        status="partially_filled",
        filled_qty=4.0,
        limit_price=None,
    )
    monkeypatch.setattr("trading.fill_tracker.get_broker", lambda _n: _stub_adapter(order))
    notify = AsyncMock(return_value={"id": "notif"})
    monkeypatch.setattr("trading.fill_tracker.notify", notify)

    await reconcile_open_executions("alpaca", db_path=db)

    assert len(_fill_report_calls(notify)) == 0
    assert (
        order_executions_mod.get_execution("exec_pf", db_path=db)["status"]
        == "partially_filled"
    )


@pytest.mark.asyncio
async def test_fill_report_skipped_for_recorded_web_chat_proposal(
    monkeypatch, tmp_path: Path
) -> None:
    """Reviewer repro: a web-chat propose_order row (status='recorded',
    delivery NULL) executes immediately with no phone card — its fill must
    NOT push a phone fill_report (delivery=='phone' gate), though the
    display 'fill' broadcast still fires."""
    db = tmp_path / "fill.db"
    monkeypatch.setattr(order_executions_mod, "DEFAULT_DB_PATH", db)
    recorded = paper_orders_mod.record_paper_order_proposal(
        proposal={
            "symbol": "AAPL",
            "side": "buy",
            "qty": 2.0,
            "order_type": "market",
        },
        db_path=db,
    )
    _seed_submitted(
        db,
        execution_id="exec_recorded",
        proposal_id=str(recorded["id"]),
        broker_order_id="broker-recorded",
    )

    order = Order(
        id="broker-recorded",
        symbol="AAPL",
        qty=2.0,
        side="buy",
        order_type="market",
        status="filled",
        filled_qty=2.0,
        limit_price=None,
    )
    monkeypatch.setattr(
        "trading.fill_tracker.get_broker", lambda _n: _stub_adapter(order)
    )
    notify = AsyncMock(return_value={"id": "notif"})
    monkeypatch.setattr("trading.fill_tracker.notify", notify)

    await reconcile_open_executions("alpaca", db_path=db)

    assert len(_fill_report_calls(notify)) == 0
    fill_broadcasts = [
        c.kwargs for c in notify.await_args_list if c.kwargs.get("kind") == "fill"
    ]
    assert len(fill_broadcasts) == 1
