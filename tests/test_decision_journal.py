"""Decision Journal — CONTEXT/DECISION/OUTCOME write path + fail-soft guarantees.

The journal hooks the execution/approval HOT PATH. The invariant these tests defend: a
journal write must NEVER break proposal creation, approval, fill reconciliation, or a
position close. Outcomes are computed only from paper fill prices, never fabricated.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from brokers.base import Account, Position
from db import decision_journal as dj
from db import order_executions as order_executions_mod
from db import paper_orders as paper_orders_mod
from db import sqlite as sqlite_mod
from db.decision_journal import (
    get_by_proposal,
    record_decision,
    resolve_outcome,
    set_decision,
    set_entry_fill,
)
from db.managed_positions import close_position, record_open_position
from db.multi_account import migrate_multi_account
from db.order_executions import create_execution_record, update_execution_status
from db.strategies import init_strategy_tables, seed_default_strategies
from trading.position_ledger import record_strategy_fill
from trading.proposals import approve_proposal, create_phone_proposal, reject_proposal

# --- Harness -----------------------------------------------------------------------------


def _patch_passing_env(monkeypatch, db_path: Path) -> None:
    """Enough environment for create_phone_proposal/approve_proposal in dry_run."""
    from brokers.alpaca import AlpacaAdapter
    from db import base as db_base

    monkeypatch.setattr(db_base, "DEFAULT_DB_PATH", db_path)
    monkeypatch.setattr(sqlite_mod, "DEFAULT_DB_PATH", db_path)
    monkeypatch.setattr(order_executions_mod, "DEFAULT_DB_PATH", db_path)
    monkeypatch.setattr(paper_orders_mod, "DEFAULT_DB_PATH", db_path)

    account = Account(
        id="acc1", status="ACTIVE", cash=50_000.0,
        buying_power=100_000.0, portfolio_value=100_000.0,
    )
    positions = [
        Position(symbol="AAPL", qty=10.0, side="long", market_value=1_500.0,
                 unrealized_pl=50.0, avg_entry_price=145.0, current_price=150.0)
    ]
    sqlite_mod.save_snapshot(account, positions, [], broker="alpaca", db_path=db_path)

    stub = object.__new__(AlpacaAdapter)
    stub.symbol_exists = lambda _symbol: True  # type: ignore[method-assign]
    cfg = {
        "execution_mode": "dry_run", "kill_switch_active": False,
        "max_order_value_usd": 50_000, "max_position_pct": 30,
        "max_open_orders": 10, "max_daily_orders": 20,
    }
    monkeypatch.setattr("trading.risk_engine.get_broker", lambda _name: stub)
    monkeypatch.setattr(
        "trading.risk_engine.market_hub.quote_snapshot",
        lambda _symbols: {"AAPL": {"price": 150.0}},
    )
    monkeypatch.setattr("trading.risk_engine.is_kill_switch_active", lambda: False)
    monkeypatch.setattr("trading.risk_engine.trading_config", lambda: cfg)
    monkeypatch.setattr("trading.execution_service.is_kill_switch_active", lambda: False)
    monkeypatch.setattr("trading.execution_service.trading_config", lambda: cfg)
    monkeypatch.setattr("trading.execution_service.notify", AsyncMock(return_value={}))
    monkeypatch.setattr("trading.proposals.notify", AsyncMock(return_value={}))


def _order_args(**overrides) -> dict:
    base = {"symbol": "AAPL", "side": "buy", "qty": 2, "order_type": "market"}
    base.update(overrides)
    return base


def _expire(db: Path, proposal_id: str) -> None:
    past = (datetime.now(UTC) - timedelta(seconds=5)).isoformat()
    conn = sqlite3.connect(db)
    conn.execute(
        "UPDATE paper_order_proposals SET expires_at = ? WHERE id = ?", (past, proposal_id)
    )
    conn.commit()
    conn.close()


# --- CONTEXT on propose ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_context_recorded_on_propose(monkeypatch, tmp_path: Path) -> None:
    db = tmp_path / "j.db"
    _patch_passing_env(monkeypatch, db)

    row = await create_phone_proposal(
        _order_args(), source="strategy:dip_buy", rule_id="dip_buy",
        thesis="dip below VWAP", entry_ref_price="150.0", score=False,
        ttl_seconds=300, db_path=db,
    )
    j = get_by_proposal(row["id"], db_path=db)
    assert j is not None
    assert j["symbol"] == "AAPL"
    assert j["side"] == "buy"
    assert j["thesis"] == "dip below VWAP"
    assert j["strategy_id"] == "dip_buy"
    assert j["entry_ref_price"] == "150.0"
    assert j["decision"] is None  # not decided yet
    assert j["outcome"] is None


@pytest.mark.asyncio
async def test_context_idempotent_on_proposal_id(tmp_path: Path) -> None:
    db = tmp_path / "j.db"
    record_decision(proposal_id="p1", symbol="AAPL", side="buy", qty="10",
                    order_type="market", thesis="first", db_path=db)
    # A second write for the same proposal_id must NOT overwrite the CONTEXT.
    record_decision(proposal_id="p1", symbol="AAPL", side="buy", qty="10",
                    order_type="market", thesis="second", db_path=db)
    j = get_by_proposal("p1", db_path=db)
    assert j["thesis"] == "first"


# --- Every proposal journaled, incl. declines --------------------------------------------


@pytest.mark.asyncio
async def test_rejected_proposal_is_never_filled(monkeypatch, tmp_path: Path) -> None:
    db = tmp_path / "j.db"
    _patch_passing_env(monkeypatch, db)

    row = await create_phone_proposal(_order_args(), score=False, ttl_seconds=300, db_path=db)
    out = await reject_proposal(row["id"], db_path=db)
    assert out["status"] == "rejected"

    j = get_by_proposal(row["id"], db_path=db)
    assert j["decision"] == "rejected"
    assert j["decided_by"] == "phone"
    assert j["outcome"] == "never_filled"
    assert j["realized_pnl"] is None


@pytest.mark.asyncio
async def test_expired_proposal_is_never_filled(monkeypatch, tmp_path: Path) -> None:
    db = tmp_path / "j.db"
    _patch_passing_env(monkeypatch, db)

    row = await create_phone_proposal(_order_args(), score=False, ttl_seconds=300, db_path=db)
    _expire(db, row["id"])
    out = await approve_proposal(row["id"], db_path=db)
    assert out["status"] == "expired"

    j = get_by_proposal(row["id"], db_path=db)
    assert j["decision"] == "expired"
    assert j["decided_by"] == "system"
    assert j["outcome"] == "never_filled"


@pytest.mark.asyncio
async def test_approved_proposal_records_decision(monkeypatch, tmp_path: Path) -> None:
    db = tmp_path / "j.db"
    _patch_passing_env(monkeypatch, db)

    row = await create_phone_proposal(_order_args(), score=False, ttl_seconds=300, db_path=db)
    out = await approve_proposal(row["id"], decided_by="web", db_path=db)
    assert out["status"] == "executed"

    j = get_by_proposal(row["id"], db_path=db)
    assert j["decision"] == "approved"
    assert j["decided_by"] == "web"


# --- Outcome computation -----------------------------------------------------------------


def _seed_long(db: Path, *, pid: str, entry: str, stop_pct: float = -3.0) -> None:
    import json

    record_decision(
        proposal_id=pid, symbol="AAPL", side="buy", qty="10", order_type="market",
        entry_ref_price=entry, plan_json=json.dumps({"kind": "dip", "stop_pct": stop_pct}),
        db_path=db,
    )
    set_decision(pid, decision="approved", decided_by="phone", db_path=db)
    set_entry_fill(pid, entry_execution_id="entry_ex", entry_fill_price=entry,
                   qty_filled="10", db_path=db)


def test_outcome_win(tmp_path: Path) -> None:
    db = tmp_path / "j.db"
    _seed_long(db, pid="win1", entry="100")
    resolve_outcome("win1", exit_execution_id="exit_ex", exit_fill_price="110",
                    close_reason="target", entry_time="2026-06-01T14:30:00+00:00",
                    close_time="2026-06-05T20:00:00+00:00", db_path=db)
    j = get_by_proposal("win1", db_path=db)
    assert j["outcome"] == "win"
    assert Decimal(j["realized_pnl"]) == Decimal("100")  # (110-100)*10
    assert j["r_multiple"] == pytest.approx(10.0 / 3.0)  # (110-100)/(100-97)
    assert j["exit_fill_price"] == "110"
    assert j["exit_execution_id"] == "exit_ex"
    assert j["close_reason"] == "target"
    assert j["holding_days"] == pytest.approx(4.229, abs=0.01)


def test_outcome_loss(tmp_path: Path) -> None:
    db = tmp_path / "j.db"
    _seed_long(db, pid="loss1", entry="100")
    resolve_outcome("loss1", exit_execution_id="exit_ex", exit_fill_price="95",
                    close_reason="stop", db_path=db)
    j = get_by_proposal("loss1", db_path=db)
    assert j["outcome"] == "loss"
    assert Decimal(j["realized_pnl"]) == Decimal("-50")  # (95-100)*10
    assert j["r_multiple"] == pytest.approx(-5.0 / 3.0)  # (95-100)/3


def test_outcome_scratch(tmp_path: Path) -> None:
    db = tmp_path / "j.db"
    _seed_long(db, pid="sc1", entry="100")
    resolve_outcome("sc1", exit_execution_id="exit_ex", exit_fill_price="100",
                    close_reason="time_cap", db_path=db)
    j = get_by_proposal("sc1", db_path=db)
    assert j["outcome"] == "scratch"
    assert Decimal(j["realized_pnl"]) == Decimal("0")


def test_outcome_options_multiplier(tmp_path: Path) -> None:
    """Options P&L must apply the ×100 contract multiplier (else recorded 100× too small)."""
    db = tmp_path / "j.db"
    record_decision(proposal_id="opt1", symbol="AAPL", side="buy", qty="2",
                    order_type="limit", entry_ref_price="3.50",
                    asset_class="us_option", multiplier="100", db_path=db)
    set_entry_fill("opt1", entry_execution_id="ex", entry_fill_price="3.50",
                   qty_filled="2", db_path=db)
    resolve_outcome("opt1", exit_execution_id="exx", exit_fill_price="5.00",
                    close_reason="target", db_path=db)
    j = get_by_proposal("opt1", db_path=db)
    assert j["outcome"] == "win"
    assert j["asset_class"] == "us_option"
    assert j["multiplier"] == "100"
    # (5.00 - 3.50) × 2 contracts × 100 multiplier = 300.00
    assert Decimal(j["realized_pnl"]) == Decimal("300.00")


def test_outcome_equity_default_multiplier_is_one(tmp_path: Path) -> None:
    """Equity (no multiplier passed) defaults to 1 — pnl unchanged."""
    db = tmp_path / "j.db"
    record_decision(proposal_id="eq1", symbol="AAPL", side="buy", qty="10",
                    order_type="market", entry_ref_price="100", db_path=db)
    set_entry_fill("eq1", entry_execution_id="ex", entry_fill_price="100",
                   qty_filled="10", db_path=db)
    resolve_outcome("eq1", exit_fill_price="110", db_path=db)
    j = get_by_proposal("eq1", db_path=db)
    assert j["multiplier"] == "1"
    assert Decimal(j["realized_pnl"]) == Decimal("100")  # (110-100)*10*1


def test_reconcile_closure_is_incomplete(tmp_path: Path) -> None:
    """No exit price sourceable (position vanished, no agent sell) → closed_incomplete."""
    db = tmp_path / "j.db"
    _seed_long(db, pid="inc1", entry="100")
    resolve_outcome("inc1", exit_execution_id=None, exit_fill_price=None,
                    close_reason="closed_externally", db_path=db)
    j = get_by_proposal("inc1", db_path=db)
    assert j["outcome"] == "closed_incomplete"
    assert j["realized_pnl"] is None
    assert j["r_multiple"] is None


def test_resolve_no_row_is_noop(tmp_path: Path) -> None:
    db = tmp_path / "j.db"
    # No CONTEXT row for this proposal (e.g. a synthetic auto_entry id) — must not raise.
    resolve_outcome("ghost", exit_fill_price="110", db_path=db)
    assert get_by_proposal("ghost", db_path=db) is None


# --- close_position integration (J0 linkage + J1 final outcome) --------------------------


def test_close_position_resolves_journal_from_execution(tmp_path: Path) -> None:
    db = tmp_path / "j.db"
    migrate_multi_account(db)

    # Entry execution carries the proposal id the journal is keyed on.
    create_execution_record(
        execution_id="entry_ex", proposal_id="pClose", session_id=None, symbol="AAPL",
        side="buy", qty=Decimal("10"), order_type="market", limit_price=None,
        execution_mode="paper", status="filled", broker="alpaca",
        account_id="acct_x", db_path=db,
    )
    # Exit execution carries the realized sell price.
    create_execution_record(
        execution_id="exit_ex", proposal_id=None, session_id=None, symbol="AAPL",
        side="sell", qty=Decimal("10"), order_type="market", limit_price=None,
        execution_mode="paper", status="submitted", broker="alpaca",
        account_id="acct_x", db_path=db,
    )
    update_execution_status("exit_ex", "filled", avg_fill_price=Decimal("108"),
                            filled_qty=Decimal("10"), db_path=db)

    import json

    record_decision(proposal_id="pClose", symbol="AAPL", side="buy", qty="10",
                    order_type="market", entry_ref_price="100",
                    plan_json=json.dumps({"stop_pct": -3.0}), db_path=db)
    set_entry_fill("pClose", entry_execution_id="entry_ex", entry_fill_price="100",
                   qty_filled="10", db_path=db)

    pos_id = record_open_position(
        account_id="acct_x", symbol="AAPL", strategy_id="dip_buy", qty="10",
        exit_mode="auto", params={}, entry_execution_id="entry_ex", db_path=db,
    )

    close_position(pos_id, reason="target", exit_execution_id="exit_ex", db_path=db)

    j = get_by_proposal("pClose", db_path=db)
    assert j["outcome"] == "win"
    assert j["exit_fill_price"] == "108"  # sourced from the exit execution row
    assert Decimal(j["realized_pnl"]) == Decimal("80")  # (108-100)*10
    assert j["exit_execution_id"] == "exit_ex"


def test_close_position_no_exit_execution_is_incomplete(tmp_path: Path) -> None:
    db = tmp_path / "j.db"
    migrate_multi_account(db)
    create_execution_record(
        execution_id="entry_ex2", proposal_id="pInc", session_id=None, symbol="AAPL",
        side="buy", qty=Decimal("10"), order_type="market", limit_price=None,
        execution_mode="paper", status="filled", broker="alpaca",
        account_id="acct_x", db_path=db,
    )
    record_decision(proposal_id="pInc", symbol="AAPL", side="buy", qty="10",
                    order_type="market", entry_ref_price="100", db_path=db)
    set_entry_fill("pInc", entry_execution_id="entry_ex2", entry_fill_price="100",
                   qty_filled="10", db_path=db)
    pos_id = record_open_position(
        account_id="acct_x", symbol="AAPL", strategy_id="dip_buy", qty="10",
        exit_mode="auto", params={}, entry_execution_id="entry_ex2", db_path=db,
    )
    # A reconcile-closure: no exit execution linked.
    close_position(pos_id, reason="closed_externally", db_path=db)
    j = get_by_proposal("pInc", db_path=db)
    assert j["outcome"] == "closed_incomplete"
    assert j["realized_pnl"] is None


# --- FAIL-SOFT: a journal exception must never propagate into the hot path ----------------


@pytest.mark.asyncio
async def test_reject_survives_journal_exception(monkeypatch, tmp_path: Path) -> None:
    db = tmp_path / "j.db"
    _patch_passing_env(monkeypatch, db)
    row = await create_phone_proposal(_order_args(), score=False, ttl_seconds=300, db_path=db)

    def _boom(*a, **k):
        raise RuntimeError("journal down")

    monkeypatch.setattr(dj, "set_decision", _boom)

    out = await reject_proposal(row["id"], db_path=db)
    # The decline still succeeds despite the journal write blowing up.
    assert out["status"] == "rejected"
    stored = paper_orders_mod.get_proposal(row["id"], db_path=db)
    assert stored["status"] == "rejected"


def test_record_strategy_fill_survives_journal_exception(monkeypatch, tmp_path: Path) -> None:
    db = tmp_path / "j.db"
    sqlite_mod.init_db(db)
    migrate_multi_account(db)
    init_strategy_tables(db)
    paper_orders_mod.init_paper_order_tables(db)
    seed_default_strategies(db_path=db)

    def _boom(*a, **k):
        raise RuntimeError("journal down")

    monkeypatch.setattr(dj, "set_entry_fill", _boom)

    mp_id = record_strategy_fill(
        {"id": "exec_auto", "symbol": "AAPL", "side": "buy",
         "proposal_id": "auto_entry|dip_buy|2026-06-26T12:00:00+00:00",
         "account_id": "acct_x"},
        filled_qty=10.0, filled_avg_price=101.0, status="filled", db_path=db,
    )
    # The managed position is still recorded — the journal failure was swallowed.
    assert mp_id is not None


def test_close_position_survives_journal_exception(monkeypatch, tmp_path: Path) -> None:
    db = tmp_path / "j.db"
    migrate_multi_account(db)
    create_execution_record(
        execution_id="entry_ex3", proposal_id="pFail", session_id=None, symbol="AAPL",
        side="buy", qty=Decimal("10"), order_type="market", limit_price=None,
        execution_mode="paper", status="filled", broker="alpaca",
        account_id="acct_x", db_path=db,
    )
    record_decision(proposal_id="pFail", symbol="AAPL", side="buy", qty="10",
                    order_type="market", entry_ref_price="100", db_path=db)
    pos_id = record_open_position(
        account_id="acct_x", symbol="AAPL", strategy_id="dip_buy", qty="10",
        exit_mode="auto", params={}, entry_execution_id="entry_ex3", db_path=db,
    )

    def _boom(*a, **k):
        raise RuntimeError("journal down")

    monkeypatch.setattr(dj, "resolve_outcome", _boom)

    # Must not raise, and the position must still be closed.
    close_position(pos_id, reason="target", exit_execution_id=None, db_path=db)
    from db.managed_positions import list_open_positions

    assert list_open_positions(account_id="acct_x", db_path=db) == []


def test_close_position_survives_row_refetch_exception(monkeypatch, tmp_path: Path) -> None:
    """The post-commit row re-fetch is inside the fail-soft block: if the SELECT raises,
    close_position() must still return normally (the close UPDATE already committed)."""
    db = tmp_path / "j.db"
    migrate_multi_account(db)
    pos_id = record_open_position(
        account_id="acct_x", symbol="AAPL", strategy_id="dip_buy", qty="10",
        exit_mode="auto", params={}, entry_execution_id="entry_ex_rf", db_path=db,
    )

    import db.managed_positions as mp_mod

    real_connect = mp_mod._connect
    calls = {"n": 0}

    def flaky_connect(path=db):
        calls["n"] += 1
        # 1st call = the close UPDATE (must succeed); 2nd = the re-fetch inside
        # _record_close_outcome (make it blow up).
        if calls["n"] >= 2:
            raise sqlite3.OperationalError("db locked during outcome re-fetch")
        return real_connect(path)

    monkeypatch.setattr(mp_mod, "_connect", flaky_connect)

    # Must not raise despite the re-fetch SELECT throwing.
    close_position(pos_id, reason="target", db_path=db)

    monkeypatch.undo()  # restore _connect for the verification query
    assert mp_mod.list_open_positions(account_id="acct_x", db_path=db) == []


@pytest.mark.asyncio
async def test_create_proposal_survives_context_journal_exception(
    monkeypatch, tmp_path: Path
) -> None:
    """A CONTEXT-write failure must NEVER break proposal creation."""
    db = tmp_path / "j.db"
    _patch_passing_env(monkeypatch, db)

    def _boom(*a, **k):
        raise RuntimeError("journal down")

    monkeypatch.setattr(dj, "record_decision", _boom)

    row = await create_phone_proposal(_order_args(), score=False, ttl_seconds=300, db_path=db)
    # The proposal is still created despite the CONTEXT write blowing up.
    assert row["status"] == "pending_phone"
    assert paper_orders_mod.get_proposal(row["id"], db_path=db) is not None
