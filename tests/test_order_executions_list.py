"""``list_executions`` — the read-only blotter query (status filter + newest-first)."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from db import order_executions as order_executions_mod


def _seed(
    db_path: Path,
    *,
    execution_id: str,
    status: str,
    created_at: datetime,
    symbol: str = "AAPL",
    account_id: str | None = None,
) -> None:
    order_executions_mod.create_execution_record(
        execution_id=execution_id,
        proposal_id="prop1",
        session_id=None,
        symbol=symbol,
        side="buy",
        qty=Decimal("10"),
        order_type="market",
        limit_price=None,
        execution_mode="paper",
        status=status,
        broker="alpaca",
        account_id=account_id,
        db_path=db_path,
    )
    created_iso = created_at.astimezone(UTC).isoformat()
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "UPDATE order_executions SET created_at = ? WHERE id = ?",
            (created_iso, execution_id),
        )
        conn.commit()


def test_list_executions_newest_first(tmp_path: Path) -> None:
    db = tmp_path / "exec.db"
    now = datetime.now(UTC)
    _seed(db, execution_id="e1", status="filled", created_at=now - timedelta(minutes=3))
    _seed(db, execution_id="e2", status="rejected", created_at=now - timedelta(minutes=2))
    _seed(db, execution_id="e3", status="filled", created_at=now - timedelta(minutes=1))

    rows = order_executions_mod.list_executions(db_path=db)

    assert [r["id"] for r in rows] == ["e3", "e2", "e1"]


def test_list_executions_filters_by_status(tmp_path: Path) -> None:
    db = tmp_path / "exec.db"
    now = datetime.now(UTC)
    _seed(db, execution_id="e1", status="filled", created_at=now - timedelta(minutes=3))
    _seed(db, execution_id="e2", status="rejected", created_at=now - timedelta(minutes=2))
    _seed(db, execution_id="e3", status="filled", created_at=now - timedelta(minutes=1))

    rows = order_executions_mod.list_executions(status="rejected", db_path=db)

    assert [r["id"] for r in rows] == ["e2"]
    assert rows[0]["status"] == "rejected"


def test_list_executions_filters_by_account_and_respects_limit(tmp_path: Path) -> None:
    db = tmp_path / "exec.db"
    now = datetime.now(UTC)
    _seed(
        db,
        execution_id="e1",
        status="filled",
        created_at=now - timedelta(minutes=3),
        account_id="acct_a",
    )
    _seed(
        db,
        execution_id="e2",
        status="filled",
        created_at=now - timedelta(minutes=2),
        account_id="acct_b",
    )
    _seed(
        db,
        execution_id="e3",
        status="filled",
        created_at=now - timedelta(minutes=1),
        account_id="acct_a",
    )

    rows = order_executions_mod.list_executions(account_id="acct_a", db_path=db)
    assert [r["id"] for r in rows] == ["e3", "e1"]

    limited = order_executions_mod.list_executions(limit=1, db_path=db)
    assert [r["id"] for r in limited] == ["e3"]
