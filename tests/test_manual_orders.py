"""Manual desk verbs — place/cancel/replace refusal fences + the TIF seam.

Hermetic: chokepoint mocked where a broker would be reached; every refusal
fence asserted against the real row shapes.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import trading.manual_orders as manual
import trading.proposals as proposals_mod
from db.order_executions import create_execution_record, get_execution
from trading.order_proposal import (
    build_order_proposal_from_args,
    order_intent_from_fields,
)


@pytest.fixture
def db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    db_path = tmp_path / "t.db"
    from db import base as db_base

    monkeypatch.setattr(db_base, "DEFAULT_DB_PATH", db_path)
    return db_path


def _fake_adapter(**over) -> SimpleNamespace:
    adapter = SimpleNamespace(
        is_paper_endpoint=True,
        acancel_order=AsyncMock(),
        aget_order=AsyncMock(),
    )
    for k, v in over.items():
        setattr(adapter, k, v)
    return adapter


def _seed_working_limit(db: Path, **over) -> str:
    import uuid

    fields = {
        "execution_id": f"exec-{uuid.uuid4().hex[:8]}",
        "proposal_id": "prop-1",
        "session_id": None,
        "symbol": "TSLA",
        "side": "buy",
        "qty": Decimal("5"),
        "order_type": "limit",
        "limit_price": Decimal("100.00"),
        "execution_mode": "paper",
        "status": "pending",
        "broker": "alpaca",
        "account_id": "acct_x",
    }
    fields.update(over)
    row = create_execution_record(db_path=db, **fields)
    from db.order_executions import update_execution_status

    update_execution_status(
        row["id"], "submitted", broker_order_id="brk-1", db_path=db
    )
    return row["id"]


def _order(status: str, filled: str = "0") -> SimpleNamespace:
    return SimpleNamespace(status=status, filled_qty=filled)


# ---------------------------------------------------------------------------
# TIF / extended-hours seam (shared builder)
# ---------------------------------------------------------------------------


def test_builder_accepts_gtc_and_intent_carries_it(db: Path) -> None:
    built = build_order_proposal_from_args(
        {
            "symbol": "TSLA",
            "side": "buy",
            "qty": 5,
            "order_type": "limit",
            "limit_price": 100,
            "time_in_force": "gtc",
        },
        db_path=db,
    )
    assert "error" not in built
    assert built["intent_fields"]["time_in_force"] == "gtc"
    intent = order_intent_from_fields(
        built["intent_fields"], proposal_id="p", session_id=None, db_path=db
    )
    assert intent.time_in_force == "gtc"
    assert intent.book_entry is False


def test_builder_default_day_stays_out_of_canonical_fields(db: Path) -> None:
    built = build_order_proposal_from_args(
        {"symbol": "TSLA", "side": "buy", "qty": 5, "order_type": "market"},
        db_path=db,
    )
    # Hash stability: existing callers' canonical payloads must not change.
    assert "time_in_force" not in built["intent_fields"]
    assert "extended_hours" not in built["intent_fields"]
    intent = order_intent_from_fields(
        built["intent_fields"], proposal_id="p", session_id=None, db_path=db
    )
    assert intent.time_in_force == "day"
    assert intent.extended_hours is False


def test_builder_rejects_bad_tif_and_ext_on_market(db: Path) -> None:
    assert "error" in build_order_proposal_from_args(
        {"symbol": "T", "side": "buy", "qty": 1, "order_type": "market", "time_in_force": "ioc"},
        db_path=db,
    )
    assert "error" in build_order_proposal_from_args(
        {"symbol": "T", "side": "buy", "qty": 1, "order_type": "market", "extended_hours": True},
        db_path=db,
    )


def test_builder_ext_hours_limit_carries_through(db: Path) -> None:
    built = build_order_proposal_from_args(
        {
            "symbol": "TSLA",
            "side": "buy",
            "qty": 5,
            "order_type": "limit",
            "limit_price": 100,
            "extended_hours": True,
        },
        db_path=db,
    )
    assert built["intent_fields"]["extended_hours"] is True
    intent = order_intent_from_fields(
        built["intent_fields"], proposal_id="p", session_id=None, db_path=db
    )
    assert intent.extended_hours is True


# ---------------------------------------------------------------------------
# place_manual_order
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_place_invalid_args_refused(db: Path) -> None:
    result = await manual.place_manual_order(
        {"symbol": "", "side": "buy", "qty": 1}, db_path=db
    )
    assert result["status"] == "invalid"


@pytest.mark.asyncio
async def test_place_executes_via_approval_and_never_announces(
    db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    announce = AsyncMock()
    monkeypatch.setattr(proposals_mod, "_announce_proposal", announce)
    submit = AsyncMock(
        return_value=SimpleNamespace(
            execution_id="exec-1", execution_mode="paper", status="submitted",
            broker_order_id="b1", dry_run_log=None,
        )
    )
    monkeypatch.setattr(proposals_mod.ExecutionService, "submit", submit)

    result = await manual.place_manual_order(
        {"symbol": "TSLA", "side": "buy", "qty": 5, "order_type": "market"},
        db_path=db,
    )
    assert result["status"] == "executed"
    assert result["execution_id"] == "exec-1"
    assert result["proposal_id"]
    announce.assert_not_awaited()
    submit.assert_awaited_once()
    intent = submit.await_args.args[0]
    assert intent.symbol == "TSLA"
    assert str(intent.qty) == "5"

    from db.paper_orders import get_proposal

    row = get_proposal(result["proposal_id"], db_path=db)
    assert row["status"] == "executed"


@pytest.mark.asyncio
async def test_place_risk_block_passes_reason_through(
    db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from trading.models import RiskRejectedError

    monkeypatch.setattr(
        proposals_mod.ExecutionService,
        "submit",
        AsyncMock(side_effect=RiskRejectedError("daily order cap reached")),
    )
    result = await manual.place_manual_order(
        {"symbol": "TSLA", "side": "buy", "qty": 5, "order_type": "market"},
        db_path=db,
    )
    assert result["status"] == "blocked"
    assert result["reason"] == "daily order cap reached"


# ---------------------------------------------------------------------------
# cancel_manual_order fences
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cancel_unknown_id(db: Path) -> None:
    assert (await manual.cancel_manual_order("nope", db_path=db))["status"] == "not_found"


@pytest.mark.asyncio
async def test_cancel_refuses_protective_stop(db: Path) -> None:
    exec_id = _seed_working_limit(db, proposal_id="stop|prop-9", order_type="stop")
    result = await manual.cancel_manual_order(exec_id, db_path=db)
    assert result["status"] == "rejected"
    assert "stop guard" in result["reason"]


@pytest.mark.asyncio
async def test_cancel_refuses_any_stop_row(db: Path) -> None:
    exec_id = _seed_working_limit(db, order_type="stop")
    result = await manual.cancel_manual_order(exec_id, db_path=db)
    assert result["status"] == "rejected"


@pytest.mark.asyncio
async def test_cancel_refuses_book_entry(db: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    exec_id = _seed_working_limit(db)
    monkeypatch.setattr(manual, "_is_book_order", lambda row, db_path: True)
    result = await manual.cancel_manual_order(exec_id, db_path=db)
    assert result["status"] == "rejected"
    assert "book" in result["reason"]


@pytest.mark.asyncio
async def test_cancel_refuses_non_working(db: Path) -> None:
    exec_id = _seed_working_limit(db)
    from db.order_executions import update_execution_status

    update_execution_status(exec_id, "filled", db_path=db)
    assert (await manual.cancel_manual_order(exec_id, db_path=db))["status"] == "not_working"


@pytest.mark.asyncio
async def test_cancel_respects_verb_gate(db: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    exec_id = _seed_working_limit(db)
    monkeypatch.setattr(manual, "_is_book_order", lambda row, db_path: False)
    monkeypatch.setattr(manual, "_verb_gate", lambda probe: (None, "kill switch active"))
    result = await manual.cancel_manual_order(exec_id, db_path=db)
    assert result["status"] == "rejected"
    assert result["reason"] == "kill switch active"


@pytest.mark.asyncio
async def test_cancel_sends_and_leaves_row_submitted(
    db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    exec_id = _seed_working_limit(db)
    adapter = _fake_adapter()
    monkeypatch.setattr(manual, "_is_book_order", lambda row, db_path: False)
    monkeypatch.setattr(manual, "_verb_gate", lambda probe: (adapter, None))
    result = await manual.cancel_manual_order(exec_id, db_path=db)
    assert result["status"] == "cancel_sent"
    adapter.acancel_order.assert_awaited_once_with("brk-1")
    # Reconcile is the fill tracker's job — the row must stay working.
    assert get_execution(exec_id, db_path=db)["status"] == "submitted"


# ---------------------------------------------------------------------------
# replace_manual_order
# ---------------------------------------------------------------------------


def _patch_replace_env(monkeypatch, adapter, *, refreshed: bool = True) -> AsyncMock:
    monkeypatch.setattr(manual, "_is_book_order", lambda row, db_path: False)
    monkeypatch.setattr(manual, "_verb_gate", lambda probe: (adapter, None))
    refresh = AsyncMock(return_value=refreshed)
    monkeypatch.setattr(manual, "_refresh_snapshot", refresh)
    return refresh


@pytest.mark.asyncio
async def test_replace_refuses_market_order(db: Path) -> None:
    exec_id = _seed_working_limit(db, order_type="market", limit_price=None)
    result = await manual.replace_manual_order(
        exec_id, limit_price=Decimal("101"), db_path=db
    )
    assert result["status"] == "rejected"


@pytest.mark.asyncio
async def test_replace_fill_race_reports_and_never_resubmits(
    db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    exec_id = _seed_working_limit(db)
    adapter = _fake_adapter(aget_order=AsyncMock(return_value=_order("filled", "5")))
    _patch_replace_env(monkeypatch, adapter)
    place = AsyncMock()
    monkeypatch.setattr(manual, "place_manual_order", place)
    result = await manual.replace_manual_order(
        exec_id, limit_price=Decimal("101"), db_path=db
    )
    assert result["status"] == "filled_before_replace"
    place.assert_not_awaited()
    # Row untouched: the fill tracker records the fill.
    assert get_execution(exec_id, db_path=db)["status"] == "submitted"


@pytest.mark.asyncio
async def test_replace_partial_fill_no_resubmit(
    db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    exec_id = _seed_working_limit(db)
    adapter = _fake_adapter(aget_order=AsyncMock(return_value=_order("canceled", "2")))
    _patch_replace_env(monkeypatch, adapter)
    place = AsyncMock()
    monkeypatch.setattr(manual, "place_manual_order", place)
    result = await manual.replace_manual_order(
        exec_id, limit_price=Decimal("101"), db_path=db
    )
    assert result["status"] == "partial_fill_no_replace"
    place.assert_not_awaited()
    assert get_execution(exec_id, db_path=db)["status"] == "submitted"


@pytest.mark.asyncio
async def test_replace_clean_cancel_resubmits_at_new_price(
    db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    exec_id = _seed_working_limit(db)
    adapter = _fake_adapter(aget_order=AsyncMock(return_value=_order("canceled", "0")))
    refresh = _patch_replace_env(monkeypatch, adapter)
    place = AsyncMock(return_value={"status": "executed", "execution_id": "exec-2"})
    monkeypatch.setattr(manual, "place_manual_order", place)
    result = await manual.replace_manual_order(
        exec_id, limit_price=Decimal("101.50"), db_path=db
    )
    assert result["status"] == "executed"
    assert result["replaced_execution_id"] == exec_id
    # Old row terminalized BEFORE the resubmit so inflight sums release.
    assert get_execution(exec_id, db_path=db)["status"] == "canceled"
    # The snapshot re-poll drops the dead broker order — without it the sell
    # gate still reserves the cancelled order's qty (review BLOCKER).
    refresh.assert_awaited_once()
    assert refresh.await_args.kwargs["gone_order_ids"] == frozenset({"brk-1"})
    args = place.await_args.args[0]
    assert args["limit_price"] == "101.50"
    assert args["qty"] == "5"
    assert args["side"] == "buy"
    assert args["account_id"] == "acct_x"
    # Terms preserved: defaults when the minting proposal is gone.
    assert args["time_in_force"] == "day"
    assert args["extended_hours"] is False


@pytest.mark.asyncio
async def test_replace_refuses_when_snapshot_refresh_fails(
    db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    exec_id = _seed_working_limit(db)
    adapter = _fake_adapter(aget_order=AsyncMock(return_value=_order("canceled", "0")))
    _patch_replace_env(monkeypatch, adapter, refreshed=False)
    place = AsyncMock()
    monkeypatch.setattr(manual, "place_manual_order", place)
    result = await manual.replace_manual_order(
        exec_id, limit_price=Decimal("101"), db_path=db
    )
    assert result["status"] == "cancelled_no_replace"
    place.assert_not_awaited()


@pytest.mark.asyncio
async def test_replace_done_for_day_is_not_a_clean_cancel(
    db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A GTC order parked at done_for_day reworks next session — treating it
    # as cancelled would double the order (review BLOCKER).
    exec_id = _seed_working_limit(db)
    adapter = _fake_adapter(
        aget_order=AsyncMock(return_value=_order("done_for_day", "0"))
    )
    _patch_replace_env(monkeypatch, adapter)
    monkeypatch.setattr(manual, "_CANCEL_POLL_ATTEMPTS", 2)
    monkeypatch.setattr(manual, "_CANCEL_POLL_DELAY_S", 0.0)
    place = AsyncMock()
    monkeypatch.setattr(manual, "place_manual_order", place)
    result = await manual.replace_manual_order(
        exec_id, limit_price=Decimal("101"), db_path=db
    )
    assert result["status"] == "cancel_pending"
    place.assert_not_awaited()
    assert get_execution(exec_id, db_path=db)["status"] == "submitted"


@pytest.mark.asyncio
async def test_replace_recovers_gtc_terms_from_proposal(
    db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Seed a real proposal carrying gtc+ext so the resubmit keeps the order's
    # character (a GTC coming back as day dies at the close — review MAJOR).
    from db.paper_orders import create_pending_proposal

    prow = create_pending_proposal(
        proposal={"symbol": "TSLA", "time_in_force": "gtc", "extended_hours": True},
        intent_fields={"symbol": "TSLA", "side": "buy", "qty": "5",
                       "order_type": "limit", "time_in_force": "gtc",
                       "extended_hours": True},
        session_id=None,
        ttl_seconds=60,
        summary="t",
        db_path=db,
    )
    exec_id = _seed_working_limit(db, proposal_id=prow["id"])
    adapter = _fake_adapter(aget_order=AsyncMock(return_value=_order("canceled", "0")))
    _patch_replace_env(monkeypatch, adapter)
    place = AsyncMock(return_value={"status": "executed"})
    monkeypatch.setattr(manual, "place_manual_order", place)
    await manual.replace_manual_order(exec_id, limit_price=Decimal("101"), db_path=db)
    args = place.await_args.args[0]
    assert args["time_in_force"] == "gtc"
    assert args["extended_hours"] is True


@pytest.mark.asyncio
async def test_replace_nan_limit_refused(db: Path) -> None:
    exec_id = _seed_working_limit(db)
    result = await manual.replace_manual_order(
        exec_id, limit_price=Decimal("NaN"), db_path=db
    )
    assert result["status"] == "invalid"


def test_builder_rejects_non_finite_numbers(db: Path) -> None:
    for bad in ("NaN", "Infinity"):
        assert "error" in build_order_proposal_from_args(
            {"symbol": "T", "side": "buy", "qty": bad, "order_type": "market"},
            db_path=db,
        )
        assert "error" in build_order_proposal_from_args(
            {"symbol": "T", "side": "buy", "qty": 1, "order_type": "limit",
             "limit_price": bad},
            db_path=db,
        )


@pytest.mark.asyncio
async def test_place_idempotency_replays_first_outcome(
    db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manual._recent_requests.clear()
    submit = AsyncMock(
        return_value=SimpleNamespace(
            execution_id="exec-1", execution_mode="paper", status="submitted",
            broker_order_id="b1", dry_run_log=None,
        )
    )
    monkeypatch.setattr(proposals_mod.ExecutionService, "submit", submit)
    args = {"symbol": "TSLA", "side": "buy", "qty": 5, "order_type": "market"}
    first = await manual.place_manual_order(args, request_id="req-1", db_path=db)
    second = await manual.place_manual_order(args, request_id="req-1", db_path=db)
    assert first["status"] == "executed"
    assert second["idempotent_replay"] is True
    assert second["execution_id"] == first["execution_id"]
    submit.assert_awaited_once()


# ---------------------------------------------------------------------------
# close_manual_position (flatten)
# ---------------------------------------------------------------------------


def _seed_flatten_env(db: Path, monkeypatch, *, open_orders, positions=None):
    from brokers.base import Account, Position
    from db import sqlite as sqlite_mod
    from db.multi_account import migrate_multi_account
    from db.trades import create_trade_account

    account = create_trade_account("paper", "alpaca", db_path=db)
    account_id = account["id"]
    migrate_multi_account(db_path=db)
    import sqlite3

    with sqlite3.connect(db) as conn:
        conn.execute(
            "UPDATE trade_accounts SET execution_capable = 1, enabled = 1, "
            "is_default = 1 WHERE id = ?",
            (account_id,),
        )
    acct = Account(
        id="PAX", status="ACTIVE", cash=50_000.0, buying_power=100_000.0,
        portfolio_value=100_000.0,
    )
    positions = positions if positions is not None else [
        Position(symbol="TSLA", qty=10.0, side="long", market_value=1000.0,
                 unrealized_pl=0.0, avg_entry_price=95.0, current_price=100.0)
    ]
    sqlite_mod.save_snapshot(
        acct, positions, open_orders, broker="alpaca",
        account_id=account_id, db_path=db,
    )
    return account_id


class _SnapOrder:
    """Minimal broker-order shape for save_snapshot."""

    def __init__(self, order_id, symbol, side, order_type, qty=5.0,
                 limit_price=100.0):
        self.id = order_id
        self.symbol = symbol
        self.side = side
        self.order_type = order_type
        self.qty = qty
        self.filled_qty = 0.0
        self.status = "new"
        self.limit_price = limit_price
        self.stop_price = None
        self.time_in_force = "day"


@pytest.mark.asyncio
async def test_flatten_never_cancels_foreign_or_stop_family(
    db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Hand-placed orders (no execution row) and the stop/trailing family are
    untouchable; only OUR working limit is cancelled (review BLOCKER)."""
    ours = _seed_working_limit(db, symbol="TSLA")
    ours_broker_id = "brk-1"
    open_orders = [
        _SnapOrder(ours_broker_id, "TSLA", "buy", "limit"),
        _SnapOrder("hand-1", "TSLA", "sell", "trailing_stop"),
        _SnapOrder("hand-2", "TSLA", "sell", "stop_limit"),
        _SnapOrder("foreign-1", "TSLA", "sell", "limit"),
    ]
    account_id = _seed_flatten_env(db, monkeypatch, open_orders=open_orders)
    import sqlite3

    with sqlite3.connect(db) as conn:
        conn.execute(
            "UPDATE order_executions SET account_id = ? WHERE id = ?",
            (account_id, ours),
        )
    adapter = _fake_adapter(
        aget_order=AsyncMock(return_value=_order("canceled", "0"))
    )
    monkeypatch.setattr(manual, "_is_book_order", lambda row, db_path: False)
    monkeypatch.setattr(manual, "_verb_gate", lambda probe: (adapter, None))
    refresh = AsyncMock(return_value=True)
    monkeypatch.setattr(manual, "_refresh_snapshot", refresh)
    place = AsyncMock(return_value={"status": "executed", "execution_id": "exec-x"})
    monkeypatch.setattr(manual, "place_manual_order", place)

    result = await manual.close_manual_position(
        "TSLA", account_id=account_id, db_path=db
    )
    assert result["status"] == "executed"
    cancelled_ids = {c.args[0] for c in adapter.acancel_order.await_args_list}
    assert cancelled_ids == {ours_broker_id}
    assert result["cancelled_orders"] == [ours_broker_id]
    assert {u["order_id"] for u in result["untouched_orders"]} == {"foreign-1"}
    refresh.assert_awaited_once()
    close_args = place.await_args.args[0]
    assert close_args["side"] == "sell"
    assert close_args["qty"] == "10"


@pytest.mark.asyncio
async def test_flatten_refuses_options_and_incapable_accounts(
    db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert (
        await manual.close_manual_position(
            "TSLA260918C00400000", account_id=None, db_path=db
        )
    )["status"] == "invalid"

    from db.multi_account import migrate_multi_account
    from db.trades import create_trade_account

    account = create_trade_account("frozen", "alpaca", db_path=db)
    migrate_multi_account(db_path=db)
    import sqlite3

    with sqlite3.connect(db) as conn:
        conn.execute(
            "UPDATE trade_accounts SET execution_capable = 0, enabled = 1 "
            "WHERE id = ?",
            (account["id"],),
        )
    result = await manual.close_manual_position(
        "TSLA", account_id=account["id"], db_path=db
    )
    assert result["status"] == "invalid"
    assert "execution-capable" in result["reason"]


@pytest.mark.asyncio
async def test_flatten_no_position_refuses_before_any_cancel(
    db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    account_id = _seed_flatten_env(
        db, monkeypatch,
        open_orders=[_SnapOrder("x-1", "TSLA", "buy", "limit")],
        positions=[],
    )
    adapter = _fake_adapter()
    monkeypatch.setattr(manual, "_verb_gate", lambda probe: (adapter, None))
    result = await manual.close_manual_position(
        "TSLA", account_id=account_id, db_path=db
    )
    assert result["status"] == "nothing_to_close"
    adapter.acancel_order.assert_not_awaited()


@pytest.mark.asyncio
async def test_replace_cancel_pending_leaves_everything(
    db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    exec_id = _seed_working_limit(db)
    adapter = _fake_adapter(
        aget_order=AsyncMock(return_value=_order("pending_cancel", "0"))
    )
    _patch_replace_env(monkeypatch, adapter)
    monkeypatch.setattr(manual, "_CANCEL_POLL_ATTEMPTS", 2)
    monkeypatch.setattr(manual, "_CANCEL_POLL_DELAY_S", 0.0)
    place = AsyncMock()
    monkeypatch.setattr(manual, "place_manual_order", place)
    result = await manual.replace_manual_order(
        exec_id, limit_price=Decimal("101"), db_path=db
    )
    assert result["status"] == "cancel_pending"
    place.assert_not_awaited()
    assert get_execution(exec_id, db_path=db)["status"] == "submitted"
