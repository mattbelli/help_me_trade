"""Phone trade-approval proposal lifecycle — pending, approve, reject."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from brokers.alpaca import AlpacaAdapter
from brokers.base import Account, Position
from db import order_executions as order_executions_mod
from db import paper_orders as paper_orders_mod
from db import sqlite as sqlite_mod
from trading.execution_service import ExecutionService
from trading.order_proposal import build_order_proposal_from_args
from trading.proposals import (
    approve_proposal,
    create_phone_proposal,
    reject_proposal,
)


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
        "trading.execution_service.notify",
        AsyncMock(return_value={}),
    )
    monkeypatch.setattr(
        "trading.proposals.notify",
        AsyncMock(return_value={}),
    )


def _order_args(**overrides) -> dict:
    base = {"symbol": "AAPL", "side": "buy", "qty": 2, "order_type": "market"}
    base.update(overrides)
    return base


def _execution_count(db_path: Path, *, proposal_id: str | None = None) -> int:
    order_executions_mod.init_execution_tables(db_path)
    conn = sqlite3.connect(db_path)
    if proposal_id:
        row = conn.execute(
            "SELECT COUNT(*) AS cnt FROM order_executions WHERE proposal_id = ?",
            (proposal_id,),
        ).fetchone()
    else:
        row = conn.execute("SELECT COUNT(*) AS cnt FROM order_executions").fetchone()
    conn.close()
    return int(row[0])


@pytest.mark.asyncio
async def test_create_pending_proposal_not_executed(
    monkeypatch, tmp_path: Path
) -> None:
    db = tmp_path / "proposals.db"
    _patch_passing_env(monkeypatch, db)

    row = await create_phone_proposal(_order_args(), ttl_seconds=300, db_path=db)

    assert row["status"] == "pending_phone"
    assert row["expires_at"]
    assert row["payload_hash"]
    assert _execution_count(db) == 0


@pytest.mark.asyncio
async def test_approve_within_ttl_executes_once(monkeypatch, tmp_path: Path) -> None:
    db = tmp_path / "proposals.db"
    _patch_passing_env(monkeypatch, db)

    row = await create_phone_proposal(_order_args(), ttl_seconds=300, db_path=db)
    submit_spy = AsyncMock(wraps=ExecutionService.submit)
    monkeypatch.setattr("trading.proposals.ExecutionService.submit", submit_spy)

    outcome = await approve_proposal(row["id"], db_path=db)

    assert outcome["status"] == "executed"
    assert outcome["execution_id"]
    assert submit_spy.await_count == 1

    stored = paper_orders_mod.get_proposal(row["id"], db_path=db)
    assert stored is not None
    assert stored["status"] == "executed"
    assert stored["execution_id"] == outcome["execution_id"]
    assert _execution_count(db, proposal_id=row["id"]) == 1


@pytest.mark.asyncio
async def test_double_approve_executes_once(monkeypatch, tmp_path: Path) -> None:
    db = tmp_path / "proposals.db"
    _patch_passing_env(monkeypatch, db)

    row = await create_phone_proposal(_order_args(), ttl_seconds=300, db_path=db)
    submit_spy = AsyncMock(wraps=ExecutionService.submit)
    monkeypatch.setattr("trading.proposals.ExecutionService.submit", submit_spy)

    first = await approve_proposal(row["id"], db_path=db)
    second = await approve_proposal(row["id"], db_path=db)

    assert first["status"] == "executed"
    assert second["status"] == "already_decided"
    assert submit_spy.await_count == 1
    assert _execution_count(db, proposal_id=row["id"]) == 1


@pytest.mark.asyncio
async def test_concurrent_approve_executes_once(monkeypatch, tmp_path: Path) -> None:
    import asyncio

    db = tmp_path / "proposals.db"
    _patch_passing_env(monkeypatch, db)

    row = await create_phone_proposal(_order_args(), ttl_seconds=300, db_path=db)

    original_submit = ExecutionService.submit
    calls: list = []

    async def _slow_submit(intent, *, db_path):
        # Hold inside submit so the second approval races the claim concurrently.
        calls.append(intent)
        await asyncio.sleep(0.05)
        return await original_submit(intent, db_path=db_path)

    monkeypatch.setattr("trading.proposals.ExecutionService.submit", _slow_submit)

    results = await asyncio.gather(
        approve_proposal(row["id"], db_path=db),
        approve_proposal(row["id"], db_path=db),
    )

    statuses = sorted(r["status"] for r in results)
    assert statuses == ["already_decided", "executed"]
    assert len(calls) == 1
    assert _execution_count(db, proposal_id=row["id"]) == 1


@pytest.mark.asyncio
async def test_malformed_expiry_fails_safe(monkeypatch, tmp_path: Path) -> None:
    db = tmp_path / "proposals.db"
    _patch_passing_env(monkeypatch, db)

    row = await create_phone_proposal(_order_args(), ttl_seconds=300, db_path=db)
    # A blanked/corrupted TTL must be treated as expired — never executed.
    conn = sqlite3.connect(db)
    conn.execute(
        "UPDATE paper_order_proposals SET expires_at = '' WHERE id = ?", (row["id"],)
    )
    conn.commit()
    conn.close()

    outcome = await approve_proposal(row["id"], db_path=db)

    assert outcome["status"] == "expired"
    assert _execution_count(db, proposal_id=row["id"]) == 0


@pytest.mark.asyncio
async def test_approve_after_ttl_expired(monkeypatch, tmp_path: Path) -> None:
    db = tmp_path / "proposals.db"
    _patch_passing_env(monkeypatch, db)

    row = await create_phone_proposal(_order_args(), ttl_seconds=1, db_path=db)
    expired_at = (datetime.now(UTC) - timedelta(seconds=5)).isoformat()
    conn = sqlite3.connect(db)
    conn.execute(
        "UPDATE paper_order_proposals SET expires_at = ? WHERE id = ?",
        (expired_at, row["id"]),
    )
    conn.commit()
    conn.close()

    outcome = await approve_proposal(row["id"], db_path=db)

    assert outcome["status"] == "expired"
    stored = paper_orders_mod.get_proposal(row["id"], db_path=db)
    assert stored is not None
    assert stored["status"] == "expired"
    assert _execution_count(db, proposal_id=row["id"]) == 0


@pytest.mark.asyncio
async def test_approve_hash_mismatch(monkeypatch, tmp_path: Path) -> None:
    db = tmp_path / "proposals.db"
    _patch_passing_env(monkeypatch, db)

    row = await create_phone_proposal(_order_args(), ttl_seconds=300, db_path=db)
    outcome = await approve_proposal(
        row["id"], expected_hash="deadbeef", db_path=db
    )

    assert outcome["status"] == "hash_mismatch"
    stored = paper_orders_mod.get_proposal(row["id"], db_path=db)
    assert stored is not None
    assert stored["status"] == "pending_phone"
    assert _execution_count(db, proposal_id=row["id"]) == 0


@pytest.mark.asyncio
async def test_reject_then_approve_already_decided(
    monkeypatch, tmp_path: Path
) -> None:
    db = tmp_path / "proposals.db"
    _patch_passing_env(monkeypatch, db)

    row = await create_phone_proposal(_order_args(), ttl_seconds=300, db_path=db)
    rejected = await reject_proposal(row["id"], db_path=db)
    assert rejected["status"] == "rejected"

    outcome = await approve_proposal(row["id"], db_path=db)
    assert outcome["status"] == "already_decided"
    assert _execution_count(db, proposal_id=row["id"]) == 0


@pytest.mark.asyncio
async def test_approve_kill_switch_blocks(monkeypatch, tmp_path: Path) -> None:
    db = tmp_path / "proposals.db"
    _patch_passing_env(monkeypatch, db)
    monkeypatch.setattr("trading.execution_service.is_kill_switch_active", lambda: True)

    row = await create_phone_proposal(_order_args(), ttl_seconds=300, db_path=db)
    outcome = await approve_proposal(row["id"], db_path=db)

    assert outcome["status"] == "blocked"
    stored = paper_orders_mod.get_proposal(row["id"], db_path=db)
    assert stored is not None
    assert stored["status"] == "blocked"
    exec_row = order_executions_mod.get_execution(
        stored.get("execution_id") or "", db_path=db
    )
    if exec_row is None:
        conn = sqlite3.connect(db)
        row = conn.execute(
            "SELECT status FROM order_executions WHERE proposal_id = ?",
            (stored["id"],),
        ).fetchone()
        conn.close()
        assert row is not None
        assert row[0] == "rejected"
    else:
        assert exec_row["status"] == "rejected"


@pytest.mark.asyncio
async def test_rebuild_uses_decimal_not_float(monkeypatch, tmp_path: Path) -> None:
    db = tmp_path / "proposals.db"
    _patch_passing_env(monkeypatch, db)

    built = build_order_proposal_from_args(
        _order_args(qty="2.5"), db_path=db
    )
    assert "error" not in built
    assert built["intent_fields"]["qty"] == "2.5"

    row = await create_phone_proposal(
        _order_args(qty="2.5"), ttl_seconds=300, db_path=db
    )
    captured: list = []
    original_submit = ExecutionService.submit

    async def _capture(intent, *, db_path):
        captured.append(intent)
        return await original_submit(intent, db_path=db_path)

    monkeypatch.setattr("trading.proposals.ExecutionService.submit", _capture)
    await approve_proposal(row["id"], db_path=db)

    assert len(captured) == 1
    assert isinstance(captured[0].qty, Decimal)
    assert captured[0].qty == Decimal("2.5")
