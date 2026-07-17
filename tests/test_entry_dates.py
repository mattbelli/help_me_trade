"""entry_dates_by_symbol — flat→long walk over filled executions."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from db.order_executions import create_execution_record, entry_dates_by_symbol


def _fill(
    db: Path,
    *,
    execution_id: str,
    symbol: str,
    side: str,
    qty: str,
    submitted_at: str,
    account_id: str = "acct_a",
) -> None:
    create_execution_record(
        execution_id=execution_id,
        proposal_id=None,
        session_id=None,
        symbol=symbol,
        side=side,
        qty=Decimal(qty),
        order_type="market",
        limit_price=None,
        execution_mode="paper",
        status="filled",
        broker="alpaca",
        submitted_at=submitted_at,
        account_id=account_id,
        db_path=db,
    )


def test_entry_survives_partial_sell_and_resets_on_reentry(tmp_path: Path) -> None:
    db = tmp_path / "exec.db"
    _fill(db, execution_id="e1", symbol="GEN", side="buy", qty="10", submitted_at="2026-07-01T13:30:00+00:00")
    _fill(db, execution_id="e2", symbol="GEN", side="sell", qty="4", submitted_at="2026-07-02T13:30:00+00:00")
    assert entry_dates_by_symbol("acct_a", db_path=db) == {
        "GEN": "2026-07-01T13:30:00+00:00"
    }

    _fill(db, execution_id="e3", symbol="GEN", side="sell", qty="6", submitted_at="2026-07-03T13:30:00+00:00")
    assert entry_dates_by_symbol("acct_a", db_path=db) == {}

    _fill(db, execution_id="e4", symbol="GEN", side="buy", qty="5", submitted_at="2026-07-10T13:30:00+00:00")
    assert entry_dates_by_symbol("acct_a", db_path=db) == {
        "GEN": "2026-07-10T13:30:00+00:00"
    }


def test_scopes_by_account_and_ignores_unfilled(tmp_path: Path) -> None:
    db = tmp_path / "exec.db"
    _fill(db, execution_id="e1", symbol="MO", side="buy", qty="3", submitted_at="2026-07-16T13:30:00+00:00")
    _fill(db, execution_id="e2", symbol="MO", side="buy", qty="3", submitted_at="2026-06-01T13:30:00+00:00", account_id="acct_b")
    create_execution_record(
        execution_id="e3",
        proposal_id=None,
        session_id=None,
        symbol="PWR",
        side="buy",
        qty=Decimal("1"),
        order_type="market",
        limit_price=None,
        execution_mode="paper",
        status="rejected",
        broker="alpaca",
        submitted_at="2026-07-16T13:31:00+00:00",
        account_id="acct_a",
        db_path=db,
    )
    assert entry_dates_by_symbol("acct_a", db_path=db) == {
        "MO": "2026-07-16T13:30:00+00:00"
    }
