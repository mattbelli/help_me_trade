"""Phase A sub-step 3 — account_id threaded through proposal → intent → execution."""

from __future__ import annotations

import sqlite3
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from brokers.alpaca import AlpacaAdapter
from brokers.base import Account, PlaceOrderResponse, Position
from config.settings import primary_broker
from db import order_executions as order_executions_mod
from db import paper_orders as paper_orders_mod
from db import sqlite as sqlite_mod
from db.accounts import (
    default_execution_account,
    get_default_account,
    list_accounts,
)
from db.multi_account import migrate_multi_account
from db.paper_orders import create_pending_proposal
from trading.execution_service import ExecutionService
from trading.models import OrderIntent
from trading.order_proposal import (
    build_order_proposal_from_args,
    order_intent_from_fields,
)
from trading.proposals import approve_proposal, create_phone_proposal


def _seed_schwab_accounts(db: Path, *, count: int = 1) -> list[str]:
    from db.base import connect

    labels = ["Individual", "Roth IRA", "Joint"][:count]
    now = datetime.now(UTC).isoformat()
    ids: list[str] = []
    with connect(db) as conn:
        for label in labels:
            account_id = f"acct_{uuid.uuid4().hex[:12]}"
            conn.execute(
                """
                INSERT INTO trade_accounts (id, label, broker, created_at)
                VALUES (?, ?, 'schwab', ?)
                """,
                (account_id, label, now),
            )
            ids.append(account_id)
        conn.commit()
    return ids


def _migrated_db(tmp_path: Path, *, schwab_count: int = 0) -> Path:
    db = tmp_path / "account_path.db"
    if schwab_count:
        from db.order_executions import init_execution_tables
        from db.paper_orders import init_paper_order_tables
        from db.sqlite import init_db
        from db.trades import init_trade_tables

        init_db(db)
        init_trade_tables(db)
        init_execution_tables(db)
        init_paper_order_tables(db)
        _seed_schwab_accounts(db, count=schwab_count)
    migrate_multi_account(db)
    return db


def _save_fresh_snapshot(db_path: Path, *, broker: str = "alpaca") -> None:
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
    sqlite_mod.save_snapshot(account, positions, [], broker=broker, db_path=db_path)


def _unmigrated_db(tmp_path: Path) -> Path:
    db = tmp_path / "legacy.db"
    from db.sqlite import init_db

    init_db(db)
    _save_fresh_snapshot(db)
    return db


def _patch_passing_env(monkeypatch, db_path: Path, *, execution_mode: str = "dry_run") -> None:
    from db import base as db_base

    monkeypatch.setattr(db_base, "DEFAULT_DB_PATH", db_path)
    monkeypatch.setattr(sqlite_mod, "DEFAULT_DB_PATH", db_path)
    monkeypatch.setattr(order_executions_mod, "DEFAULT_DB_PATH", db_path)
    monkeypatch.setattr(paper_orders_mod, "DEFAULT_DB_PATH", db_path)

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

    monkeypatch.setattr("trading.risk_engine.get_broker", lambda _name: stub)
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


def _base_intent_fields(**overrides) -> dict:
    base = {
        "symbol": "AAPL",
        "side": "buy",
        "qty": "2",
        "order_type": "market",
        "limit_price": None,
        "broker": "alpaca",
        "asset_class": "us_equity",
        "occ_symbol": None,
        "multiplier": "1",
    }
    base.update(overrides)
    return base


def test_order_intent_account_id_defaults_to_none() -> None:
    intent = OrderIntent(
        proposal_id="p1",
        session_id=None,
        symbol="AAPL",
        side="buy",
        qty=Decimal("1"),
        order_type="market",
        limit_price=None,
        broker="alpaca",
    )
    assert intent.account_id is None


def test_build_order_proposal_migrated_db_sets_default_account(
    tmp_path: Path,
) -> None:
    db = _migrated_db(tmp_path)
    _save_fresh_snapshot(db)
    default = default_execution_account(db_path=db)
    assert default is not None

    built = build_order_proposal_from_args(_order_args(), db_path=db)
    assert "error" not in built
    assert built["intent_fields"]["account_id"] == default["id"]
    assert built["intent_fields"]["broker"] == default["broker"]


def test_build_order_proposal_unmigrated_db_legacy_fallback(
    tmp_path: Path,
) -> None:
    db = _unmigrated_db(tmp_path)

    built = build_order_proposal_from_args(_order_args(), db_path=db)
    assert "error" not in built
    assert built["intent_fields"]["account_id"] is None
    assert built["intent_fields"]["broker"] == primary_broker()


def test_build_order_proposal_unknown_account(tmp_path: Path) -> None:
    db = _migrated_db(tmp_path)
    _save_fresh_snapshot(db)

    built = build_order_proposal_from_args(
        _order_args(account_id="acct_nonexistent"),
        db_path=db,
    )
    assert built["error"] == "unknown account 'acct_nonexistent'"


def test_order_intent_from_fields_legacy_resolves_default(
    tmp_path: Path,
) -> None:
    db = _migrated_db(tmp_path)
    default = default_execution_account(db_path=db)
    assert default is not None

    intent = order_intent_from_fields(
        _base_intent_fields(),
        proposal_id="p1",
        session_id=None,
        db_path=db,
    )
    assert intent.account_id == default["id"]


def test_order_intent_from_fields_preserves_explicit_account(
    tmp_path: Path,
) -> None:
    db = _migrated_db(tmp_path)
    explicit = "acct_explicit123"

    intent = order_intent_from_fields(
        _base_intent_fields(account_id=explicit),
        proposal_id="p1",
        session_id=None,
        db_path=db,
    )
    assert intent.account_id == explicit


def test_order_intent_from_fields_no_execution_account(
    tmp_path: Path,
) -> None:
    db = _unmigrated_db(tmp_path)

    intent = order_intent_from_fields(
        _base_intent_fields(),
        proposal_id="p1",
        session_id=None,
        db_path=db,
    )
    assert intent.account_id is None


@pytest.mark.asyncio
async def test_create_phone_proposal_persists_account_id(
    monkeypatch, tmp_path: Path
) -> None:
    db = _migrated_db(tmp_path)
    _save_fresh_snapshot(db)
    _patch_passing_env(monkeypatch, db)
    default = default_execution_account(db_path=db)
    assert default is not None

    row = await create_phone_proposal(_order_args(), ttl_seconds=300, db_path=db)

    conn = sqlite3.connect(db)
    stored_account_id = conn.execute(
        "SELECT account_id FROM paper_order_proposals WHERE id = ?",
        (row["id"],),
    ).fetchone()[0]
    conn.close()
    assert stored_account_id == default["id"]


@pytest.mark.asyncio
async def test_approve_read_only_account_blocked(
    monkeypatch, tmp_path: Path
) -> None:
    db = _migrated_db(tmp_path, schwab_count=1)
    _save_fresh_snapshot(db)
    _patch_passing_env(monkeypatch, db)
    schwab = list_accounts(broker="schwab", db_path=db)[0]

    intent_fields = _base_intent_fields(
        broker="schwab",
        account_id=schwab["id"],
    )
    row = create_pending_proposal(
        proposal={"symbol": "AAPL", "side": "buy", "qty": 2.0, "order_type": "market"},
        intent_fields=intent_fields,
        session_id=None,
        ttl_seconds=300,
        summary="BUY 2 AAPL @ market",
        account_id=schwab["id"],
        db_path=db,
    )

    submit_spy = AsyncMock()
    monkeypatch.setattr("trading.proposals.ExecutionService.submit", submit_spy)

    outcome = await approve_proposal(row["id"], db_path=db)

    assert outcome["status"] == "blocked"
    assert outcome["reason"] == "account not execution-capable"
    submit_spy.assert_not_called()
    stored = paper_orders_mod.get_proposal(row["id"], db_path=db)
    assert stored is not None
    assert stored["status"] == "blocked"


@pytest.mark.asyncio
async def test_approve_default_account_executes(
    monkeypatch, tmp_path: Path
) -> None:
    db = _migrated_db(tmp_path)
    _save_fresh_snapshot(db)
    _patch_passing_env(monkeypatch, db)

    row = await create_phone_proposal(_order_args(), ttl_seconds=300, db_path=db)
    submit_spy = AsyncMock(wraps=ExecutionService.submit)
    monkeypatch.setattr("trading.proposals.ExecutionService.submit", submit_spy)

    outcome = await approve_proposal(row["id"], db_path=db)

    assert outcome["status"] == "executed"
    assert submit_spy.await_count == 1
    intent = submit_spy.call_args[0][0]
    default = default_execution_account(db_path=db)
    assert default is not None
    assert intent.account_id == default["id"]


@pytest.mark.asyncio
async def test_submit_uses_get_broker_for_account_when_bound(
    monkeypatch, tmp_path: Path
) -> None:
    db = _migrated_db(tmp_path)
    _save_fresh_snapshot(db)
    _patch_passing_env(monkeypatch, db, execution_mode="paper")
    default = default_execution_account(db_path=db)
    assert default is not None

    stub = object.__new__(AlpacaAdapter)
    stub.symbol_exists = lambda _symbol: True  # type: ignore[method-assign]
    stub.aplace_order = AsyncMock(  # type: ignore[method-assign]
        return_value=PlaceOrderResponse(
            broker_order_id="broker-1",
            status="accepted",
            raw={"id": "broker-1", "status": "accepted"},
        )
    )

    account_calls: list[str] = []
    broker_calls: list[str] = []

    def fake_get_broker_for_account(account_id: str):
        account_calls.append(account_id)
        return stub

    def fake_get_broker(name: str | None = None):
        broker_calls.append(name or "")
        return stub

    monkeypatch.setattr(
        "trading.execution_service.get_broker_for_account",
        fake_get_broker_for_account,
    )
    monkeypatch.setattr("trading.execution_service.get_broker", fake_get_broker)

    intent = OrderIntent(
        proposal_id="p1",
        session_id=None,
        symbol="AAPL",
        side="buy",
        qty=Decimal("2"),
        order_type="market",
        limit_price=None,
        broker="alpaca",
        account_id=default["id"],
    )
    result = await ExecutionService.submit(intent, db_path=db)

    assert result.status == "submitted"
    assert account_calls == [default["id"]]
    assert broker_calls == []

    conn = sqlite3.connect(db)
    row = conn.execute(
        "SELECT account_id FROM order_executions WHERE id = ?",
        (result.execution_id,),
    ).fetchone()
    conn.close()
    assert row[0] == default["id"]


@pytest.mark.asyncio
async def test_submit_uses_get_broker_for_legacy_intent(
    monkeypatch, tmp_path: Path
) -> None:
    db = _unmigrated_db(tmp_path)
    _patch_passing_env(monkeypatch, db, execution_mode="paper")

    stub = object.__new__(AlpacaAdapter)
    stub.symbol_exists = lambda _symbol: True  # type: ignore[method-assign]
    stub.aplace_order = AsyncMock(  # type: ignore[method-assign]
        return_value=PlaceOrderResponse(
            broker_order_id="broker-legacy",
            status="accepted",
            raw={"id": "broker-legacy", "status": "accepted"},
        )
    )

    account_calls: list[str] = []
    broker_calls: list[str] = []

    def fake_get_broker_for_account(account_id: str):
        account_calls.append(account_id)
        return stub

    def fake_get_broker(name: str | None = None):
        broker_calls.append(name or "")
        return stub

    monkeypatch.setattr(
        "trading.execution_service.get_broker_for_account",
        fake_get_broker_for_account,
    )
    monkeypatch.setattr("trading.execution_service.get_broker", fake_get_broker)

    intent = OrderIntent(
        proposal_id="p1",
        session_id=None,
        symbol="AAPL",
        side="buy",
        qty=Decimal("2"),
        order_type="market",
        limit_price=None,
        broker="alpaca",
        account_id=None,
    )
    await ExecutionService.submit(intent, db_path=db)

    assert account_calls == []
    assert broker_calls == ["alpaca"]


@pytest.mark.asyncio
async def test_legacy_stored_proposal_without_account_id_executes(
    monkeypatch, tmp_path: Path
) -> None:
    db = _migrated_db(tmp_path)
    _save_fresh_snapshot(db)
    _patch_passing_env(monkeypatch, db)
    default = get_default_account("alpaca", db_path=db)
    assert default is not None

    intent_fields = _base_intent_fields()
    assert "account_id" not in intent_fields

    row = create_pending_proposal(
        proposal={"symbol": "AAPL", "side": "buy", "qty": 2.0, "order_type": "market"},
        intent_fields=intent_fields,
        session_id=None,
        ttl_seconds=300,
        summary="BUY 2 AAPL @ market",
        db_path=db,
    )

    outcome = await approve_proposal(row["id"], db_path=db)
    assert outcome["status"] == "executed"
