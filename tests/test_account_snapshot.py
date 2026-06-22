"""Per-account snapshot writes and account-keyed reads (Phase A sub-step 5)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from brokers.alpaca import AlpacaAdapter
from brokers.base import Account, Position
from db import order_executions as order_executions_mod
from db import sqlite as sqlite_mod
from db.accounts import get_default_account
from db.base import connect
from db.multi_account import migrate_multi_account
from trading.models import OrderIntent
from trading.risk_engine import RiskEngine


def _account(*, cash: float = 10_000.0, account_id: str = "broker-acct-1") -> Account:
    return Account(
        id=account_id,
        status="ACTIVE",
        cash=cash,
        buying_power=cash,
        portfolio_value=cash * 2,
    )


def _init_db(tmp_path: Path) -> Path:
    db = tmp_path / "account_snapshot.db"
    sqlite_mod.init_db(db)
    from db.order_executions import init_execution_tables
    from db.paper_orders import init_paper_order_tables
    from db.trades import init_trade_tables

    init_trade_tables(db)
    init_execution_tables(db)
    init_paper_order_tables(db)
    migrate_multi_account(db)
    return db


def _poll_run_account_id(db: Path) -> str | None:
    with connect(db) as conn:
        row = conn.execute(
            "SELECT account_id FROM poll_runs ORDER BY id DESC LIMIT 1"
        ).fetchone()
    return row["account_id"] if row else None


def test_save_snapshot_stamps_account_id(tmp_path: Path) -> None:
    db = tmp_path / "save.db"
    sqlite_mod.init_db(db)

    sqlite_mod.save_snapshot(
        _account(),
        [],
        [],
        broker="alpaca",
        account_id="acct_x",
        db_path=db,
    )
    assert _poll_run_account_id(db) == "acct_x"

    sqlite_mod.save_snapshot(_account(), [], [], broker="alpaca", db_path=db)
    assert _poll_run_account_id(db) is None


def test_get_latest_snapshot_account_keyed(tmp_path: Path) -> None:
    db = tmp_path / "keyed.db"
    sqlite_mod.init_db(db)
    account_a = "acct_a"
    account_b = "acct_b"

    sqlite_mod.save_snapshot(
        _account(cash=1_000.0),
        [],
        [],
        broker="alpaca",
        account_id=account_a,
        db_path=db,
    )
    sqlite_mod.save_snapshot(
        _account(cash=2_000.0),
        [],
        [],
        broker="alpaca",
        account_id=account_b,
        db_path=db,
    )

    snap_a = sqlite_mod.get_latest_snapshot("alpaca", account_id=account_a, db_path=db)
    snap_b = sqlite_mod.get_latest_snapshot("alpaca", account_id=account_b, db_path=db)
    snap_broker = sqlite_mod.get_latest_snapshot("alpaca", db_path=db)

    assert snap_a is not None
    assert snap_b is not None
    assert snap_broker is not None
    assert snap_a["account"]["cash"] == 1_000.0
    assert snap_b["account"]["cash"] == 2_000.0
    assert snap_broker["account"]["cash"] == 2_000.0


def test_get_latest_snapshot_account_fallback(tmp_path: Path) -> None:
    db = tmp_path / "fallback.db"
    sqlite_mod.init_db(db)

    sqlite_mod.save_snapshot(
        _account(cash=5_000.0),
        [],
        [],
        broker="alpaca",
        db_path=db,
    )

    snapshot = sqlite_mod.get_latest_snapshot(
        "alpaca", account_id="never-saved", db_path=db
    )
    assert snapshot is not None
    assert snapshot["account"]["cash"] == 5_000.0


@pytest.mark.asyncio
async def test_poll_stamps_default_account(monkeypatch, tmp_path: Path) -> None:
    from api import main
    from db import accounts as accounts_mod
    from db import base as db_base

    db = _init_db(tmp_path)
    default = get_default_account("alpaca", db_path=db)
    assert default is not None

    monkeypatch.setattr(db_base, "DEFAULT_DB_PATH", db)
    monkeypatch.setattr(accounts_mod, "DEFAULT_DB_PATH", db)
    monkeypatch.setattr(sqlite_mod, "DEFAULT_DB_PATH", db)
    monkeypatch.setattr(
        main,
        "get_default_account",
        lambda broker="alpaca": get_default_account(broker, db_path=db),
    )

    mock_broker = MagicMock()
    mock_broker.afetch_snapshot = AsyncMock(
        return_value=(_account(account_id="broker-acct-1"), [], [])
    )

    def save_with_test_db(*args, **kwargs):
        kwargs["db_path"] = db
        return sqlite_mod.save_snapshot(*args, **kwargs)

    with (
        patch.object(main, "broker_is_configured", return_value=True),
        patch.object(main, "get_broker", return_value=mock_broker),
        patch.object(main, "save_snapshot", side_effect=save_with_test_db),
        patch.object(main, "compute_and_save_snapshot_analytics"),
        patch.object(main, "count_pending_paper_executions", return_value=0),
        patch.object(main, "count_submitted_executions", return_value=0),
        patch.object(main, "get_latest_snapshot", return_value=None),
    ):
        await main._poll_broker("alpaca")

    assert _poll_run_account_id(db) == default["id"]


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


def _stub_alpaca(*, tradable: bool = True) -> AlpacaAdapter:
    stub = object.__new__(AlpacaAdapter)
    stub.symbol_exists = lambda _symbol: tradable  # type: ignore[method-assign]
    return stub


def _patch_risk_env(monkeypatch, db_path: Path) -> None:
    from db import base as db_base

    monkeypatch.setattr(db_base, "DEFAULT_DB_PATH", db_path)
    monkeypatch.setattr(sqlite_mod, "DEFAULT_DB_PATH", db_path)
    monkeypatch.setattr(order_executions_mod, "DEFAULT_DB_PATH", db_path)
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


def _save_account_snapshot(
    db_path: Path,
    *,
    account_id: str,
    buying_power: float,
) -> None:
    account = Account(
        id="broker-acct-1",
        status="ACTIVE",
        cash=buying_power / 2,
        buying_power=buying_power,
        portfolio_value=buying_power,
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
    sqlite_mod.save_snapshot(
        account,
        positions,
        [],
        broker="alpaca",
        account_id=account_id,
        db_path=db_path,
    )


@pytest.mark.asyncio
async def test_risk_engine_reads_account_snapshot(
    monkeypatch, tmp_path: Path
) -> None:
    db = _init_db(tmp_path)
    default = get_default_account("alpaca", db_path=db)
    assert default is not None

    second_account = f"acct_{uuid.uuid4().hex[:12]}"
    now = datetime.now(UTC).isoformat()
    with connect(db) as conn:
        conn.execute(
            """
            INSERT INTO trade_accounts (
                id, label, broker, created_at, connection_id,
                enabled, execution_capable, is_default
            ) VALUES (?, 'Second Alpaca', 'alpaca', ?, ?, 1, 1, 0)
            """,
            (second_account, now, default["connection_id"]),
        )
        conn.commit()

    _save_account_snapshot(db, account_id=default["id"], buying_power=100_000.0)
    _save_account_snapshot(db, account_id=second_account, buying_power=500.0)

    _patch_risk_env(monkeypatch, db)

    default_result = await RiskEngine.validate(
        _intent(account_id=default["id"], qty=Decimal("5")),
        db_path=db,
    )
    assert default_result.passed is True

    capped_result = await RiskEngine.validate(
        _intent(account_id=second_account, qty=Decimal("5")),
        db_path=db,
    )
    assert capped_result.passed is False
    assert capped_result.blocked_reason == "insufficient buying power"


@pytest.mark.asyncio
async def test_risk_engine_account_id_none_uses_broker_path(
    monkeypatch, tmp_path: Path
) -> None:
    db = tmp_path / "legacy.db"
    sqlite_mod.init_db(db)
    _save_account_snapshot(db, account_id="legacy-unstamped", buying_power=100_000.0)
    _patch_risk_env(monkeypatch, db)

    result = await RiskEngine.validate(_intent(account_id=None), db_path=db)
    assert result.passed is True
