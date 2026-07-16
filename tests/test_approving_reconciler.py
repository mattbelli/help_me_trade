"""H2 approving-state reconciler."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from db import order_executions as order_executions_mod
from db import paper_orders as paper_orders_mod
from db.order_executions import create_execution_record
from db.paper_orders import get_proposal, transition_proposal_status
from trading.proposals import reconcile_orphaned_approvals


def _insert_approving_proposal(
    db: Path,
    proposal_id: str,
    *,
    created_at: str | None = None,
    expires_at: str | None = None,
) -> None:
    """Eligibility is anchored to expires_at + max_age (claims must precede
    expiry, so expiry+margin proves no live approve flow). Default expires_at
    tracks created_at + 300s so `_old_ts()`-seeded rows are reconciler-eligible
    and fresh rows are not — mirroring real create_pending_proposal TTLs."""
    paper_orders_mod.init_paper_order_tables(db)
    ts = created_at or datetime.now(UTC).isoformat()
    if expires_at is None:
        base = datetime.fromisoformat(ts)
        if base.tzinfo is None:
            base = base.replace(tzinfo=UTC)
        expires_at = (base + timedelta(seconds=300)).isoformat()
    conn = sqlite3.connect(db)
    conn.execute(
        """
        INSERT INTO paper_order_proposals (
            id, session_id, symbol, side, qty, order_type, limit_price,
            status, proposal_json, created_at, expires_at, payload_hash, delivery
        ) VALUES (?, NULL, 'AAPL', 'buy', 1, 'market', NULL,
                  'approving', ?, ?, ?, 'hash', 'phone')
        """,
        (
            proposal_id,
            '{"summary": "BUY 1 AAPL @ market"}',
            ts,
            expires_at,
        ),
    )
    conn.commit()
    conn.close()


def _old_ts(seconds: int = 700) -> str:
    """700s: created+300s TTL puts expiry 400s ago — past the 300s post-expiry
    eligibility margin."""
    return (datetime.now(UTC) - timedelta(seconds=seconds)).isoformat()


@pytest.mark.asyncio
async def test_reconcile_execution_submitted_to_executed(monkeypatch, tmp_path: Path) -> None:
    db = tmp_path / "recon.db"
    proposal_id = "paper_recon1"
    _insert_approving_proposal(db, proposal_id, created_at=_old_ts())

    create_execution_record(
        execution_id="exec_sub1",
        proposal_id=proposal_id,
        session_id=None,
        symbol="AAPL",
        side="buy",
        qty=Decimal("1"),
        order_type="market",
        limit_price=None,
        execution_mode="paper",
        status="submitted",
        broker="alpaca",
        db_path=db,
    )

    la_mock = AsyncMock()
    notify_mock = AsyncMock(return_value={})
    monkeypatch.setattr("trading.proposals._try_end_proposal_live_activity", la_mock)
    monkeypatch.setattr("trading.proposals.notify", notify_mock)

    count = await reconcile_orphaned_approvals(db_path=db)

    assert count == 1
    row = get_proposal(proposal_id, db_path=db)
    assert row is not None
    assert row["status"] == "executed"
    assert row["execution_id"] == "exec_sub1"
    assert row["decided_at"]
    la_mock.assert_awaited_once()
    assert la_mock.await_args.kwargs["status"] == "executed"
    notify_mock.assert_awaited_once()
    assert notify_mock.await_args.kwargs["kind"] == "proposal_reconciled"
    assert notify_mock.await_args.kwargs["push"] is False


@pytest.mark.asyncio
async def test_reconcile_execution_rejected_to_failed(monkeypatch, tmp_path: Path) -> None:
    db = tmp_path / "recon.db"
    proposal_id = "paper_recon2"
    _insert_approving_proposal(db, proposal_id, created_at=_old_ts())

    create_execution_record(
        execution_id="exec_rej1",
        proposal_id=proposal_id,
        session_id=None,
        symbol="AAPL",
        side="buy",
        qty=Decimal("1"),
        order_type="market",
        limit_price=None,
        execution_mode="paper",
        status="rejected",
        broker="alpaca",
        db_path=db,
    )

    la_mock = AsyncMock()
    notify_mock = AsyncMock(return_value={})
    monkeypatch.setattr("trading.proposals._try_end_proposal_live_activity", la_mock)
    monkeypatch.setattr("trading.proposals.notify", notify_mock)

    count = await reconcile_orphaned_approvals(db_path=db)

    assert count == 1
    row = get_proposal(proposal_id, db_path=db)
    assert row is not None
    assert row["status"] == "failed"
    assert row["last_error"] == "rejected"
    la_mock.assert_awaited_once()
    assert la_mock.await_args.kwargs["status"] == "failed"


@pytest.mark.asyncio
async def test_reconcile_no_execution_to_orphaned_failed(monkeypatch, tmp_path: Path) -> None:
    db = tmp_path / "recon.db"
    proposal_id = "paper_recon3"
    _insert_approving_proposal(db, proposal_id, created_at=_old_ts())

    la_mock = AsyncMock()
    notify_mock = AsyncMock(return_value={})
    monkeypatch.setattr("trading.proposals._try_end_proposal_live_activity", la_mock)
    monkeypatch.setattr("trading.proposals.notify", notify_mock)

    count = await reconcile_orphaned_approvals(db_path=db)

    assert count == 1
    row = get_proposal(proposal_id, db_path=db)
    assert row is not None
    assert row["status"] == "failed"
    assert "orphaned approving" in row["last_error"]


@pytest.mark.asyncio
async def test_reconcile_skips_young_approving(monkeypatch, tmp_path: Path) -> None:
    db = tmp_path / "recon.db"
    proposal_id = "paper_young"
    _insert_approving_proposal(db, proposal_id, created_at=datetime.now(UTC).isoformat())

    la_mock = AsyncMock()
    notify_mock = AsyncMock(return_value={})
    monkeypatch.setattr("trading.proposals._try_end_proposal_live_activity", la_mock)
    monkeypatch.setattr("trading.proposals.notify", notify_mock)

    count = await reconcile_orphaned_approvals(db_path=db)

    assert count == 0
    row = get_proposal(proposal_id, db_path=db)
    assert row is not None
    assert row["status"] == "approving"
    la_mock.assert_not_awaited()
    notify_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_reconcile_race_guard_skips_concurrent_terminal(
    monkeypatch, tmp_path: Path
) -> None:
    db = tmp_path / "recon.db"
    proposal_id = "paper_race"
    _insert_approving_proposal(db, proposal_id, created_at=_old_ts())

    create_execution_record(
        execution_id="exec_race",
        proposal_id=proposal_id,
        session_id=None,
        symbol="AAPL",
        side="buy",
        qty=Decimal("1"),
        order_type="market",
        limit_price=None,
        execution_mode="paper",
        status="submitted",
        broker="alpaca",
        db_path=db,
    )

    transition_proposal_status(
        proposal_id,
        expect="approving",
        to="executed",
        execution_id="exec_race",
        db_path=db,
    )

    la_mock = AsyncMock()
    notify_mock = AsyncMock(return_value={})
    monkeypatch.setattr("trading.proposals._try_end_proposal_live_activity", la_mock)
    monkeypatch.setattr("trading.proposals.notify", notify_mock)

    count = await reconcile_orphaned_approvals(db_path=db)

    assert count == 0
    notify_mock.assert_not_awaited()
    la_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_reconcile_idempotent_second_pass(monkeypatch, tmp_path: Path) -> None:
    db = tmp_path / "recon.db"
    proposal_id = "paper_idem"
    _insert_approving_proposal(db, proposal_id, created_at=_old_ts())

    la_mock = AsyncMock()
    notify_mock = AsyncMock(return_value={})
    monkeypatch.setattr("trading.proposals._try_end_proposal_live_activity", la_mock)
    monkeypatch.setattr("trading.proposals.notify", notify_mock)

    first = await reconcile_orphaned_approvals(db_path=db)
    second = await reconcile_orphaned_approvals(db_path=db)

    assert first == 1
    assert second == 0
    assert notify_mock.await_count == 1
    assert la_mock.await_count == 1


def test_get_execution_by_proposal_returns_most_recent(tmp_path: Path) -> None:
    db = tmp_path / "exec.db"
    proposal_id = "paper_exec_lookup"

    create_execution_record(
        execution_id="exec_old",
        proposal_id=proposal_id,
        session_id=None,
        symbol="AAPL",
        side="buy",
        qty=Decimal("1"),
        order_type="market",
        limit_price=None,
        execution_mode="paper",
        status="error",
        broker="alpaca",
        db_path=db,
    )
    conn = sqlite3.connect(db)
    conn.execute(
        "UPDATE order_executions SET created_at = ? WHERE id = ?",
        ("2026-01-01T00:00:00+00:00", "exec_old"),
    )
    conn.commit()
    conn.close()

    create_execution_record(
        execution_id="exec_new",
        proposal_id=proposal_id,
        session_id=None,
        symbol="AAPL",
        side="buy",
        qty=Decimal("1"),
        order_type="market",
        limit_price=None,
        execution_mode="paper",
        status="submitted",
        broker="alpaca",
        db_path=db,
    )

    row = order_executions_mod.get_execution_by_proposal(proposal_id, db_path=db)
    assert row is not None
    assert row["id"] == "exec_new"


@pytest.mark.asyncio
async def test_reconcile_skips_inflight_pending_execution(
    monkeypatch, tmp_path: Path
) -> None:
    """An execution in 'pending' (audit row written, validate mid-flight) must
    NOT be resolved — marking the proposal failed while the order may still
    place is the exact wrong-state hazard the in-flight skip closes."""
    db = tmp_path / "recon.db"
    proposal_id = "paper_inflight"
    _insert_approving_proposal(db, proposal_id, created_at=_old_ts())

    create_execution_record(
        execution_id="exec_inflight",
        proposal_id=proposal_id,
        session_id=None,
        symbol="AAPL",
        side="buy",
        qty=Decimal("1"),
        order_type="market",
        limit_price=None,
        execution_mode="paper",
        status="pending",
        broker="alpaca",
        db_path=db,
    )

    la_mock = AsyncMock()
    notify_mock = AsyncMock(return_value={})
    monkeypatch.setattr("trading.proposals._try_end_proposal_live_activity", la_mock)
    monkeypatch.setattr("trading.proposals.notify", notify_mock)

    count = await reconcile_orphaned_approvals(db_path=db)

    assert count == 0
    row = get_proposal(proposal_id, db_path=db)
    assert row is not None
    assert row["status"] == "approving"  # untouched — resolves on a later pass
    la_mock.assert_not_awaited()
    notify_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_reconcile_ineligible_before_expiry_plus_margin(
    monkeypatch, tmp_path: Path
) -> None:
    """A claim can happen up to the last second of TTL — eligibility must be
    anchored to expires_at + margin, not created_at + margin."""
    db = tmp_path / "recon.db"
    proposal_id = "paper_lateclaim"
    # Created 400s ago with a 300s TTL: expired 100s ago — NOT yet 300s past
    # expiry, so a late claim's approve flow could still be alive. Ineligible.
    _insert_approving_proposal(
        db, proposal_id, created_at=_old_ts(seconds=400)
    )

    la_mock = AsyncMock()
    notify_mock = AsyncMock(return_value={})
    monkeypatch.setattr("trading.proposals._try_end_proposal_live_activity", la_mock)
    monkeypatch.setattr("trading.proposals.notify", notify_mock)

    count = await reconcile_orphaned_approvals(db_path=db)

    assert count == 0
    row = get_proposal(proposal_id, db_path=db)
    assert row is not None
    assert row["status"] == "approving"


@pytest.mark.asyncio
@pytest.mark.parametrize("broker_terminal", ["canceled", "expired"])
async def test_reconcile_canceled_expired_execution_to_executed(
    monkeypatch, tmp_path: Path, broker_terminal: str
) -> None:
    """Livelock regression (adversarial finding): fill_tracker can flip
    submitted->canceled/expired before the reconciler's first eligible look.
    Those orders verifiably reached the broker -> proposal resolves 'executed'
    (same convention as bare submitted), never stuck in approving."""
    db = tmp_path / "recon.db"
    proposal_id = f"paper_{broker_terminal}"
    _insert_approving_proposal(db, proposal_id, created_at=_old_ts())

    create_execution_record(
        execution_id=f"exec_{broker_terminal}",
        proposal_id=proposal_id,
        session_id=None,
        symbol="AAPL",
        side="buy",
        qty=Decimal("1"),
        order_type="market",
        limit_price=None,
        execution_mode="paper",
        status=broker_terminal,
        broker="alpaca",
        db_path=db,
    )

    la_mock = AsyncMock()
    notify_mock = AsyncMock(return_value={})
    monkeypatch.setattr("trading.proposals._try_end_proposal_live_activity", la_mock)
    monkeypatch.setattr("trading.proposals.notify", notify_mock)

    count = await reconcile_orphaned_approvals(db_path=db)

    assert count == 1
    row = get_proposal(proposal_id, db_path=db)
    assert row is not None
    assert row["status"] == "executed"
    assert row["execution_id"] == f"exec_{broker_terminal}"
