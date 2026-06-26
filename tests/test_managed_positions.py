"""Managed-position ledger + strategy-fill attribution (exit module D1)."""

from __future__ import annotations

from pathlib import Path

from db.base import connect
from db.managed_positions import (
    close_position,
    list_open_positions,
    record_open_position,
    update_exit_state,
)
from db.multi_account import migrate_multi_account
from db.paper_orders import init_paper_order_tables
from db.sqlite import init_db
from db.strategies import init_strategy_tables, seed_default_strategies
from trading.position_ledger import record_strategy_fill


def _db(tmp_path: Path) -> Path:
    db = tmp_path / "managed.db"
    init_db(db)
    migrate_multi_account(db)
    init_strategy_tables(db)
    init_paper_order_tables(db)
    seed_default_strategies(db_path=db)
    return db


def _seed_proposal(
    db: Path, *, pid: str, source: str, account_id: str, symbol: str = "AAPL"
) -> None:
    with connect(db) as conn:
        conn.execute(
            """
            INSERT INTO paper_order_proposals
                (id, symbol, side, qty, proposal_json, created_at, source, account_id, status)
            VALUES (?, ?, 'buy', 10, '{}', '2026-06-22', ?, ?, 'filled')
            """,
            (pid, symbol, source, account_id),
        )
        conn.commit()


def test_record_open_and_idempotent(tmp_path: Path) -> None:
    db = _db(tmp_path)
    pid1 = record_open_position(
        account_id="acct_1",
        symbol="aapl",
        strategy_id="dip_buy",
        qty="10",
        exit_mode="auto",
        params={"stop_pct": -3.0},
        db_path=db,
    )
    # Same (account, symbol, strategy) while open → updates, no second row.
    pid2 = record_open_position(
        account_id="acct_1",
        symbol="AAPL",
        strategy_id="dip_buy",
        qty="15",
        exit_mode="auto",
        params={"stop_pct": -3.0},
        db_path=db,
    )
    assert pid1 == pid2
    rows = list_open_positions(account_id="acct_1", db_path=db)
    assert len(rows) == 1
    assert rows[0]["symbol"] == "AAPL"
    assert rows[0]["qty"] == "15"
    assert rows[0]["params"]["stop_pct"] == -3.0


def test_update_and_close(tmp_path: Path) -> None:
    db = _db(tmp_path)
    pid = record_open_position(
        account_id="acct_1",
        symbol="AAPL",
        strategy_id="dip_buy",
        qty="10",
        exit_mode="auto",
        params={},
        db_path=db,
    )
    update_exit_state(pid, high_water_mark="151.5", first_target_hit=True, db_path=db)
    row = list_open_positions(db_path=db)[0]
    assert row["high_water_mark"] == "151.5"
    assert row["first_target_hit"] is True

    close_position(pid, reason="stop", db_path=db)
    assert list_open_positions(db_path=db) == []
    # After close, a new entry for the same (account, symbol, strategy) is allowed.
    record_open_position(
        account_id="acct_1",
        symbol="AAPL",
        strategy_id="dip_buy",
        qty="5",
        exit_mode="auto",
        params={},
        db_path=db,
    )
    assert len(list_open_positions(db_path=db)) == 1


def test_record_strategy_fill_buy_records(tmp_path: Path) -> None:
    db = _db(tmp_path)
    _seed_proposal(db, pid="paper_1", source="strategy:dip_buy", account_id="acct_x")
    mp_id = record_strategy_fill(
        {
            "id": "exec_1",
            "symbol": "AAPL",
            "side": "buy",
            "proposal_id": "paper_1",
            "account_id": "acct_x",
        },
        filled_qty=10.0,
        status="filled",
        db_path=db,
    )
    assert mp_id is not None
    rows = list_open_positions(account_id="acct_x", db_path=db)
    assert len(rows) == 1
    assert rows[0]["strategy_id"] == "dip_buy"
    assert rows[0]["qty"] == "10.0"


def test_record_strategy_fill_ignores_sell_and_nonstrategy(tmp_path: Path) -> None:
    db = _db(tmp_path)
    _seed_proposal(db, pid="paper_sell", source="strategy:dip_buy", account_id="a")
    # A SELL never opens a managed position.
    assert (
        record_strategy_fill(
            {"id": "e", "symbol": "AAPL", "side": "sell", "proposal_id": "paper_sell", "account_id": "a"},
            filled_qty=10.0,
            status="filled",
            db_path=db,
        )
        is None
    )
    # A non-strategy source (manual/rule) is not managed.
    _seed_proposal(db, pid="paper_manual", source="manual", account_id="a")
    assert (
        record_strategy_fill(
            {"id": "e2", "symbol": "AAPL", "side": "buy", "proposal_id": "paper_manual", "account_id": "a"},
            filled_qty=10.0,
            status="filled",
            db_path=db,
        )
        is None
    )
    # Non-terminal status is ignored.
    _seed_proposal(db, pid="paper_pend", source="strategy:dip_buy", account_id="a", symbol="MSFT")
    assert (
        record_strategy_fill(
            {"id": "e3", "symbol": "MSFT", "side": "buy", "proposal_id": "paper_pend", "account_id": "a"},
            filled_qty=10.0,
            status="accepted",
            db_path=db,
        )
        is None
    )
    assert list_open_positions(db_path=db) == []
