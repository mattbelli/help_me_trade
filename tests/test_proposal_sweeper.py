"""R3 proposal expiry sweeper, proposal_missed push, and missed-list route."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from brokers.alpaca import AlpacaAdapter
from brokers.base import Account, Position
from db import base as db_base
from db import order_executions as order_executions_mod
from db import paper_orders as paper_orders_mod
from db import sqlite as sqlite_mod
from db.decision_journal import get_by_proposal
from trading.proposals import create_phone_proposal, sweep_expired_proposals


def _stub_alpaca(*, tradable: bool = True) -> AlpacaAdapter:
    stub = object.__new__(AlpacaAdapter)
    stub.symbol_exists = lambda _symbol: tradable  # type: ignore[method-assign]
    return stub


def _patch_passing_env(monkeypatch, db_path: Path) -> None:
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
        "trading.proposals.notify",
        AsyncMock(return_value={}),
    )


def _order_args(**overrides) -> dict:
    base = {"symbol": "AAPL", "side": "buy", "qty": 2, "order_type": "market"}
    base.update(overrides)
    return base


def _expire(db: Path, proposal_id: str) -> None:
    past = (datetime.now(UTC) - timedelta(seconds=5)).isoformat()
    conn = sqlite3.connect(db)
    conn.execute(
        "UPDATE paper_order_proposals SET expires_at = ? WHERE id = ?",
        (past, proposal_id),
    )
    conn.commit()
    conn.close()


def _set_status(db: Path, proposal_id: str, status: str) -> None:
    conn = sqlite3.connect(db)
    conn.execute(
        "UPDATE paper_order_proposals SET status = ? WHERE id = ?",
        (status, proposal_id),
    )
    conn.commit()
    conn.close()


def _set_created_at(db: Path, proposal_id: str, created_at: str) -> None:
    conn = sqlite3.connect(db)
    conn.execute(
        "UPDATE paper_order_proposals SET created_at = ? WHERE id = ?",
        (created_at, proposal_id),
    )
    conn.commit()
    conn.close()


@pytest.mark.asyncio
async def test_sweeper_expires_past_ttl_and_pushes(monkeypatch, tmp_path: Path) -> None:
    db = tmp_path / "sweep.db"
    notify_mock = AsyncMock(return_value={})
    _patch_passing_env(monkeypatch, db)
    monkeypatch.setattr("trading.proposals.notify", notify_mock)

    row = await create_phone_proposal(
        _order_args(), score=False, ttl_seconds=300, db_path=db
    )
    _expire(db, row["id"])
    notify_mock.reset_mock()

    count = await sweep_expired_proposals(db_path=db)

    assert count == 1
    stored = paper_orders_mod.get_proposal(row["id"], db_path=db)
    assert stored is not None
    assert stored["status"] == "expired"
    assert stored["decided_at"]

    notify_mock.assert_awaited_once()
    kwargs = notify_mock.await_args.kwargs
    assert kwargs["kind"] == "proposal_missed"
    assert kwargs["title"] == "Missed trade proposal"
    assert kwargs["severity"] == "warning"
    assert kwargs["push"] is True
    data = kwargs["data"]
    assert data["type"] == "proposal_missed"
    assert data["proposal_id"] == row["id"]
    assert isinstance(data["qty"], str)
    assert data["expired_at"] == stored["expires_at"]
    assert data["symbol"] == "AAPL"
    assert data["side"] == "buy"
    assert data["summary"]


@pytest.mark.asyncio
async def test_sweeper_leaves_unexpired_pending_alone(
    monkeypatch, tmp_path: Path
) -> None:
    db = tmp_path / "sweep.db"
    notify_mock = AsyncMock(return_value={})
    _patch_passing_env(monkeypatch, db)
    monkeypatch.setattr("trading.proposals.notify", notify_mock)

    row = await create_phone_proposal(
        _order_args(), score=False, ttl_seconds=300, db_path=db
    )
    notify_mock.reset_mock()

    count = await sweep_expired_proposals(db_path=db)

    assert count == 0
    stored = paper_orders_mod.get_proposal(row["id"], db_path=db)
    assert stored is not None
    assert stored["status"] == "pending_phone"
    notify_mock.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("terminal_status", ["approving", "executed", "rejected"])
async def test_sweeper_race_guard_skips_non_pending(
    monkeypatch, tmp_path: Path, terminal_status: str
) -> None:
    db = tmp_path / "sweep.db"
    notify_mock = AsyncMock(return_value={})
    _patch_passing_env(monkeypatch, db)
    monkeypatch.setattr("trading.proposals.notify", notify_mock)
    journal_mock = AsyncMock()
    monkeypatch.setattr("trading.proposals._journal_decision", journal_mock)
    la_mock = AsyncMock()
    monkeypatch.setattr("trading.proposals._try_end_proposal_live_activity", la_mock)

    row = await create_phone_proposal(
        _order_args(), score=False, ttl_seconds=300, db_path=db
    )
    _expire(db, row["id"])
    _set_status(db, row["id"], terminal_status)
    notify_mock.reset_mock()

    count = await sweep_expired_proposals(db_path=db)

    assert count == 0
    notify_mock.assert_not_awaited()
    journal_mock.assert_not_called()
    la_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_sweeper_idempotent_second_pass(monkeypatch, tmp_path: Path) -> None:
    db = tmp_path / "sweep.db"
    notify_mock = AsyncMock(return_value={})
    _patch_passing_env(monkeypatch, db)
    monkeypatch.setattr("trading.proposals.notify", notify_mock)

    row = await create_phone_proposal(
        _order_args(), score=False, ttl_seconds=300, db_path=db
    )
    _expire(db, row["id"])
    notify_mock.reset_mock()

    first = await sweep_expired_proposals(db_path=db)
    second = await sweep_expired_proposals(db_path=db)

    assert first == 1
    assert second == 0
    assert notify_mock.await_count == 1


@pytest.mark.asyncio
async def test_sweeper_ends_live_activity(monkeypatch, tmp_path: Path) -> None:
    db = tmp_path / "sweep.db"
    _patch_passing_env(monkeypatch, db)
    la_mock = AsyncMock()
    monkeypatch.setattr("trading.proposals._try_end_proposal_live_activity", la_mock)

    row = await create_phone_proposal(
        _order_args(), score=False, ttl_seconds=300, db_path=db
    )
    _expire(db, row["id"])

    await sweep_expired_proposals(db_path=db)

    la_mock.assert_awaited_once()
    assert la_mock.await_args.kwargs["status"] == "expired"


@pytest.mark.asyncio
async def test_sweeper_survives_push_failure(monkeypatch, tmp_path: Path) -> None:
    db = tmp_path / "sweep.db"
    _patch_passing_env(monkeypatch, db)
    monkeypatch.setattr(
        "trading.proposals.notify",
        AsyncMock(side_effect=RuntimeError("push down")),
    )

    row = await create_phone_proposal(
        _order_args(), score=False, ttl_seconds=300, db_path=db
    )
    _expire(db, row["id"])

    count = await sweep_expired_proposals(db_path=db)

    assert count == 1
    stored = paper_orders_mod.get_proposal(row["id"], db_path=db)
    assert stored is not None
    assert stored["status"] == "expired"


@pytest.mark.asyncio
async def test_sweeper_journals_never_filled(monkeypatch, tmp_path: Path) -> None:
    db = tmp_path / "sweep.db"
    _patch_passing_env(monkeypatch, db)

    row = await create_phone_proposal(
        _order_args(), score=False, ttl_seconds=300, db_path=db
    )
    _expire(db, row["id"])

    await sweep_expired_proposals(db_path=db)

    journal = get_by_proposal(row["id"], db_path=db)
    assert journal["decision"] == "expired"
    assert journal["decided_by"] == "system"
    assert journal["outcome"] == "never_filled"


def test_missed_list_route_shape_and_filters(monkeypatch, tmp_path: Path) -> None:
    db = db_base.DEFAULT_DB_PATH
    monkeypatch.setattr(paper_orders_mod, "DEFAULT_DB_PATH", db)

    expired_row = paper_orders_mod.create_pending_proposal(
        proposal={"symbol": "AAPL", "side": "buy", "qty": 1, "order_type": "market"},
        intent_fields={"symbol": "AAPL", "side": "buy", "qty": 1, "order_type": "market"},
        session_id=None,
        ttl_seconds=60,
        summary="BUY 1 AAPL @ market",
        db_path=db,
    )
    _expire(db, expired_row["id"])
    paper_orders_mod.transition_proposal_status(
        expired_row["id"],
        expect="pending_phone",
        to="expired",
        db_path=db,
    )

    pending_row = paper_orders_mod.create_pending_proposal(
        proposal={"symbol": "MSFT", "side": "sell", "qty": 3, "order_type": "market"},
        intent_fields={"symbol": "MSFT", "side": "sell", "qty": 3, "order_type": "market"},
        session_id=None,
        ttl_seconds=300,
        summary="SELL 3 MSFT @ market",
        db_path=db,
    )

    executed_row = paper_orders_mod.create_pending_proposal(
        proposal={"symbol": "GOOG", "side": "buy", "qty": 1, "order_type": "market"},
        intent_fields={"symbol": "GOOG", "side": "buy", "qty": 1, "order_type": "market"},
        session_id=None,
        ttl_seconds=60,
        summary="BUY 1 GOOG @ market",
        db_path=db,
    )
    paper_orders_mod.transition_proposal_status(
        executed_row["id"],
        expect="pending_phone",
        to="executed",
        execution_id="exec_test",
        db_path=db,
    )

    older_ts = (datetime.now(UTC) - timedelta(hours=2)).isoformat()
    newer_ts = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    _set_created_at(db, expired_row["id"], older_ts)

    expired_newer = paper_orders_mod.create_pending_proposal(
        proposal={"symbol": "TSLA", "side": "buy", "qty": 1, "order_type": "market"},
        intent_fields={"symbol": "TSLA", "side": "buy", "qty": 1, "order_type": "market"},
        session_id=None,
        ttl_seconds=60,
        summary="BUY 1 TSLA @ market",
        db_path=db,
    )
    paper_orders_mod.transition_proposal_status(
        expired_newer["id"],
        expect="pending_phone",
        to="expired",
        db_path=db,
    )
    _set_created_at(db, expired_newer["id"], newer_ts)

    from api.main import app

    with TestClient(app, base_url="http://127.0.0.1") as client:
        missed = client.get("/api/proposals/missed")
        assert missed.status_code == 200
        body = missed.json()
        assert "proposals" in body
        ids = {p["id"] for p in body["proposals"]}
        assert expired_row["id"] in ids
        assert pending_row["id"] not in ids
        assert executed_row["id"] not in ids
        sample = next(p for p in body["proposals"] if p["id"] == expired_row["id"])
        assert sample["status"] == "expired"
        assert sample["symbol"] == "AAPL"
        assert "summary" in sample

        since = client.get("/api/proposals/missed", params={"since": older_ts})
        assert since.status_code == 200
        since_ids = {p["id"] for p in since.json()["proposals"]}
        assert expired_row["id"] not in since_ids
        assert expired_newer["id"] in since_ids

        limited = client.get("/api/proposals/missed", params={"limit": 1})
        assert limited.status_code == 200
        assert len(limited.json()["proposals"]) == 1

        # Literal "missed" must resolve here, not as /proposals/{proposal_id}.
        assert client.get("/api/proposals/missed").json().keys() == {"proposals"}
