"""Exit engine — auto-sell dispatch, guards, and closure reconcile."""

from __future__ import annotations

import math
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from brokers.base import Account, Position
from db import order_executions as order_executions_mod
from db import sqlite as sqlite_mod
from db.base import connect, now_iso
from db.earnings_calendar import upsert_earnings
from db.managed_positions import list_open_positions, record_open_position, update_exit_state
from db.multi_account import migrate_multi_account
from db.paper_orders import init_paper_order_tables
from db.sqlite import init_db
from db.strategies import init_strategy_tables, seed_default_strategies, update_strategy
from trading.exit_engine import _process_managed_position, run_exit_engine


def _db(tmp_path: Path) -> Path:
    db = tmp_path / "exit_engine.db"
    init_db(db)
    migrate_multi_account(db)
    init_strategy_tables(db)
    init_paper_order_tables(db)
    order_executions_mod.init_execution_tables(db)
    seed_default_strategies(db_path=db)
    return db


def _seed_account(db: Path, *, account_id: str = "acct_1", broker: str = "alpaca") -> None:
    now = datetime.now(UTC).isoformat()
    with connect(db) as conn:
        conn.execute(
            """
            INSERT INTO trade_accounts (
                id, label, broker, created_at,
                enabled, execution_capable, is_default
            ) VALUES (?, 'Test', ?, ?, 1, 1, 1)
            """,
            (account_id, broker, now),
        )
        conn.commit()


def _save_snapshot(
    db: Path,
    *,
    account_id: str = "acct_1",
    positions: list[Position] | None = None,
    broker: str = "alpaca",
) -> None:
    account = Account(
        id=account_id,
        status="ACTIVE",
        cash=50_000.0,
        buying_power=100_000.0,
        portfolio_value=100_000.0,
    )
    sqlite_mod.save_snapshot(
        account,
        positions or [],
        [],
        broker=broker,
        account_id=account_id,
        db_path=db,
    )


def _position(
    *,
    symbol: str = "AAPL",
    qty: float = 10.0,
    avg_entry_price: float = 100.0,
    current_price: float = 100.0,
) -> Position:
    return Position(
        symbol=symbol,
        qty=qty,
        side="long",
        market_value=qty * current_price,
        unrealized_pl=0.0,
        avg_entry_price=avg_entry_price,
        current_price=current_price,
    )


def _open_managed(
    db: Path,
    *,
    symbol: str = "AAPL",
    strategy_id: str = "dip_buy",
    exit_mode: str = "auto",
    params: dict | None = None,
    first_target_hit: bool = False,
    exit_context: dict | None = None,
    account_id: str = "acct_1",
) -> str:
    pid = record_open_position(
        account_id=account_id,
        symbol=symbol,
        strategy_id=strategy_id,
        qty=str(10),
        exit_mode=exit_mode,
        params=params or {"stop_pct": -3.0, "first_target_pct": 3.0},
        db_path=db,
    )
    if first_target_hit or exit_context:
        update_exit_state(
            pid,
            first_target_hit=first_target_hit if first_target_hit else None,
            exit_context=exit_context,
            db_path=db,
        )
    return pid


@pytest.fixture
def exit_stubs(monkeypatch: pytest.MonkeyPatch):
    submit_calls: list = []
    proposal_calls: list = []
    broadcast_calls: list = []

    class _SubmitResult:
        execution_id = "exec_x"
        status = "submitted"
        execution_mode = "paper"

    async def _submit(intent, *, db_path):
        submit_calls.append({"intent": intent, "db_path": db_path})
        return _SubmitResult()

    async def _proposal(args, *, source=None, ttl_seconds=None, db_path=None, **kwargs):
        proposal_calls.append(
            {"args": args, "source": source, "db_path": db_path, **kwargs}
        )
        return {"status": "pending_phone", "id": "prop_x"}

    async def _broadcast(payload):
        broadcast_calls.append(payload)

    async def _push(**kwargs):
        pass

    monkeypatch.setattr("trading.exit_engine.ExecutionService.submit", _submit)
    monkeypatch.setattr("trading.exit_engine.create_phone_proposal", _proposal)
    monkeypatch.setattr("trading.exit_engine.market_hub.broadcast_alert", _broadcast)
    monkeypatch.setattr("trading.exit_engine.send_alert_push", _push)

    return {
        "submit_calls": submit_calls,
        "proposal_calls": proposal_calls,
        "broadcast_calls": broadcast_calls,
    }


@pytest.mark.asyncio
async def test_dip_stop_auto_dispatches_sell(
    tmp_path: Path, exit_stubs, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = _db(tmp_path)
    _seed_account(db)
    _save_snapshot(
        db,
        positions=[_position(avg_entry_price=100.0, current_price=96.0, qty=10.0)],
    )
    _open_managed(db, params={"stop_pct": -3.0})

    outcomes = await run_exit_engine(db_path=db)

    assert len(outcomes) == 1
    assert outcomes[0]["action"] == "exit"
    assert outcomes[0]["mode"] == "auto"
    assert len(exit_stubs["submit_calls"]) == 1
    intent = exit_stubs["submit_calls"][0]["intent"]
    assert intent.side == "sell"
    assert intent.order_type == "market"
    assert intent.qty == Decimal(str(math.floor(10.0)))
    assert intent.account_id == "acct_1"
    assert len(exit_stubs["proposal_calls"]) == 0
    row = list_open_positions(db_path=db)[0]
    assert row["exit_context"]["pending_exit"]["kind"] == "auto"
    assert row["exit_context"]["pending_exit"]["ref"] == "exec_x"
    assert len(exit_stubs["broadcast_calls"]) == 1
    assert exit_stubs["broadcast_calls"][0]["source"] == "exit_engine"


@pytest.mark.asyncio
async def test_dip_first_target_auto_scales(
    tmp_path: Path, exit_stubs, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = _db(tmp_path)
    _seed_account(db)
    _save_snapshot(
        db,
        positions=[_position(avg_entry_price=100.0, current_price=104.0, qty=10.0)],
    )
    _open_managed(db, params={"stop_pct": -3.0, "first_target_pct": 3.0})

    outcomes = await run_exit_engine(db_path=db)

    assert outcomes[0]["action"] == "scale"
    assert outcomes[0]["qty"] == 5
    intent = exit_stubs["submit_calls"][0]["intent"]
    assert intent.qty == Decimal("5")
    row = list_open_positions(db_path=db)[0]
    assert row["first_target_hit"] is True


@pytest.mark.asyncio
async def test_dip_hold_persists_hwm_no_submit(
    tmp_path: Path, exit_stubs, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = _db(tmp_path)
    _seed_account(db)
    _save_snapshot(
        db,
        positions=[
            _position(avg_entry_price=100.0, current_price=105.0, qty=10.0),
        ],
    )
    pid = _open_managed(
        db,
        params={"stop_pct": -3.0, "trail_pct": 11.0},
        first_target_hit=True,
    )
    update_exit_state(pid, high_water_mark="104.0", db_path=db)

    outcomes = await run_exit_engine(db_path=db)

    assert outcomes[0]["action"] == "hold"
    assert exit_stubs["submit_calls"] == []
    row = list_open_positions(db_path=db)[0]
    assert row["high_water_mark"] == "105.0"


@pytest.mark.asyncio
async def test_earnings_within_lead_auto_exits(
    tmp_path: Path, exit_stubs, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = _db(tmp_path)
    _seed_account(db)
    today = "2026-06-22T12:00:00+00:00"
    monkeypatch.setattr("trading.exit_engine.now_iso", lambda: today)
    upsert_earnings([{"symbol": "AAPL", "date": "2026-06-23"}], db_path=db)
    update_strategy("earnings_drift", exit_mode="auto", db_path=db)
    _save_snapshot(
        db,
        positions=[_position(avg_entry_price=100.0, current_price=101.0, qty=8.0)],
    )
    record_open_position(
        account_id="acct_1",
        symbol="AAPL",
        strategy_id="earnings_drift",
        qty="8",
        exit_mode="auto",
        params={"exit_lead_td": 1},
        db_path=db,
    )

    outcomes = await run_exit_engine(db_path=db)

    assert outcomes[0]["action"] == "exit"
    assert outcomes[0]["qty"] == 8
    assert exit_stubs["submit_calls"][0]["intent"].qty == Decimal("8")


@pytest.mark.asyncio
async def test_propose_mode_uses_phone_proposal(
    tmp_path: Path, exit_stubs, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = _db(tmp_path)
    _seed_account(db)
    update_strategy("dip_buy", exit_mode="propose", db_path=db)
    _save_snapshot(
        db,
        positions=[_position(avg_entry_price=100.0, current_price=96.0, qty=10.0)],
    )
    _open_managed(db, exit_mode="propose")

    outcomes = await run_exit_engine(db_path=db)

    assert outcomes[0]["mode"] == "propose"
    assert len(exit_stubs["proposal_calls"]) == 1
    assert exit_stubs["submit_calls"] == []
    row = list_open_positions(db_path=db)[0]
    assert row["exit_context"]["pending_exit"]["kind"] == "propose"
    assert len(exit_stubs["broadcast_calls"]) == 1


@pytest.mark.asyncio
async def test_in_flight_guard_blocks_duplicate_submit(
    tmp_path: Path, exit_stubs, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = _db(tmp_path)
    _seed_account(db)
    _save_snapshot(
        db,
        positions=[_position(avg_entry_price=100.0, current_price=96.0, qty=10.0)],
    )
    _open_managed(
        db,
        exit_context={
            "pending_exit": {
                "kind": "auto",
                "ref": "exec_live",
                "at": now_iso(),
            }
        },
    )
    order_executions_mod.create_execution_record(
        execution_id="exec_live",
        proposal_id="p1",
        session_id=None,
        symbol="AAPL",
        side="sell",
        qty=Decimal("10"),
        order_type="market",
        limit_price=None,
        execution_mode="paper",
        status="submitted",
        broker="alpaca",
        account_id="acct_1",
        db_path=db,
    )

    outcomes = await run_exit_engine(db_path=db)

    assert outcomes[0]["action"] == "skip"
    assert outcomes[0]["reason"] == "in_flight"
    assert exit_stubs["submit_calls"] == []


@pytest.mark.asyncio
async def test_throttle_guard_blocks_fresh_unknown_ref(
    tmp_path: Path, exit_stubs, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = _db(tmp_path)
    _seed_account(db)
    _save_snapshot(
        db,
        positions=[_position(avg_entry_price=100.0, current_price=96.0, qty=10.0)],
    )
    _open_managed(
        db,
        exit_context={
            "pending_exit": {
                "kind": "auto",
                "ref": "exec_missing",
                "at": now_iso(),
            }
        },
    )

    outcomes = await run_exit_engine(db_path=db)

    assert outcomes[0]["reason"] == "in_flight"
    assert exit_stubs["submit_calls"] == []


@pytest.mark.asyncio
async def test_closure_reconcile_closes_row_no_submit(
    tmp_path: Path, exit_stubs, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = _db(tmp_path)
    _seed_account(db)
    # Managed row first, THEN the (empty) snapshot — so captured_at post-dates entry
    # and the position is legitimately "gone" (it was held, the fresh snapshot has it
    # flat). This is the real closure scenario.
    _open_managed(db)
    _save_snapshot(db, positions=[])

    outcomes = await run_exit_engine(db_path=db)

    assert outcomes[0]["action"] == "closed"
    assert list_open_positions(db_path=db) == []
    assert exit_stubs["submit_calls"] == []


@pytest.mark.asyncio
async def test_entry_postdating_snapshot_is_not_closed(
    tmp_path: Path, exit_stubs, monkeypatch: pytest.MonkeyPatch
) -> None:
    # C-2 regression: a BUY that just filled (managed row created) but isn't in the
    # current pre-fill snapshot yet must NOT be closed — closing it would orphan a
    # live position from all exit management. Snapshot captured BEFORE the row exists.
    db = _db(tmp_path)
    _seed_account(db)
    _save_snapshot(db, positions=[])  # captured before the managed row is recorded
    _open_managed(db)  # entry_date now post-dates the snapshot

    outcomes = await run_exit_engine(db_path=db)

    assert outcomes[0]["action"] == "skip"
    assert outcomes[0]["reason"] == "entry_postdates_snapshot"
    assert len(list_open_positions(db_path=db)) == 1  # NOT closed
    assert exit_stubs["submit_calls"] == []


@pytest.mark.asyncio
async def test_scale_qty_below_one_skips_but_persists_target(
    tmp_path: Path, exit_stubs, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = _db(tmp_path)
    _seed_account(db)
    _save_snapshot(
        db,
        positions=[_position(avg_entry_price=100.0, current_price=104.0, qty=1.0)],
    )
    _open_managed(db, params={"stop_pct": -3.0, "first_target_pct": 3.0})

    outcomes = await run_exit_engine(db_path=db)

    assert outcomes[0]["action"] == "skip"
    assert outcomes[0]["reason"] == "qty_below_1"
    assert exit_stubs["submit_calls"] == []
    row = list_open_positions(db_path=db)[0]
    assert row["first_target_hit"] is True


@pytest.mark.asyncio
async def test_unknown_kind_skips(tmp_path: Path, exit_stubs) -> None:
    db = _db(tmp_path)
    _seed_account(db)
    _save_snapshot(
        db,
        positions=[_position(avg_entry_price=100.0, current_price=96.0, qty=10.0)],
    )
    record_open_position(
        account_id="acct_1",
        symbol="AAPL",
        strategy_id="manual_x",
        qty="10",
        exit_mode="auto",
        params={},
        db_path=db,
    )

    outcomes = await run_exit_engine(db_path=db)

    assert outcomes[0]["reason"] == "unknown_kind"
    assert exit_stubs["submit_calls"] == []


@pytest.mark.asyncio
async def test_no_snapshot_skips_without_closing(
    tmp_path: Path, exit_stubs, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = _db(tmp_path)
    _seed_account(db)
    _open_managed(db)

    outcomes = await run_exit_engine(db_path=db)

    assert outcomes[0]["action"] == "skip"
    assert outcomes[0]["reason"] == "no_snapshot"
    assert len(list_open_positions(db_path=db)) == 1
    assert exit_stubs["submit_calls"] == []


@pytest.mark.asyncio
async def test_wrong_account_snapshot_is_rejected(
    tmp_path: Path, exit_stubs, monkeypatch: pytest.MonkeyPatch
) -> None:
    # m-1 regression: get_latest_snapshot falls back to the broker-wide latest run
    # when the account has none. A snapshot belonging to a DIFFERENT account must be
    # rejected (treated as no snapshot) — never acted on, never used to mass-close.
    db = _db(tmp_path)
    _seed_account(db)  # acct_1 (the managed row's account)
    # The only snapshot in the broker belongs to acct_2.
    _save_snapshot(db, account_id="acct_2", positions=[_position()])
    _open_managed(db, account_id="acct_1")

    outcomes = await run_exit_engine(db_path=db)

    assert outcomes[0]["action"] == "skip"
    assert outcomes[0]["reason"] == "no_snapshot"
    assert len(list_open_positions(db_path=db)) == 1  # NOT closed by a foreign snapshot
    assert exit_stubs["submit_calls"] == []


@pytest.mark.asyncio
async def test_portfolio_position_skipped_entirely(tmp_path: Path) -> None:
    """Portfolio-kind (momentum) positions are skipped by the exit engine — no stop/target/
    time exit, no closure reconcile. The monthly rebalance owns their full lifecycle."""
    db = tmp_path / "exit_portfolio.db"
    _open_managed(db, strategy_id="momentum_sp500", symbol="AAPL")
    row = list_open_positions(db_path=db)[0]
    res = await _process_managed_position(
        row,
        broker_name="alpaca",
        positions_by_symbol={},
        snapshot=None,
        db_path=db,
    )
    assert res["action"] == "skip"
    assert res["reason"] == "portfolio_rebalance_managed"
