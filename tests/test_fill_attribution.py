"""safety-M1: a strategy BUY that fills but can't be attributed must never be a
silent orphan — it is retried, then durably quarantined + paged (critical)."""

from __future__ import annotations

import logging
import sqlite3
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from db import fill_attribution as fa
from db.managed_positions import close_position, list_open_positions, record_open_position
from db.multi_account import migrate_multi_account
from db.order_executions import (
    create_execution_record,
    init_execution_tables,
    update_execution_status,
)
from db.paper_orders import init_paper_order_tables
from db.sqlite import init_db
from db.strategies import init_strategy_tables, seed_default_strategies
from trading import position_ledger
from trading.position_ledger import (
    flush_fill_attribution_orphans,
    record_strategy_fill,
    sweep_unmanaged_terminal_fills,
)

# A dip_buy strategy exists after seeding; auto_entry ids resolve without a proposal row.
_AUTO_DIP = "auto_entry|dip_buy|2026-06-26T12:00:00+00:00"


def _db(tmp_path: Path) -> Path:
    db = tmp_path / "fa.db"
    init_db(db)
    migrate_multi_account(db)
    init_strategy_tables(db)
    init_paper_order_tables(db)
    seed_default_strategies(db_path=db)
    return db


def _buy_fill(execution_id: str = "exec_orphan", proposal_id: str = _AUTO_DIP) -> dict:
    return {
        "id": execution_id,
        "symbol": "AAPL",
        "side": "buy",
        "proposal_id": proposal_id,
        "account_id": "acct_x",
    }


def test_write_failure_quarantines_and_does_not_orphan(
    monkeypatch, caplog, tmp_path: Path
) -> None:
    db = _db(tmp_path)

    def _boom(*a, **k):
        raise RuntimeError("managed_positions write down")

    monkeypatch.setattr(position_ledger, "record_open_position", _boom)

    with caplog.at_level(logging.ERROR, logger="trading.position_ledger"):
        out = record_strategy_fill(_buy_fill(), filled_qty=10.0, status="filled", db_path=db)

    # The fill is NOT silently recorded as managed...
    assert out is None
    assert list_open_positions(db_path=db) == []
    # ...it is durably quarantined for repair...
    orphans = fa.list_open_orphans(db_path=db)
    assert len(orphans) == 1
    assert orphans[0]["reason"] == "write_failed"
    assert orphans[0]["execution_id"] == "exec_orphan"
    assert orphans[0]["strategy_id"] == "dip_buy"
    # ...and it is logged loudly.
    assert any("UNMANAGED FILL" in r.message for r in caplog.records)


def test_transient_write_retries_then_succeeds(monkeypatch, tmp_path: Path) -> None:
    db = _db(tmp_path)
    calls = {"n": 0}
    real = record_open_position

    def _flaky(*a, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            raise sqlite3.OperationalError("database is locked")
        return real(*a, **k)

    monkeypatch.setattr(position_ledger, "record_open_position", _flaky)
    monkeypatch.setattr(position_ledger, "_WRITE_RETRY_SLEEP_S", 0.0)

    out = record_strategy_fill(_buy_fill(), filled_qty=10.0, status="filled", db_path=db)

    assert out is not None  # recovered on retry
    assert calls["n"] == 2
    assert fa.list_open_orphans(db_path=db) == []  # no orphan when it eventually writes
    rows = list_open_positions(account_id="acct_x", db_path=db)
    assert len(rows) == 1 and rows[0]["strategy_id"] == "dip_buy"


def test_unknown_strategy_quarantines(tmp_path: Path) -> None:
    db = _db(tmp_path)
    out = record_strategy_fill(
        _buy_fill(execution_id="exec_ghost", proposal_id="auto_entry|ghost_strat|t"),
        filled_qty=5.0,
        status="filled",
        db_path=db,
    )
    assert out is None
    orphans = fa.list_open_orphans(db_path=db)
    assert len(orphans) == 1
    assert orphans[0]["reason"] == "unknown_strategy"
    assert orphans[0]["strategy_id"] == "ghost_strat"


def test_orphan_is_idempotent_per_execution(monkeypatch, tmp_path: Path) -> None:
    db = _db(tmp_path)
    monkeypatch.setattr(
        position_ledger, "record_open_position",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("down")),
    )
    for _ in range(3):
        record_strategy_fill(_buy_fill(), filled_qty=10.0, status="filled", db_path=db)
    # Re-reconciling the same execution must not page three times.
    assert len(fa.list_open_orphans(db_path=db)) == 1


def _seed_terminal_buy(
    db: Path,
    *,
    execution_id: str,
    status: str,
    filled_qty: str | None,
    proposal_id: str = _AUTO_DIP,
    symbol: str = "AAPL",
    account_id: str = "acct_x",
) -> None:
    """A real order_executions row: a BUY that reached ``status`` with ``filled_qty``
    shares (the sweep's candidate)."""
    init_execution_tables(db)
    create_execution_record(
        execution_id=execution_id,
        proposal_id=proposal_id,
        session_id=None,
        symbol=symbol,
        side="buy",
        qty=Decimal("100"),
        order_type="market",
        limit_price=None,
        execution_mode="paper",
        status=status,
        broker="alpaca",
        account_id=account_id,
        db_path=db,
    )
    if filled_qty is not None:
        update_execution_status(execution_id, status, filled_qty=filled_qty, db_path=db)


@pytest.mark.asyncio
async def test_sweep_quarantines_partial_then_terminal(monkeypatch, tmp_path: Path) -> None:
    """H1 poll-gap / restart walk: a strategy BUY partial-fills, then the order jumps to
    canceled/expired while the poller was down (a deploy restart or overnight gap). The
    poll wakes to submitted->canceled, filled_qty=70 — record_strategy_fill never saw the
    transition, so there is NO managed row and NO orphan. The sweep must quarantine it +
    page ONCE, and re-running (another restart) must not double-page."""
    db = _db(tmp_path)
    _seed_terminal_buy(db, execution_id="exec_gap", status="canceled", filled_qty="70")

    # Precondition: real shares held, but unmanaged and unquarantined — the silent hole.
    assert list_open_positions(db_path=db) == []
    assert fa.list_open_orphans(db_path=db) == []

    notify = AsyncMock(return_value={"id": "notif_sweep"})
    monkeypatch.setattr("signals.notify.notify", notify)

    swept = await sweep_unmanaged_terminal_fills(db_path=db)

    assert swept == 1
    # Durably quarantined for repair...
    orphans_all = fa.orphan_exists_for_execution("exec_gap", db_path=db)
    assert orphans_all is True
    # ...and paged EXACTLY once (a batch summary, not per-row)...
    notify.assert_awaited_once()
    call = notify.await_args.kwargs
    assert call["severity"] == "critical"
    assert call["push"] is True
    assert call["data"]["type"] == "attribution_orphan_sweep"
    assert call["data"]["count"] == 1
    # ...and marked notified, so the per-row flush + a re-sweep do NOT re-page.
    assert fa.list_open_orphans(db_path=db) == []
    notify.reset_mock()
    assert await sweep_unmanaged_terminal_fills(db_path=db) == 0
    notify.assert_not_awaited()
    assert await flush_fill_attribution_orphans(db_path=db) == 0


@pytest.mark.asyncio
async def test_sweep_quarantines_filled_crash_seam(monkeypatch, tmp_path: Path) -> None:
    """M1 crash seam: reconcile wrote status='filled' then the process died BEFORE
    record_strategy_fill ran. The row is 'filled' with no managed row and no orphan —
    out of the reconcile pool forever. The sweep recovers it."""
    db = _db(tmp_path)
    _seed_terminal_buy(db, execution_id="exec_crash", status="filled", filled_qty="42")

    notify = AsyncMock(return_value={"id": "n"})
    monkeypatch.setattr("signals.notify.notify", notify)

    assert await sweep_unmanaged_terminal_fills(db_path=db) == 1
    assert fa.orphan_exists_for_execution("exec_crash", db_path=db) is True


@pytest.mark.asyncio
async def test_sweep_skips_already_attributed_fill(monkeypatch, tmp_path: Path) -> None:
    """A fill whose managed row exists (normal attribution) is NOT re-quarantined."""
    db = _db(tmp_path)
    _seed_terminal_buy(db, execution_id="exec_ok", status="filled", filled_qty="10")
    record_open_position(
        account_id="acct_x", symbol="AAPL", strategy_id="dip_buy", qty="10",
        exit_mode="propose", params={}, entry_execution_id="exec_ok", db_path=db,
    )

    notify = AsyncMock(return_value={"id": "n"})
    monkeypatch.setattr("signals.notify.notify", notify)

    assert await sweep_unmanaged_terminal_fills(db_path=db) == 0
    notify.assert_not_awaited()
    assert fa.orphan_exists_for_execution("exec_ok", db_path=db) is False


@pytest.mark.asyncio
async def test_sweep_skips_open_managed_triple(monkeypatch, tmp_path: Path) -> None:
    """A later ADD whose shares fold into an existing OPEN managed row (same
    account/symbol/strategy, different execution_id) is NOT quarantined. The row
    must predate the add — a fold, by construction, lands in a row that was already
    open when the add executed."""
    db = _db(tmp_path)
    # Open row opened by a DIFFERENT (earlier) execution for the same triple...
    record_open_position(
        account_id="acct_x", symbol="AAPL", strategy_id="dip_buy", qty="5",
        exit_mode="propose", params={}, entry_execution_id="exec_first", db_path=db,
    )
    # ...then the add executes and folds into it (no entry_execution_id of its own).
    _seed_terminal_buy(db, execution_id="exec_add", status="filled", filled_qty="5")

    notify = AsyncMock(return_value={"id": "n"})
    monkeypatch.setattr("signals.notify.notify", notify)

    assert await sweep_unmanaged_terminal_fills(db_path=db) == 0
    assert fa.orphan_exists_for_execution("exec_add", db_path=db) is False


@pytest.mark.asyncio
async def test_sweep_skips_folded_add_after_position_closes(
    monkeypatch, tmp_path: Path
) -> None:
    """Adversarial F1 regression: open A -> fold add B (no entry_execution_id of its
    own) -> position CLOSES (correctly sold by the exit engine). B's execution now
    matches no row by entry_execution_id and no OPEN triple row — a plain
    status='open' check would falsely quarantine B and fire a false CRITICAL page for
    shares that were correctly managed and already sold. The lifetime-bounded lookup
    (row alive at B's created_at) must SKIP it: no orphan, no page."""
    db = _db(tmp_path)
    # Open A.
    position_id = record_open_position(
        account_id="acct_x", symbol="AAPL", strategy_id="dip_buy", qty="10",
        exit_mode="propose", params={}, entry_execution_id="exec_a", db_path=db,
    )
    # Fold add B: executes while the row is open; upsert refreshes the row without
    # storing B's execution id.
    _seed_terminal_buy(db, execution_id="exec_b", status="filled", filled_qty="5")
    record_open_position(
        account_id="acct_x", symbol="AAPL", strategy_id="dip_buy", qty="15",
        exit_mode="propose", params={}, entry_execution_id="exec_b_ignored", db_path=db,
    )
    # Position closes (exit engine sold everything).
    close_position(position_id, reason="target_hit", db_path=db)
    assert list_open_positions(db_path=db) == []

    notify = AsyncMock(return_value={"id": "n"})
    monkeypatch.setattr("signals.notify.notify", notify)

    assert await sweep_unmanaged_terminal_fills(db_path=db) == 0
    notify.assert_not_awaited()  # no false CRITICAL page
    assert fa.orphan_exists_for_execution("exec_b", db_path=db) is False


@pytest.mark.asyncio
async def test_sweep_skips_non_strategy_and_zero_fill(monkeypatch, tmp_path: Path) -> None:
    """Rows the sweep must leave alone: a non-strategy BUY (no managed row by design),
    and a terminal row with zero filled shares (nothing to manage)."""
    db = _db(tmp_path)
    # Non-strategy proposal id: not auto_entry and no proposal row -> not attributable.
    _seed_terminal_buy(
        db, execution_id="exec_manual", status="canceled", filled_qty="8",
        proposal_id="manual_ticket_1",
    )
    # Strategy BUY that filled ZERO (canceled before any fill).
    _seed_terminal_buy(db, execution_id="exec_zero", status="canceled", filled_qty="0")

    notify = AsyncMock(return_value={"id": "n"})
    monkeypatch.setattr("signals.notify.notify", notify)

    assert await sweep_unmanaged_terminal_fills(db_path=db) == 0
    notify.assert_not_awaited()
    assert fa.list_open_orphans(db_path=db) == []


@pytest.mark.asyncio
async def test_sweep_pages_once_for_batch(monkeypatch, tmp_path: Path) -> None:
    """Backfill-safe: many historical unmanaged fills page ONCE with a count."""
    db = _db(tmp_path)
    _seed_terminal_buy(db, execution_id="e1", status="canceled", filled_qty="3", symbol="AAPL")
    _seed_terminal_buy(db, execution_id="e2", status="expired", filled_qty="4", symbol="NVDA")

    notify = AsyncMock(return_value={"id": "n"})
    monkeypatch.setattr("signals.notify.notify", notify)

    assert await sweep_unmanaged_terminal_fills(db_path=db) == 2
    notify.assert_awaited_once()  # ONE page for the batch, not one per row
    assert notify.await_args.kwargs["data"]["count"] == 2


@pytest.mark.asyncio
async def test_sweep_leaves_open_when_notify_persist_fails(
    monkeypatch, tmp_path: Path
) -> None:
    """If the batch page never persists (notify returns {}), the orphan stays 'open' so
    the per-row flush still pages it — degraded but never lost."""
    db = _db(tmp_path)
    _seed_terminal_buy(db, execution_id="exec_lost", status="canceled", filled_qty="9")

    notify = AsyncMock(return_value={})  # persist failed inside notify()
    monkeypatch.setattr("signals.notify.notify", notify)

    assert await sweep_unmanaged_terminal_fills(db_path=db) == 1
    # Recorded but NOT marked notified -> still 'open' for the per-row flush fallback.
    assert len(fa.list_open_orphans(db_path=db)) == 1
    # And the sweep stays idempotent: a re-run does not create a second orphan.
    assert await sweep_unmanaged_terminal_fills(db_path=db) == 0
    assert len(fa.list_open_orphans(db_path=db)) == 1


@pytest.mark.asyncio
async def test_sweep_survives_notify_raising_mid_batch(
    monkeypatch, tmp_path: Path
) -> None:
    """Adversarial F5a: notify RAISES (not just returns {}) during the batch page.
    The exception propagates out of the sweep — the _poll_once try/except wrapper is
    what isolates the cycle — but the orphans were quarantined BEFORE paging, so they
    persist 'open', the next sweep is idempotent (no duplicate orphan), and the
    per-row flush still pages them."""
    db = _db(tmp_path)
    _seed_terminal_buy(db, execution_id="exec_boom", status="canceled", filled_qty="6")

    notify = AsyncMock(side_effect=RuntimeError("push service down"))
    monkeypatch.setattr("signals.notify.notify", notify)

    with pytest.raises(RuntimeError, match="push service down"):
        await sweep_unmanaged_terminal_fills(db_path=db)

    # Quarantine landed before the page attempt and stays 'open'.
    orphans = fa.list_open_orphans(db_path=db)
    assert len(orphans) == 1
    assert orphans[0]["execution_id"] == "exec_boom"

    # Next cycle: idempotent — no duplicate orphan, no second quarantine.
    notify_ok = AsyncMock(return_value={"id": "n"})
    monkeypatch.setattr("signals.notify.notify", notify_ok)
    assert await sweep_unmanaged_terminal_fills(db_path=db) == 0
    assert len(fa.list_open_orphans(db_path=db)) == 1
    # The per-row flush is the recovery path that pages it.
    assert await flush_fill_attribution_orphans(db_path=db) == 1
    assert fa.list_open_orphans(db_path=db) == []


@pytest.mark.asyncio
async def test_sweep_check_then_record_race_is_degraded_safe(
    monkeypatch, tmp_path: Path
) -> None:
    """Adversarial F5b race shape: attribution opens the managed row BETWEEN the
    sweep's is_execution_managed check and record_orphan. A false orphan is created
    and paged once (deny-by-default: a spurious page beats a silent orphan), but the
    next cycle's orphan-exists idempotency prevents any re-page — pin that degraded-
    safe behavior."""
    db = _db(tmp_path)
    _seed_terminal_buy(db, execution_id="exec_race", status="filled", filled_qty="7")

    def racy_is_managed(execution_id, **kwargs):
        # Simulate: the check misses the row, which lands immediately after.
        record_open_position(
            account_id="acct_x", symbol="AAPL", strategy_id="dip_buy", qty="7",
            exit_mode="propose", params={}, entry_execution_id=execution_id,
            db_path=db,
        )
        return False

    monkeypatch.setattr("db.managed_positions.is_execution_managed", racy_is_managed)
    notify = AsyncMock(return_value={"id": "n"})
    monkeypatch.setattr("signals.notify.notify", notify)

    # The race produces ONE false orphan + ONE page (accepted degraded mode).
    assert await sweep_unmanaged_terminal_fills(db_path=db) == 1
    notify.assert_awaited_once()
    assert fa.orphan_exists_for_execution("exec_race", db_path=db) is True

    # Next cycle, race gone (real lookup back): the orphan-exists screen keeps the
    # sweep idempotent — no second orphan, NO re-page.
    monkeypatch.undo()
    notify2 = AsyncMock(return_value={"id": "n2"})
    monkeypatch.setattr("signals.notify.notify", notify2)
    assert await sweep_unmanaged_terminal_fills(db_path=db) == 0
    notify2.assert_not_awaited()


@pytest.mark.asyncio
async def test_flush_pages_critical_and_marks_notified(monkeypatch, tmp_path: Path) -> None:
    db = _db(tmp_path)
    fa.record_orphan(
        execution_id="exec_orphan", reason="write_failed", symbol="AAPL",
        account_id="acct_x", strategy_id="dip_buy", db_path=db,
    )

    notify = AsyncMock(return_value={"id": "notif_orphan"})
    monkeypatch.setattr("signals.notify.notify", notify)

    paged = await flush_fill_attribution_orphans(db_path=db)

    assert paged == 1
    notify.assert_awaited_once()
    call = notify.await_args.kwargs
    assert call["kind"] == "attribution_orphan"
    assert call["severity"] == "critical"
    assert call["push"] is True
    assert call["ref_id"] == "exec_orphan"
    assert "AAPL" in call["title"]

    # Marked notified — a second pass does not re-page.
    assert fa.list_open_orphans(db_path=db) == []
    notify.reset_mock()
    assert await flush_fill_attribution_orphans(db_path=db) == 0
    notify.assert_not_awaited()


@pytest.mark.asyncio
async def test_flush_keeps_orphan_open_when_notify_persist_fails(
    monkeypatch, tmp_path: Path
) -> None:
    """Adversarial-review MAJOR: notify() swallows persist failures and returns {}.

    If the inbox row never landed, the orphan must stay 'open' so it re-pages on
    the next tick — a duplicate page beats a permanently lost one.
    """
    db = _db(tmp_path)
    fa.record_orphan(
        execution_id="exec_lost_page", reason="write_failed", symbol="NVDA",
        account_id="acct_x", strategy_id="dip_buy", db_path=db,
    )

    notify = AsyncMock(return_value={})  # persist failed inside notify()
    monkeypatch.setattr("signals.notify.notify", notify)

    paged = await flush_fill_attribution_orphans(db_path=db)

    assert paged == 0
    notify.assert_awaited_once()
    # Still open — re-pages next tick once notify() can persist again.
    assert len(fa.list_open_orphans(db_path=db)) == 1

    notify_ok = AsyncMock(return_value={"id": "notif_ok"})
    monkeypatch.setattr("signals.notify.notify", notify_ok)
    assert await flush_fill_attribution_orphans(db_path=db) == 1
    assert fa.list_open_orphans(db_path=db) == []
