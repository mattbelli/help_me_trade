"""Agent trade proposal intake — propose_trade gate chain and scan-fire producer."""

from __future__ import annotations

import sqlite3
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from brokers.alpaca import AlpacaAdapter
from brokers.base import Account, Position
from db import order_executions as order_executions_mod
from db import paper_orders as paper_orders_mod
from db import sqlite as sqlite_mod
from db.accounts import default_execution_account
from db.ai_tables import create_rule, init_ai_tables
from db.multi_account import migrate_multi_account
from signals.rule_schema import Action
from trading.market_hours import is_market_open
from trading.order_proposal import build_order_proposal_from_args
from trading.proposal_intake import (
    AGENT_PROPOSAL_TTL_SECONDS,
    TradeCandidate,
    auto_execute_entry,
    propose_from_scan_fire,
    propose_trade,
)
from trading.proposals import create_phone_proposal

_ET = ZoneInfo("America/New_York")
_WED_10AM = datetime(2025, 6, 18, 10, 0, tzinfo=_ET)
_WED_8AM = datetime(2025, 6, 18, 8, 0, tzinfo=_ET)
_WED_430PM = datetime(2025, 6, 18, 16, 30, tzinfo=_ET)
_SAT_10AM = datetime(2025, 6, 21, 10, 0, tzinfo=_ET)


def _stub_alpaca(*, tradable: bool = True) -> AlpacaAdapter:
    stub = object.__new__(AlpacaAdapter)
    stub.symbol_exists = lambda _symbol: tradable  # type: ignore[method-assign]
    return stub


def _patch_env(
    monkeypatch,
    db_path: Path,
    *,
    price: float = 150.0,
    symbol: str = "AAPL",
    buying_power: float = 100_000.0,
    agent_enabled: bool = True,
    kill_switch: bool = False,
) -> None:
    from db import base as db_base

    monkeypatch.setattr(db_base, "DEFAULT_DB_PATH", db_path)
    monkeypatch.setattr(sqlite_mod, "DEFAULT_DB_PATH", db_path)
    monkeypatch.setattr(order_executions_mod, "DEFAULT_DB_PATH", db_path)
    monkeypatch.setattr(paper_orders_mod, "DEFAULT_DB_PATH", db_path)

    account = Account(
        id="acc1",
        status="ACTIVE",
        cash=50_000.0,
        buying_power=buying_power,
        portfolio_value=100_000.0,
    )
    positions = [
        Position(
            symbol=symbol,
            qty=10.0,
            side="long",
            market_value=1_500.0,
            unrealized_pl=50.0,
            avg_entry_price=145.0,
            current_price=price,
        )
    ]
    sqlite_mod.save_snapshot(account, positions, [], broker="alpaca", db_path=db_path)

    trading_cfg = {
        "execution_mode": "dry_run",
        "kill_switch_active": kill_switch,
        "agent_proposals_enabled": agent_enabled,
        "max_pending_agent_proposals": 3,
        "max_order_value_usd": 50_000,
        "max_position_pct": 30,
        "max_open_orders": 10,
        "max_daily_orders": 20,
    }

    monkeypatch.setattr(
        "trading.risk_engine.get_broker",
        lambda _name: _stub_alpaca(),
    )
    monkeypatch.setattr(
        "trading.risk_engine.market_hub.quote_snapshot",
        lambda _symbols: {symbol: {"price": price}},
    )
    monkeypatch.setattr("trading.risk_engine.is_kill_switch_active", lambda: kill_switch)
    monkeypatch.setattr("trading.risk_engine.trading_config", lambda: trading_cfg)
    monkeypatch.setattr("trading.proposal_intake.is_kill_switch_active", lambda: kill_switch)
    monkeypatch.setattr("trading.proposal_intake.trading_config", lambda: trading_cfg)
    monkeypatch.setattr("trading.proposal_intake.is_market_open", lambda: True)
    monkeypatch.setattr(
        "trading.proposals.notify",
        AsyncMock(),
    )


def _candidate(**overrides) -> TradeCandidate:
    base = {
        "symbol": "AAPL",
        "side": "buy",
        "notional_usd": Decimal("300"),
        "source": "agent_scan",
        "rule_id": "rule_abc",
    }
    base.update(overrides)
    return TradeCandidate(**base)


def _proposal_count(db_path: Path) -> int:
    paper_orders_mod.init_paper_order_tables(db_path)
    conn = sqlite3.connect(db_path)
    row = conn.execute(
        "SELECT COUNT(*) FROM paper_order_proposals WHERE status = 'pending_phone'"
    ).fetchone()
    conn.close()
    return int(row[0])


def _execution_count(db_path: Path) -> int:
    order_executions_mod.init_execution_tables(db_path)
    conn = sqlite3.connect(db_path)
    row = conn.execute("SELECT COUNT(*) FROM order_executions").fetchone()
    conn.close()
    return int(row[0])


def test_is_market_open_regular_session() -> None:
    assert is_market_open(_WED_10AM) is True


def test_is_market_open_weekend() -> None:
    assert is_market_open(_SAT_10AM) is False


def test_is_market_open_before_open() -> None:
    assert is_market_open(_WED_8AM) is False


def test_is_market_open_after_close() -> None:
    assert is_market_open(_WED_430PM) is False


@pytest.mark.asyncio
async def test_propose_trade_happy_path(monkeypatch, tmp_path: Path) -> None:
    db = tmp_path / "intake.db"
    _patch_env(monkeypatch, db)

    result = await propose_trade(_candidate(), db_path=db)

    assert result["status"] == "proposed"
    assert result["id"]
    assert _proposal_count(db) == 1
    assert _execution_count(db) == 0

    row = paper_orders_mod.get_proposal(result["id"], db_path=db)
    assert row is not None
    assert row["source"] == "agent_scan"
    assert row["rule_id"] == "rule_abc"
    assert row["status"] == "pending_phone"
    assert int(row["qty"]) == 2


@pytest.mark.asyncio
async def test_propose_trade_market_closed(monkeypatch, tmp_path: Path) -> None:
    db = tmp_path / "intake.db"
    _patch_env(monkeypatch, db)
    monkeypatch.setattr("trading.proposal_intake.is_market_open", lambda: False)

    result = await propose_trade(_candidate(), db_path=db)

    assert result == {"status": "market_closed"}
    assert _proposal_count(db) == 0
    assert _execution_count(db) == 0


@pytest.mark.asyncio
async def test_propose_trade_kill_switch(monkeypatch, tmp_path: Path) -> None:
    db = tmp_path / "intake.db"
    _patch_env(monkeypatch, db, kill_switch=True)

    result = await propose_trade(_candidate(), db_path=db)

    assert result == {"status": "kill_switch"}
    assert _proposal_count(db) == 0
    assert _execution_count(db) == 0


@pytest.mark.asyncio
async def test_propose_trade_disabled(monkeypatch, tmp_path: Path) -> None:
    db = tmp_path / "intake.db"
    _patch_env(monkeypatch, db, agent_enabled=False)

    result = await propose_trade(_candidate(), db_path=db)

    assert result == {"status": "disabled"}
    assert _proposal_count(db) == 0
    assert _execution_count(db) == 0


@pytest.mark.asyncio
async def test_propose_trade_duplicate(monkeypatch, tmp_path: Path) -> None:
    db = tmp_path / "intake.db"
    _patch_env(monkeypatch, db)

    built = build_order_proposal_from_args(
        {"symbol": "AAPL", "side": "buy", "qty": 1, "order_type": "market"},
        db_path=db,
    )
    paper_orders_mod.create_pending_proposal(
        proposal=built["proposal"],
        intent_fields=built["intent_fields"],
        session_id=None,
        ttl_seconds=300,
        summary="existing",
        db_path=db,
    )

    result = await propose_trade(_candidate(), db_path=db)

    assert result == {"status": "duplicate"}
    assert _proposal_count(db) == 1
    assert _execution_count(db) == 0


@pytest.mark.asyncio
async def test_propose_trade_no_price(monkeypatch, tmp_path: Path) -> None:
    db = tmp_path / "intake.db"
    _patch_env(monkeypatch, db)
    async def _no_price(_symbol):
        return None

    monkeypatch.setattr("trading.proposal_intake._live_market_price", _no_price)

    result = await propose_trade(_candidate(), db_path=db)

    assert result == {"status": "no_price"}
    assert _proposal_count(db) == 0
    assert _execution_count(db) == 0


@pytest.mark.asyncio
async def test_propose_trade_too_small(monkeypatch, tmp_path: Path) -> None:
    db = tmp_path / "intake.db"
    _patch_env(monkeypatch, db, price=150.0)

    result = await propose_trade(
        _candidate(notional_usd=Decimal("100")),
        db_path=db,
    )

    assert result == {"status": "too_small"}
    assert _proposal_count(db) == 0
    assert _execution_count(db) == 0


@pytest.mark.asyncio
async def test_propose_trade_risk_blocked(monkeypatch, tmp_path: Path) -> None:
    db = tmp_path / "intake.db"
    _patch_env(monkeypatch, db)

    async def _fail_validate(_intent, *, db_path):
        from trading.models import RiskCheckResult

        return RiskCheckResult(
            passed=False,
            checks={"mock": "fail"},
            blocked_reason="mock block",
        )

    monkeypatch.setattr("trading.proposal_intake.RiskEngine.validate", _fail_validate)

    result = await propose_trade(_candidate(), db_path=db)

    assert result == {"status": "risk_blocked", "reason": "mock block"}
    assert _proposal_count(db) == 0
    assert _execution_count(db) == 0


@pytest.mark.asyncio
async def test_propose_trade_sizing_notional_and_cap(monkeypatch, tmp_path: Path) -> None:
    db = tmp_path / "intake.db"
    _patch_env(monkeypatch, db, price=150.0, buying_power=200.0)

    result = await propose_trade(
        _candidate(notional_usd=Decimal("500")),
        db_path=db,
    )

    assert result["status"] == "proposed"
    row = paper_orders_mod.get_proposal(result["id"], db_path=db)
    assert row is not None
    assert int(row["qty"]) == 1


@pytest.mark.asyncio
async def test_propose_trade_sizing_notional_only(monkeypatch, tmp_path: Path) -> None:
    db = tmp_path / "intake.db"
    _patch_env(monkeypatch, db, price=150.0, buying_power=100_000.0)

    result = await propose_trade(
        _candidate(notional_usd=Decimal("450")),
        db_path=db,
    )

    assert result["status"] == "proposed"
    row = paper_orders_mod.get_proposal(result["id"], db_path=db)
    assert row is not None
    assert int(row["qty"]) == 3


@pytest.mark.asyncio
async def test_propose_from_scan_fire_trade_action(monkeypatch, tmp_path: Path) -> None:
    db = tmp_path / "intake.db"
    _patch_env(monkeypatch, db)

    calls: list[TradeCandidate] = []

    async def _spy_propose(candidate, *, db_path):
        calls.append(candidate)
        return {"status": "proposed", "id": "paper_test"}

    monkeypatch.setattr("trading.proposal_intake.propose_trade", _spy_propose)

    item = {
        "rule_id": "r1",
        "rule_name": "Buy dip",
        "action": {
            "type": "trade",
            "symbol": "AAPL",
            "side": "buy",
            "notional_usd": 300,
            "order_type": "market",
        },
    }
    result = await propose_from_scan_fire(item, db_path=db)

    assert result == {"status": "proposed", "id": "paper_test"}
    assert len(calls) == 1
    c = calls[0]
    assert c.symbol == "AAPL"
    assert c.side == "buy"
    assert c.notional_usd == Decimal("300")
    assert c.source == "agent_scan"
    assert c.rule_id == "r1"
    assert c.rationale == "Buy dip"


@pytest.mark.asyncio
async def test_propose_from_scan_fire_alert_returns_none(
    monkeypatch, tmp_path: Path
) -> None:
    db = tmp_path / "intake.db"
    _patch_env(monkeypatch, db)

    spy = AsyncMock()
    monkeypatch.setattr("trading.proposal_intake.propose_trade", spy)

    item = {
        "rule_id": "r2",
        "rule_name": "Alert only",
        "action": {"type": "alert", "message": "hello"},
    }
    result = await propose_from_scan_fire(item, db_path=db)

    assert result is None
    spy.assert_not_called()


@pytest.mark.asyncio
async def test_agent_proposal_ttl(monkeypatch, tmp_path: Path) -> None:
    db = tmp_path / "intake.db"
    _patch_env(monkeypatch, db)

    captured: dict = {}

    async def _capture_create(args, **kwargs):
        captured.update(kwargs)
        return await create_phone_proposal(args, **kwargs)

    monkeypatch.setattr("trading.proposal_intake.create_phone_proposal", _capture_create)

    await propose_trade(_candidate(), db_path=db)

    assert captured.get("ttl_seconds") == AGENT_PROPOSAL_TTL_SECONDS
    assert captured.get("source") == "agent_scan"
    assert captured.get("rule_id") == "rule_abc"


def _insert_pending_proposal(db_path: Path, symbol: str) -> None:
    built = build_order_proposal_from_args(
        {"symbol": symbol, "side": "buy", "qty": 1, "order_type": "market"},
        db_path=db_path,
    )
    paper_orders_mod.create_pending_proposal(
        proposal=built["proposal"],
        intent_fields=built["intent_fields"],
        session_id=None,
        ttl_seconds=300,
        summary=f"existing {symbol}",
        db_path=db_path,
    )


@pytest.mark.asyncio
async def test_propose_trade_max_pending(monkeypatch, tmp_path: Path) -> None:
    db = tmp_path / "intake.db"
    _patch_env(monkeypatch, db)
    # propose_trade sizes (incl. the live-price lookup) before the max_pending check, so give
    # NVDA a price — otherwise this short-circuits at no_price (was relying on a prior test
    # warming the quote cache → order-dependent / fails in isolation / hits live Alpaca in CI).
    monkeypatch.setattr(
        "trading.proposal_intake._live_market_price",
        AsyncMock(return_value=Decimal("150")),
    )
    max_pending = 3
    for sym in ("MSFT", "GOOG", "META"):
        _insert_pending_proposal(db, sym)

    result = await propose_trade(_candidate(symbol="NVDA"), db_path=db)

    assert result == {"status": "max_pending"}
    assert _proposal_count(db) == max_pending
    assert _execution_count(db) == 0


@pytest.mark.asyncio
async def test_propose_from_scan_fire_negative_notional(
    monkeypatch, tmp_path: Path
) -> None:
    db = tmp_path / "intake.db"
    _patch_env(monkeypatch, db)

    spy = AsyncMock()
    monkeypatch.setattr("trading.proposal_intake.propose_trade", spy)

    item = {
        "rule_id": "r_neg",
        "rule_name": "Bad notional",
        "action": {
            "type": "trade",
            "symbol": "AAPL",
            "side": "buy",
            "notional_usd": -100,
        },
    }
    result = await propose_from_scan_fire(item, db_path=db)

    assert result is None
    spy.assert_not_called()
    assert _proposal_count(db) == 0
    assert _execution_count(db) == 0


@pytest.mark.asyncio
async def test_propose_from_scan_fire_invalid_limit_price(
    monkeypatch, tmp_path: Path
) -> None:
    db = tmp_path / "intake.db"
    _patch_env(monkeypatch, db)

    spy = AsyncMock()
    monkeypatch.setattr("trading.proposal_intake.propose_trade", spy)

    item = {
        "rule_id": "r_bad_limit",
        "rule_name": "Bad limit",
        "action": {
            "type": "trade",
            "symbol": "AAPL",
            "side": "buy",
            "notional_usd": 300,
            "limit_price": "not_a_number",
        },
    }
    result = await propose_from_scan_fire(item, db_path=db)

    assert result is None
    spy.assert_not_called()
    assert _proposal_count(db) == 0
    assert _execution_count(db) == 0


def test_create_rule_trade_forced_disabled(tmp_path: Path) -> None:
    db = tmp_path / "rules.db"
    init_ai_tables(db)
    rule = create_rule(
        rule_id="trade_rule_1",
        name="Auto buy",
        conditions={"fact": "AAPL_price", "operator": "greaterThan", "value": 0},
        action={
            "type": "trade",
            "symbol": "AAPL",
            "side": "buy",
            "notional_usd": 500,
        },
        enabled=True,
        db_path=db,
    )
    assert rule["enabled"] is False


def test_action_trade_requires_fields() -> None:
    with pytest.raises(ValidationError, match="symbol, side, and notional_usd"):
        Action(type="trade")

    with pytest.raises(ValidationError):
        Action(type="Trade")

    Action(
        type="trade",
        symbol="AAPL",
        side="buy",
        notional_usd=100,
    )


def _migrated_db(tmp_path: Path) -> Path:
    db = tmp_path / "multi_intake.db"
    init_ai_tables(db)
    sqlite_mod.init_db(db)
    from db.paper_orders import init_paper_order_tables
    from db.trades import init_trade_tables

    init_trade_tables(db)
    order_executions_mod.init_execution_tables(db)
    init_paper_order_tables(db)
    migrate_multi_account(db)
    return db


def _seed_strat1_account(db: Path) -> str:
    from datetime import UTC, datetime

    from db.base import connect

    now = datetime.now(UTC).isoformat()
    conn_id = "conn_alpaca_strat1"
    acct_id = "acct_alpaca_strat1"
    with connect(db) as conn:
        conn.execute(
            """
            INSERT INTO broker_connections (
                id, broker, label, enabled, credentials_json, created_at
            ) VALUES (?, 'alpaca', 'STRAT1', 1, '{}', ?)
            """,
            (conn_id, now),
        )
        conn.execute(
            """
            INSERT INTO trade_accounts (
                id, label, broker, created_at, connection_id,
                enabled, execution_capable, is_default
            ) VALUES (?, 'STRAT1', 'alpaca', ?, ?, 1, 1, 0)
            """,
            (acct_id, now, conn_id),
        )
        conn.commit()
    return acct_id


def _save_account_snapshot(
    db: Path,
    *,
    account_id: str,
    buying_power: float = 100_000.0,
) -> None:
    account = Account(
        id=account_id,
        status="ACTIVE",
        cash=50_000.0,
        buying_power=buying_power,
        portfolio_value=100_000.0,
    )
    sqlite_mod.save_snapshot(
        account,
        [],
        [],
        broker="alpaca",
        db_path=db,
        account_id=account_id,
    )


def _patch_env_multi(
    monkeypatch,
    db_path: Path,
    *,
    price: float = 150.0,
    symbol: str = "AAPL",
    agent_enabled: bool = True,
    kill_switch: bool = False,
) -> None:
    from db import base as db_base

    monkeypatch.setattr(db_base, "DEFAULT_DB_PATH", db_path)
    monkeypatch.setattr(sqlite_mod, "DEFAULT_DB_PATH", db_path)
    monkeypatch.setattr(order_executions_mod, "DEFAULT_DB_PATH", db_path)
    monkeypatch.setattr(paper_orders_mod, "DEFAULT_DB_PATH", db_path)

    trading_cfg = {
        "execution_mode": "dry_run",
        "kill_switch_active": kill_switch,
        "agent_proposals_enabled": agent_enabled,
        "max_pending_agent_proposals": 3,
        "max_order_value_usd": 50_000,
        "max_position_pct": 30,
        "max_open_orders": 10,
        "max_daily_orders": 20,
    }

    monkeypatch.setattr(
        "trading.risk_engine.get_broker",
        lambda _name: _stub_alpaca(),
    )
    monkeypatch.setattr(
        "trading.risk_engine.market_hub.quote_snapshot",
        lambda _symbols: {symbol: {"price": price}},
    )
    monkeypatch.setattr("trading.risk_engine.is_kill_switch_active", lambda: kill_switch)
    monkeypatch.setattr("trading.risk_engine.trading_config", lambda: trading_cfg)
    monkeypatch.setattr("trading.proposal_intake.is_kill_switch_active", lambda: kill_switch)
    monkeypatch.setattr("trading.proposal_intake.trading_config", lambda: trading_cfg)
    monkeypatch.setattr("trading.proposal_intake.is_market_open", lambda: True)
    monkeypatch.setattr(
        "trading.proposals.notify",
        AsyncMock(),
    )


@pytest.mark.asyncio
async def test_propose_trade_routes_to_bound_account(monkeypatch, tmp_path: Path) -> None:
    db = _migrated_db(tmp_path)
    strat1_id = _seed_strat1_account(db)
    _save_account_snapshot(db, account_id=strat1_id, buying_power=50_000.0)
    _patch_env_multi(monkeypatch, db)

    default = default_execution_account(db_path=db)
    assert default is not None
    _save_account_snapshot(db, account_id=default["id"], buying_power=200.0)

    result = await propose_trade(
        _candidate(notional_usd=Decimal("500"), account_id=strat1_id),
        db_path=db,
    )

    assert result["status"] == "proposed"
    row = paper_orders_mod.get_proposal(result["id"], db_path=db)
    assert row is not None
    import sqlite3

    conn = sqlite3.connect(db)
    stored = conn.execute(
        "SELECT account_id FROM paper_order_proposals WHERE id = ?",
        (result["id"],),
    ).fetchone()
    conn.close()
    assert stored[0] == strat1_id
    assert int(row["qty"]) == 3


@pytest.mark.asyncio
async def test_propose_trade_invalid_account_denied(monkeypatch, tmp_path: Path) -> None:
    db = _migrated_db(tmp_path)
    _patch_env_multi(monkeypatch, db)

    result = await propose_trade(
        _candidate(account_id="acct_nonexistent"),
        db_path=db,
    )
    assert result == {"status": "invalid_account"}
    assert _proposal_count(db) == 0


@pytest.mark.asyncio
async def test_propose_trade_non_exec_account_denied(monkeypatch, tmp_path: Path) -> None:
    from datetime import UTC, datetime

    from db.base import connect

    db = _migrated_db(tmp_path)
    _patch_env_multi(monkeypatch, db)
    now = datetime.now(UTC).isoformat()
    readonly_id = "acct_readonly"
    with connect(db) as conn:
        conn.execute(
            """
            INSERT INTO trade_accounts (
                id, label, broker, created_at,
                enabled, execution_capable, is_default
            ) VALUES (?, 'Read Only', 'schwab', ?, 1, 0, 0)
            """,
            (readonly_id, now),
        )
        conn.commit()

    result = await propose_trade(
        _candidate(account_id=readonly_id),
        db_path=db,
    )
    assert result == {"status": "invalid_account"}


@pytest.mark.asyncio
async def test_propose_trade_per_account_dedup(monkeypatch, tmp_path: Path) -> None:
    db = _migrated_db(tmp_path)
    strat1_id = _seed_strat1_account(db)
    default = default_execution_account(db_path=db)
    assert default is not None
    _save_account_snapshot(db, account_id=strat1_id)
    _save_account_snapshot(db, account_id=default["id"])
    _patch_env_multi(monkeypatch, db)

    built = build_order_proposal_from_args(
        {
            "symbol": "AAPL",
            "side": "buy",
            "qty": 1,
            "order_type": "market",
            "account_id": strat1_id,
        },
        db_path=db,
    )
    paper_orders_mod.create_pending_proposal(
        proposal=built["proposal"],
        intent_fields=built["intent_fields"],
        session_id=None,
        ttl_seconds=300,
        summary="existing on strat1",
        account_id=strat1_id,
        db_path=db,
    )

    result = await propose_trade(
        _candidate(account_id=default["id"]),
        db_path=db,
    )
    assert result["status"] == "proposed"

    result_strat1 = await propose_trade(
        _candidate(account_id=strat1_id),
        db_path=db,
    )
    assert result_strat1 == {"status": "duplicate"}


def test_list_pending_proposals_account_filter(tmp_path: Path) -> None:
    db = tmp_path / "pending_filter.db"
    from db.trades import init_trade_tables

    init_trade_tables(db)
    paper_orders_mod.init_paper_order_tables(db)

    built_a = build_order_proposal_from_args(
        {"symbol": "AAPL", "side": "buy", "qty": 1, "order_type": "market"},
        db_path=db,
    )
    built_b = build_order_proposal_from_args(
        {"symbol": "MSFT", "side": "buy", "qty": 1, "order_type": "market"},
        db_path=db,
    )
    paper_orders_mod.create_pending_proposal(
        proposal=built_a["proposal"],
        intent_fields=built_a["intent_fields"],
        session_id=None,
        ttl_seconds=300,
        summary="acct_a",
        account_id="acct_a",
        db_path=db,
    )
    paper_orders_mod.create_pending_proposal(
        proposal=built_b["proposal"],
        intent_fields=built_b["intent_fields"],
        session_id=None,
        ttl_seconds=300,
        summary="acct_b",
        account_id="acct_b",
        db_path=db,
    )

    all_pending = paper_orders_mod.list_pending_proposals(db_path=db)
    assert len(all_pending) == 2

    filtered = paper_orders_mod.list_pending_proposals(account_id="acct_a", db_path=db)
    assert len(filtered) == 1
    assert filtered[0]["symbol"] == "AAPL"


@pytest.mark.asyncio
async def test_auto_execute_entry_submits_and_pushes(monkeypatch, tmp_path: Path) -> None:
    db = tmp_path / "auto_entry.db"
    _patch_env(monkeypatch, db)

    from trading.models import ExecutionResult

    submit_mock = AsyncMock(
        return_value=ExecutionResult(
            execution_id="exec_auto_1",
            broker_order_id=None,
            execution_mode="paper",
            status="submitted",
            dry_run_log=None,
        )
    )
    notify_mock = AsyncMock(return_value={})
    monkeypatch.setattr("trading.proposal_intake.ExecutionService.submit", submit_mock)
    monkeypatch.setattr("trading.proposal_intake.notify", notify_mock)

    result = await auto_execute_entry(
        _candidate(source="strategy:dip_buy"),
        db_path=db,
    )

    assert result == {"status": "executed", "execution_id": "exec_auto_1"}
    submit_mock.assert_awaited_once()
    intent = submit_mock.await_args.args[0]
    assert intent.symbol == "AAPL"
    assert intent.side == "buy"
    assert intent.proposal_id.startswith("auto_entry|dip_buy|")
    notify_mock.assert_awaited_once()
    call = notify_mock.await_args.kwargs
    assert call["kind"] == "execution"
    assert call["push"] is True
    assert call["title"].startswith("BUY ")


@pytest.mark.asyncio
async def test_auto_execute_entry_risk_blocked_no_push(monkeypatch, tmp_path: Path) -> None:
    db = tmp_path / "auto_entry_reject.db"
    _patch_env(monkeypatch, db)

    from trading.models import RiskRejectedError

    submit_mock = AsyncMock(side_effect=RiskRejectedError("mock block"))
    notify_mock = AsyncMock()
    monkeypatch.setattr("trading.proposal_intake.ExecutionService.submit", submit_mock)
    monkeypatch.setattr("trading.proposal_intake.notify", notify_mock)

    result = await auto_execute_entry(
        _candidate(source="strategy:dip_buy"),
        db_path=db,
    )

    assert result == {"status": "risk_blocked", "reason": "mock block"}
    notify_mock.assert_not_called()


@pytest.mark.asyncio
async def test_auto_execute_entry_dedups_in_flight(monkeypatch, tmp_path: Path) -> None:
    """A symbol with a recent in-flight buy must NOT be auto-entered again (double-submit guard).

    Auto-entries land in order_executions (not pending phone proposals) and aren't yet 'held'
    (snapshot lag), so without this guard a re-scan would re-enter before the first fill.
    """
    db = tmp_path / "auto_entry_dedup.db"
    _patch_env(monkeypatch, db)

    from db.accounts import default_execution_account
    from db.order_executions import create_execution_record

    acct = default_execution_account(db_path=db)
    create_execution_record(
        execution_id="exec_inflight",
        proposal_id="auto_entry|dip_buy|2026-06-26T00:00:00+00:00",
        session_id=None,
        symbol="AAPL",
        side="buy",
        qty=Decimal("2"),
        order_type="market",
        limit_price=None,
        execution_mode="paper",
        status="submitted",
        broker="alpaca",
        account_id=acct["id"] if acct else None,
        db_path=db,
    )

    submit_mock = AsyncMock()
    notify_mock = AsyncMock()
    monkeypatch.setattr("trading.proposal_intake.ExecutionService.submit", submit_mock)
    monkeypatch.setattr("trading.proposal_intake.notify", notify_mock)

    result = await auto_execute_entry(_candidate(source="strategy:dip_buy"), db_path=db)

    assert result == {"status": "duplicate"}
    submit_mock.assert_not_called()
    notify_mock.assert_not_called()


@pytest.mark.asyncio
async def test_auto_execute_entry_blocked_under_live(monkeypatch, tmp_path: Path) -> None:
    """auto_execute_entry's own paper fence: live execution_mode never auto-submits."""
    db = tmp_path / "auto_entry_live.db"
    _patch_env(monkeypatch, db)
    # Override the mode to live (the inner fence reads trading_config fresh).
    monkeypatch.setattr(
        "trading.proposal_intake.trading_config",
        lambda: {"execution_mode": "live"},
    )

    submit_mock = AsyncMock()
    notify_mock = AsyncMock()
    monkeypatch.setattr("trading.proposal_intake.ExecutionService.submit", submit_mock)
    monkeypatch.setattr("trading.proposal_intake.notify", notify_mock)

    result = await auto_execute_entry(_candidate(source="strategy:dip_buy"), db_path=db)

    assert result == {"status": "blocked_live"}
    submit_mock.assert_not_called()
    notify_mock.assert_not_called()
