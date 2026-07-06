"""safety-M1: a strategy BUY that fills but can't be attributed must never be a
silent orphan — it is retried, then durably quarantined + paged (critical)."""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from db import fill_attribution as fa
from db.managed_positions import list_open_positions, record_open_position
from db.multi_account import migrate_multi_account
from db.paper_orders import init_paper_order_tables
from db.sqlite import init_db
from db.strategies import init_strategy_tables, seed_default_strategies
from trading import position_ledger
from trading.position_ledger import flush_fill_attribution_orphans, record_strategy_fill

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
