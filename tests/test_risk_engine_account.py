"""RiskEngine per-account hardening (Phase A sub-step 4)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from brokers.alpaca import AlpacaAdapter
from brokers.base import Account, Position
from config import settings as settings_mod
from db import order_executions as order_executions_mod
from db import sqlite as sqlite_mod
from db.accounts import get_default_account, list_accounts
from db.base import connect
from db.multi_account import migrate_multi_account
from trading.models import OrderIntent
from trading.risk_engine import RiskEngine


def _seed_schwab_accounts(db: Path, *, count: int = 1) -> list[str]:
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


def _migrated_db(tmp_path: Path, *, schwab_count: int = 1) -> Path:
    db = tmp_path / "risk_account.db"
    from db.order_executions import init_execution_tables
    from db.paper_orders import init_paper_order_tables
    from db.sqlite import init_db
    from db.trades import init_trade_tables

    init_db(db)
    init_trade_tables(db)
    init_execution_tables(db)
    init_paper_order_tables(db)
    if schwab_count:
        _seed_schwab_accounts(db, count=schwab_count)
    migrate_multi_account(db)
    return db


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


def _save_fresh_snapshot(
    db_path: Path,
    *,
    buying_power: float = 100_000.0,
    account_id: str | None = None,
) -> None:
    account = Account(
        id="acc1",
        status="ACTIVE",
        cash=50_000.0,
        buying_power=buying_power,
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
    # Key the snapshot to the default account, exactly as the pollers do
    # (api/main.py). An account-scoped read never falls back to an unkeyed run, so
    # an unkeyed fixture would misrepresent production as "no snapshot".
    if account_id is None:
        default = get_default_account("alpaca", db_path=db_path)
        account_id = default["id"] if default else None
    sqlite_mod.save_snapshot(
        account,
        positions,
        [],
        broker="alpaca",
        account_id=account_id,
        db_path=db_path,
    )


def _stub_alpaca(*, tradable: bool = True) -> AlpacaAdapter:
    stub = object.__new__(AlpacaAdapter)
    stub.symbol_exists = lambda _symbol: tradable  # type: ignore[method-assign]
    return stub


def _patch_db(monkeypatch, db_path: Path) -> None:
    from db import base as db_base

    monkeypatch.setattr(db_base, "DEFAULT_DB_PATH", db_path)
    monkeypatch.setattr(sqlite_mod, "DEFAULT_DB_PATH", db_path)
    monkeypatch.setattr(order_executions_mod, "DEFAULT_DB_PATH", db_path)


def _patch_passing_env(monkeypatch, db_path: Path) -> None:
    _patch_db(monkeypatch, db_path)
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


def _seed_execution(
    db: Path,
    *,
    execution_id: str,
    account_id: str | None = None,
    status: str = "submitted",
) -> None:
    order_executions_mod.create_execution_record(
        execution_id=execution_id,
        proposal_id=f"paper_{execution_id}",
        session_id=None,
        symbol="AAPL",
        side="buy",
        qty=Decimal("1"),
        order_type="market",
        limit_price=None,
        execution_mode="paper",
        status=status,
        broker="alpaca",
        account_id=account_id,
        db_path=db,
    )


def _insert_execution_capable_alpaca(db: Path) -> str:
    default = get_default_account("alpaca", db_path=db)
    assert default is not None
    account_id = f"acct_{uuid.uuid4().hex[:12]}"
    now = datetime.now(UTC).isoformat()
    with connect(db) as conn:
        conn.execute(
            """
            INSERT INTO trade_accounts (
                id, label, broker, created_at, connection_id,
                enabled, execution_capable, is_default
            ) VALUES (?, 'Second Alpaca', 'alpaca', ?, ?, 1, 1, 0)
            """,
            (account_id, now, default["connection_id"]),
        )
        conn.commit()
    # A real enabled + execution-capable account polls, so it has its own
    # snapshot. Without one, RiskEngine correctly blocks it for "no portfolio
    # snapshot" — an account-scoped read never borrows another account's.
    _save_fresh_snapshot(db, account_id=account_id)
    return account_id


@pytest.mark.asyncio
async def test_capability_blocks_read_only_account(monkeypatch, tmp_path: Path) -> None:
    db = _migrated_db(tmp_path, schwab_count=1)
    _patch_passing_env(monkeypatch, db)
    schwab = list_accounts(broker="schwab", db_path=db)[0]

    result = await RiskEngine.validate(
        _intent(account_id=schwab["id"], broker="schwab"),
        db_path=db,
    )
    assert result.passed is False
    assert result.blocked_reason == "account not execution-capable"
    assert "fail: account not execution-capable" in result.checks[
        "account_execution_capable"
    ]


@pytest.mark.asyncio
async def test_capability_blocks_disabled_account(monkeypatch, tmp_path: Path) -> None:
    db = _migrated_db(tmp_path, schwab_count=0)
    _patch_passing_env(monkeypatch, db)
    default = get_default_account("alpaca", db_path=db)
    assert default is not None

    disabled_id = f"acct_{uuid.uuid4().hex[:12]}"
    now = datetime.now(UTC).isoformat()
    with connect(db) as conn:
        conn.execute(
            """
            INSERT INTO trade_accounts (
                id, label, broker, created_at, connection_id,
                enabled, execution_capable, is_default
            ) VALUES (?, 'Disabled Alpaca', 'alpaca', ?, ?, 0, 1, 0)
            """,
            (disabled_id, now, default["connection_id"]),
        )
        conn.commit()

    result = await RiskEngine.validate(
        _intent(account_id=disabled_id),
        db_path=db,
    )
    assert result.passed is False
    assert result.blocked_reason == "account not execution-capable"


@pytest.mark.asyncio
async def test_capability_blocks_unknown_account(monkeypatch, tmp_path: Path) -> None:
    db = _migrated_db(tmp_path, schwab_count=0)
    _patch_passing_env(monkeypatch, db)

    result = await RiskEngine.validate(
        _intent(account_id="acct_nonexistent"),
        db_path=db,
    )
    assert result.passed is False
    assert result.blocked_reason == "account not execution-capable"


@pytest.mark.asyncio
async def test_capability_passes_default_account(monkeypatch, tmp_path: Path) -> None:
    db = _migrated_db(tmp_path, schwab_count=0)
    _patch_passing_env(monkeypatch, db)
    default = get_default_account("alpaca", db_path=db)
    assert default is not None

    result = await RiskEngine.validate(
        _intent(account_id=default["id"]),
        db_path=db,
    )
    assert result.passed is True
    assert result.checks["account_execution_capable"] == "pass"


@pytest.mark.asyncio
async def test_capability_skipped_when_account_id_none(
    monkeypatch, tmp_path: Path
) -> None:
    db = _migrated_db(tmp_path, schwab_count=0)
    _patch_passing_env(monkeypatch, db)

    result = await RiskEngine.validate(_intent(account_id=None), db_path=db)
    assert result.passed is True
    assert "account_execution_capable" not in result.checks


def test_count_executions_today_account_filter(tmp_path: Path) -> None:
    db = _migrated_db(tmp_path, schwab_count=0)
    default = get_default_account("alpaca", db_path=db)
    assert default is not None
    account_b = _insert_execution_capable_alpaca(db)

    for i in range(3):
        _seed_execution(db, execution_id=f"exec_a{i:02d}", account_id=default["id"])
    for i in range(2):
        _seed_execution(db, execution_id=f"exec_b{i:02d}", account_id=account_b)

    assert order_executions_mod.count_executions_today(
        account_id=default["id"], db_path=db
    ) == 3
    assert order_executions_mod.count_executions_today(account_id=account_b, db_path=db) == 2
    assert order_executions_mod.count_executions_today(db_path=db) == 5


@pytest.mark.asyncio
async def test_per_account_daily_cap_blocks_at_limit(
    monkeypatch, tmp_path: Path
) -> None:
    db = _migrated_db(tmp_path, schwab_count=0)
    _patch_passing_env(monkeypatch, db)
    default = get_default_account("alpaca", db_path=db)
    assert default is not None

    max_daily = 2
    monkeypatch.setattr(
        "trading.risk_engine.trading_config",
        lambda: {
            **settings_mod.DEFAULT_SETTINGS["trading"],
            "max_daily_orders": max_daily,
        },
    )
    _seed_execution(db, execution_id="exec_cap01", account_id=default["id"])
    _seed_execution(db, execution_id="exec_cap02", account_id=default["id"])

    real_count = order_executions_mod.count_executions_today

    def _count_with_global_room(
        *,
        account_id: str | None = None,
        exclude_statuses: frozenset[str] | None = None,
        db_path: Path = order_executions_mod.DEFAULT_DB_PATH,
    ) -> int:
        if account_id is None:
            return max_daily - 1
        return real_count(
            account_id=account_id,
            exclude_statuses=exclude_statuses,
            db_path=db_path,
        )

    monkeypatch.setattr("trading.risk_engine.count_executions_today", _count_with_global_room)

    result = await RiskEngine.validate(
        _intent(account_id=default["id"]),
        db_path=db,
    )
    assert result.passed is False
    assert result.blocked_reason == "account daily order limit reached"
    assert "fail: account daily order limit reached" in result.checks[
        "daily_orders_account"
    ]


@pytest.mark.asyncio
async def test_single_account_daily_cap_uses_global_message(
    monkeypatch, tmp_path: Path
) -> None:
    """Sole account: account_today == today_count — global check blocks first (unchanged)."""
    db = _migrated_db(tmp_path, schwab_count=0)
    _patch_passing_env(monkeypatch, db)
    default = get_default_account("alpaca", db_path=db)
    assert default is not None

    monkeypatch.setattr(
        "trading.risk_engine.trading_config",
        lambda: {
            **settings_mod.DEFAULT_SETTINGS["trading"],
            "max_daily_orders": 2,
        },
    )
    _seed_execution(db, execution_id="exec_cap01", account_id=default["id"])
    _seed_execution(db, execution_id="exec_cap02", account_id=default["id"])

    result = await RiskEngine.validate(
        _intent(account_id=default["id"]),
        db_path=db,
    )
    assert result.passed is False
    assert result.blocked_reason == "daily order limit reached"
    assert "daily_orders_account" not in result.checks


@pytest.mark.asyncio
async def test_per_account_daily_cap_isolates_accounts(
    monkeypatch, tmp_path: Path
) -> None:
    db = _migrated_db(tmp_path, schwab_count=0)
    _patch_passing_env(monkeypatch, db)
    default = get_default_account("alpaca", db_path=db)
    assert default is not None
    account_b = _insert_execution_capable_alpaca(db)

    monkeypatch.setattr(
        "trading.risk_engine.trading_config",
        lambda: {
            **settings_mod.DEFAULT_SETTINGS["trading"],
            "max_daily_orders": 3,
        },
    )
    _seed_execution(db, execution_id="exec_a01", account_id=default["id"])
    _seed_execution(db, execution_id="exec_a02", account_id=default["id"])

    passing = await RiskEngine.validate(
        _intent(account_id=account_b),
        db_path=db,
    )
    assert passing.passed is True
    assert passing.checks["daily_orders_account"] == "pass"


@pytest.mark.asyncio
async def test_global_daily_cap_blocks_before_per_account(
    monkeypatch, tmp_path: Path
) -> None:
    db = _migrated_db(tmp_path, schwab_count=0)
    _patch_passing_env(monkeypatch, db)
    default = get_default_account("alpaca", db_path=db)
    assert default is not None
    account_b = _insert_execution_capable_alpaca(db)

    monkeypatch.setattr(
        "trading.risk_engine.trading_config",
        lambda: {
            **settings_mod.DEFAULT_SETTINGS["trading"],
            "max_daily_orders": 2,
        },
    )
    _seed_execution(db, execution_id="exec_g01", account_id=default["id"])
    _seed_execution(db, execution_id="exec_g02", account_id=account_b)

    result = await RiskEngine.validate(
        _intent(account_id=account_b),
        db_path=db,
    )
    assert result.passed is False
    assert result.blocked_reason == "daily order limit reached"
    assert "daily_orders_account" not in result.checks
