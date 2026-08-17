"""Book order-management verbs: move_stop / cancel_entry / trim.

The analyst asks in its decision block, the founder taps, and the desk acts on
what is resting at the broker at that moment. These pin the chokepoint verbs
(trading/execution_service.move_protective_stops / cancel_book_entries), the
proposal round-trip through approve_proposal, and every way a move must NOT
leave the position bare.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from test_stop_release import (
    _ACCOUNT,
    _env,
    _record_book_entry,
    _record_stop_execution,
    _stop_order,
    _StubBroker,
)

from brokers.alpaca import AlpacaError
from brokers.base import Order
from config import settings as settings_mod
from db import order_executions as order_executions_mod
from db import paper_orders as paper_orders_mod
from db import sqlite as sqlite_mod
from trading import book_orders
from trading.execution_service import cancel_book_entries, move_protective_stops
from trading.proposals import approve_proposal


def _entry_order(order_id: str = "broker-entry-gtc") -> Order:
    return Order(
        id=order_id,
        symbol="AAPL",
        qty=10.0,
        side="buy",
        order_type="limit",
        status="new",
        filled_qty=0.0,
        limit_price=140.0,
        time_in_force="gtc",
    )


def _stops_placed(broker: _StubBroker) -> list:
    return [req for req in broker.placed if req.order_type == "stop"]


async def _move(db: Path, stop_price: str, *entry_ids: str, **kw):
    return await move_protective_stops(
        "AAPL", broker="alpaca", account_id=None, stop_price=Decimal(stop_price),
        proposal_id="paper_move_1", entry_proposal_ids=frozenset(entry_ids), db_path=db, **kw,
    )


# --- move_stop --------------------------------------------------------------------


@pytest.mark.asyncio
async def test_move_stop_cancels_the_old_and_places_the_new_under_the_entry_id(
    monkeypatch, tmp_path: Path
) -> None:
    db = tmp_path / "exec.db"
    broker = _StubBroker([_stop_order()])
    _env(monkeypatch, db, broker)
    entry_pid = _record_book_entry(db)
    _record_stop_execution(db, proposal_id=f"stop|{entry_pid}")

    out = await _move(db, "145", entry_pid)

    assert out["status"] == "moved", out
    assert broker.canceled == ["broker-stop-1"]
    stops = _stops_placed(broker)
    assert len(stops) == 1
    assert stops[0].stop_price == "145"
    assert stops[0].qty == "50"
    assert stops[0].time_in_force == "gtc"
    # Old row dead, new row live under the SAME stop|<entry> id — the stop guard
    # still sees the entry as protected.
    assert order_executions_mod.get_execution("exec_stop_1", db_path=db)["status"] == "canceled"
    latest = order_executions_mod.get_execution_by_proposal(f"stop|{entry_pid}", db_path=db)
    assert latest["status"] == "submitted"
    assert latest["id"] != "exec_stop_1"
    # And the entry's stored plan follows, so a guard retry never re-arms 140.
    stored = paper_orders_mod.get_proposal(entry_pid, db_path=db)["proposal"]
    assert stored["stop_price"] == "145"


@pytest.mark.asyncio
async def test_move_stop_at_or_above_market_is_refused_before_anything_is_released(
    monkeypatch, tmp_path: Path
) -> None:
    db = tmp_path / "exec.db"
    broker = _StubBroker([_stop_order()])
    _env(monkeypatch, db, broker)
    entry_pid = _record_book_entry(db)
    _record_stop_execution(db, proposal_id=f"stop|{entry_pid}")

    out = await _move(db, "150", entry_pid)

    assert out["status"] == "rejected"
    assert broker.canceled == []
    assert broker.placed == []
    assert order_executions_mod.get_execution("exec_stop_1", db_path=db)["status"] == "submitted"


@pytest.mark.asyncio
async def test_move_stop_refuses_when_the_stop_covers_more_than_is_held(
    monkeypatch, tmp_path: Path
) -> None:
    """A 50-share stop over a 30-share position (founder trimmed at the broker):
    cancelling it could not be undone — RiskEngine would refuse the restore for
    the same reason it refuses the new one (adversarial review, reproduced)."""
    db = tmp_path / "exec.db"
    broker = _StubBroker([_stop_order()], position_qty=30.0)
    _env(monkeypatch, db, broker)
    entry_pid = _record_book_entry(db)
    _record_stop_execution(db, proposal_id=f"stop|{entry_pid}")

    out = await _move(db, "145", entry_pid)

    assert out["status"] == "rejected"
    assert "covers 50" in out["reason"]
    assert broker.canceled == [] and broker.placed == []
    assert order_executions_mod.get_execution("exec_stop_1", db_path=db)["status"] == "submitted"


@pytest.mark.asyncio
async def test_move_stop_gate_counts_every_resting_stop_not_just_the_targeted_ones(
    monkeypatch, tmp_path: Path
) -> None:
    """100 held, targeted stop 60 + untargeted stop 50: releasing the 60 leaves a
    50-share budget, so the re-place would silently cover 50 and drop 10 (review
    B NEW-1, reproduced). The gate must see the aggregate and refuse up front."""
    db = tmp_path / "exec.db"
    broker = _StubBroker(
        [_stop_order("broker-stop-1", qty=60.0), _stop_order("broker-stop-2", qty=50.0, stop_price=138.0)],
        position_qty=100.0,
    )
    _env(monkeypatch, db, broker)
    entry_pid = _record_book_entry(db)
    _record_stop_execution(db, qty="60", proposal_id=f"stop|{entry_pid}")
    _record_stop_execution(
        db, execution_id="exec_stop_2", broker_order_id="broker-stop-2", qty="50",
        proposal_id="stop|other_entry",
    )

    out = await _move(db, "145", entry_pid)

    assert out["status"] == "rejected", out
    assert broker.canceled == [] and broker.placed == []


@pytest.mark.asyncio
async def test_move_stop_only_touches_the_entries_it_was_minted_for(
    monkeypatch, tmp_path: Path
) -> None:
    db = tmp_path / "exec.db"
    broker = _StubBroker(
        [_stop_order("broker-stop-1", qty=25.0), _stop_order("broker-stop-2", qty=25.0)]
    )
    _env(monkeypatch, db, broker)
    entry_pid = _record_book_entry(db)
    _record_stop_execution(db, qty="25", proposal_id=f"stop|{entry_pid}")
    # A second entry's stop, approved AFTER the move was proposed.
    _record_stop_execution(
        db, execution_id="exec_stop_2", broker_order_id="broker-stop-2", qty="25",
        proposal_id="stop|later_entry",
    )

    out = await _move(db, "145", entry_pid)

    assert out["status"] == "moved", out
    assert broker.canceled == ["broker-stop-1"]
    assert [s.qty for s in _stops_placed(broker)] == ["25"]
    assert order_executions_mod.get_execution("exec_stop_2", db_path=db)["status"] == "submitted"


@pytest.mark.asyncio
async def test_move_stop_restores_when_the_market_reaches_the_new_trigger_mid_flight(
    monkeypatch, tmp_path: Path
) -> None:
    db = tmp_path / "exec.db"
    broker = _StubBroker([_stop_order()])
    _env(monkeypatch, db, broker)
    entry_pid = _record_book_entry(db)
    _record_stop_execution(db, proposal_id=f"stop|{entry_pid}")

    real_fetch = broker.afetch_snapshot
    calls = {"n": 0}

    async def _drops_after_release():
        calls["n"] += 1
        account, positions, orders = await real_fetch()
        if calls["n"] >= 2:  # the re-poll after the cancel: price fell to 144
            positions = [type(positions[0])(**{**positions[0].__dict__, "current_price": 144.0})]
        return account, positions, orders

    broker.afetch_snapshot = _drops_after_release  # type: ignore[method-assign]

    out = await _move(db, "145", entry_pid)

    assert out["status"] == "failed", out
    assert out["restored"] and not out["lost"]
    assert [s.stop_price for s in _stops_placed(broker)] == ["140.0"]


@pytest.mark.asyncio
async def test_move_stop_puts_the_original_back_when_the_new_stop_is_refused(
    monkeypatch, tmp_path: Path
) -> None:
    db = tmp_path / "exec.db"
    broker = _StubBroker([_stop_order()])
    _env(monkeypatch, db, broker)
    entry_pid = _record_book_entry(db)
    _record_stop_execution(db, proposal_id=f"stop|{entry_pid}")

    real_place = broker.aplace_order

    async def _refuse_new_only(req):
        if req.order_type == "stop" and req.stop_price == "145":
            raise AlpacaError("rejected")
        return await real_place(req)

    broker.aplace_order = _refuse_new_only  # type: ignore[method-assign]

    out = await _move(db, "145", entry_pid)

    assert out["status"] == "failed"
    assert out["restored"] and not out["lost"]
    stops = _stops_placed(broker)
    assert [s.stop_price for s in stops] == ["140.0"]
    assert stops[0].qty == "50"


@pytest.mark.asyncio
async def test_move_stop_pages_when_neither_new_nor_old_can_be_placed(
    monkeypatch, tmp_path: Path
) -> None:
    db = tmp_path / "exec.db"
    broker = _StubBroker([_stop_order()])
    notify = _env(monkeypatch, db, broker)
    entry_pid = _record_book_entry(db)
    _record_stop_execution(db, proposal_id=f"stop|{entry_pid}")

    async def _refuse_all(req):
        raise AlpacaError("rejected")

    broker.aplace_order = _refuse_all  # type: ignore[method-assign]

    out = await _move(db, "145", entry_pid)

    assert out["status"] == "failed"
    assert out["lost"] and not out["moved"] and not out["restored"]
    pages = [c for c in notify.await_args_list if c.kwargs.get("kind") == "risk_block"]
    assert len(pages) == 1
    assert "UNPROTECTED" in pages[0].kwargs["title"]
    assert pages[0].kwargs["push"] is True
    # The stop row is 'error', not 'canceled': the guard sees the entry as
    # unprotected again and re-arms the STORED (old) price on its next pass.
    assert order_executions_mod.get_execution("exec_stop_1", db_path=db)["status"] == "error"
    assert paper_orders_mod.list_book_entries_needing_stop(db_path=db)[0]["id"] == entry_pid
    assert paper_orders_mod.get_proposal(entry_pid, db_path=db)["proposal"]["stop_price"] == 140.0


@pytest.mark.asyncio
async def test_move_stop_with_nothing_resting_touches_nothing(
    monkeypatch, tmp_path: Path
) -> None:
    db = tmp_path / "exec.db"
    broker = _StubBroker([])
    _env(monkeypatch, db, broker)

    out = await _move(db, "145", "some_entry")
    assert out["status"] == "nothing_to_move"
    assert broker.placed == [] and broker.canceled == []


@pytest.mark.asyncio
async def test_move_stop_is_paper_only(monkeypatch, tmp_path: Path) -> None:
    db = tmp_path / "exec.db"
    broker = _StubBroker([_stop_order()])
    _env(monkeypatch, db, broker, execution_mode="dry_run")
    _record_stop_execution(db)

    out = await _move(db, "145", "book_entry_1")
    assert out["status"] == "rejected"
    assert broker.canceled == []


# --- cancel_entry -----------------------------------------------------------------


def _record_open_entry(db: Path, *, source: str, execution_id: str, broker_order_id: str) -> str:
    import sqlite3

    row = paper_orders_mod.record_paper_order_proposal(
        proposal={"symbol": "AAPL", "side": "buy", "qty": 10, "stop_price": 130.0},
        status="executed",
        db_path=db,
    )
    conn = sqlite3.connect(db)
    conn.execute(
        "UPDATE paper_order_proposals SET source = ? WHERE id = ?", (source, row["id"])
    )
    conn.commit()
    conn.close()
    order_executions_mod.create_execution_record(
        execution_id=execution_id,
        proposal_id=row["id"],
        session_id=None,
        symbol="AAPL",
        side="buy",
        qty=Decimal("10"),
        order_type="limit",
        limit_price=Decimal("140"),
        execution_mode="paper",
        status="submitted",
        broker="alpaca",
        broker_order_id=broker_order_id,
        db_path=db,
    )
    return str(row["id"])


@pytest.mark.asyncio
async def test_cancel_entry_pulls_only_the_books_working_entries(
    monkeypatch, tmp_path: Path
) -> None:
    db = tmp_path / "exec.db"
    broker = _StubBroker([_entry_order("broker-entry-book"), _entry_order("broker-entry-human")])
    _env(monkeypatch, db, broker)
    _record_open_entry(
        db, source="book_fable", execution_id="exec_book", broker_order_id="broker-entry-book"
    )
    _record_open_entry(
        db, source="chat", execution_id="exec_human", broker_order_id="broker-entry-human"
    )

    out = await cancel_book_entries(
        "AAPL", broker="alpaca", account_id=None, proposal_id="paper_cancel_1",
        execution_ids=frozenset({"exec_book", "exec_human"}), db_path=db,
    )

    assert out["status"] == "cancelled"
    assert broker.canceled == ["broker-entry-book"]
    # The row is left for the fill tracker's reconcile — no write-ahead here.
    assert order_executions_mod.get_execution("exec_book", db_path=db)["status"] == "submitted"


@pytest.mark.asyncio
async def test_cancel_entry_reports_a_broker_refusal(monkeypatch, tmp_path: Path) -> None:
    db = tmp_path / "exec.db"
    broker = _StubBroker([_entry_order("broker-entry-book")])
    _env(monkeypatch, db, broker)
    _record_open_entry(
        db, source="book_fable", execution_id="exec_book", broker_order_id="broker-entry-book"
    )

    async def _refuse(_id: str) -> None:
        raise AlpacaError("order not cancelable")

    broker.acancel_order = _refuse  # type: ignore[method-assign]
    out = await cancel_book_entries(
        "AAPL", broker="alpaca", account_id=None, proposal_id="paper_cancel_1",
        execution_ids=frozenset({"exec_book"}), db_path=db,
    )
    assert out["status"] == "failed"
    assert out["failed"][0]["execution_id"] == "exec_book"


# --- proposal round-trip: night creation → morning tap ------------------------------


def _book_account(monkeypatch) -> None:
    """Every verb resolves the book's account; the tests run account-less rows."""
    monkeypatch.setattr(
        book_orders, "_resolve_account", lambda _aid, *, db_path: {"id": None, "broker": "alpaca"}
    )
    monkeypatch.setattr(book_orders, "is_kill_switch_active", lambda: False)
    monkeypatch.setattr(
        book_orders, "trading_config",
        lambda: {"agent_proposals_enabled": True, "execution_mode": "paper"},
    )
    monkeypatch.setattr("trading.proposals.notify", AsyncMock(return_value={}))
    monkeypatch.setattr("signals.push.send_proposal_push", AsyncMock(return_value=0))
    monkeypatch.setattr("signals.push.start_proposal_live_activity", AsyncMock(return_value=None))
    monkeypatch.setattr("signals.push.end_proposal_live_activity", AsyncMock(return_value=None))
    monkeypatch.setattr("db.accounts.account_is_execution_capable", lambda *_a, **_k: True)


@pytest.mark.asyncio
async def test_move_stop_proposal_round_trip(monkeypatch, tmp_path: Path) -> None:
    db = tmp_path / "exec.db"
    broker = _StubBroker([_stop_order()])
    _env(monkeypatch, db, broker)
    _book_account(monkeypatch)
    entry_pid = _record_book_entry(db)
    _record_stop_execution(db, proposal_id=f"stop|{entry_pid}")

    created = await book_orders.propose_move_stop(
        symbol="aapl", stop_price=Decimal("145"), account_id="book-acct",
        source="book_fable", reason="lock in the gap", db_path=db,
    )
    assert created["status"] == "proposed", created
    row = paper_orders_mod.get_proposal(created["id"], db_path=db)
    assert row["proposal"]["order_action"] == "move_stop"
    assert row["summary"] == "Move AAPL stop 140.0 → 145 on 50 sh"
    assert row["proposal"]["targets"] == [entry_pid]
    assert row["side"] == "sell" and row["order_type"] == "stop"
    # Nothing touched at creation.
    assert broker.canceled == [] and broker.placed == []

    # A second ask the same night is a duplicate, and an entry on the same
    # name is NOT blocked by it (propose_trade dedup ignores order changes).
    again = await book_orders.propose_move_stop(
        symbol="AAPL", stop_price=Decimal("146"), account_id="book-acct",
        source="book_fable", reason=None, db_path=db,
    )
    assert again["status"] == "duplicate"

    result = await approve_proposal(created["id"], db_path=db)
    assert result["status"] == "executed", result
    assert result["outcome"]["status"] == "moved"
    assert broker.canceled == ["broker-stop-1"]
    assert [s.stop_price for s in _stops_placed(broker)] == ["145"]
    assert paper_orders_mod.get_proposal(created["id"], db_path=db)["status"] == "executed"


@pytest.mark.asyncio
async def test_move_stop_proposal_blocked_when_the_move_cannot_happen(
    monkeypatch, tmp_path: Path
) -> None:
    db = tmp_path / "exec.db"
    broker = _StubBroker([_stop_order()])
    _env(monkeypatch, db, broker)
    _book_account(monkeypatch)
    entry_pid = _record_book_entry(db)
    _record_stop_execution(db, proposal_id=f"stop|{entry_pid}")

    created = await book_orders.propose_move_stop(
        symbol="AAPL", stop_price=Decimal("145"), account_id="book-acct",
        source="book_fable", reason=None, db_path=db,
    )
    # By morning the stop is gone at the broker (it filled overnight) — the tap
    # must not invent a stop on shares that are no longer there.
    broker.book["broker-stop-1"] = Order(
        **{**broker.book["broker-stop-1"].__dict__, "status": "filled", "filled_qty": 50.0}
    )
    result = await approve_proposal(created["id"], db_path=db)
    assert result["status"] == "blocked"
    assert broker.placed == []
    assert paper_orders_mod.get_proposal(created["id"], db_path=db)["status"] == "blocked"


@pytest.mark.asyncio
async def test_move_stop_creation_refuses_a_stop_above_market_and_nothing_to_move(
    monkeypatch, tmp_path: Path
) -> None:
    db = tmp_path / "exec.db"
    broker = _StubBroker([_stop_order()])
    _env(monkeypatch, db, broker)
    _book_account(monkeypatch)

    nothing = await book_orders.propose_move_stop(
        symbol="AAPL", stop_price=Decimal("145"), account_id="book-acct",
        source="book_fable", reason=None, db_path=db,
    )
    assert nothing["status"] == "nothing_to_move"

    entry_pid = _record_book_entry(db)
    _record_stop_execution(db, proposal_id=f"stop|{entry_pid}")
    above = await book_orders.propose_move_stop(
        symbol="AAPL", stop_price=Decimal("155"), account_id="book-acct",
        source="book_fable", reason=None, db_path=db,
    )
    assert above["status"] == "stop_above_market"


@pytest.mark.asyncio
async def test_move_stop_restamps_an_unfilled_entry_instead(monkeypatch, tmp_path: Path) -> None:
    """Entry not filled yet → no stop rests; the stored plan takes the new
    price so the guard arms it when the fill lands."""
    db = tmp_path / "exec.db"
    broker = _StubBroker([_entry_order("broker-entry-book")])
    _env(monkeypatch, db, broker)
    _book_account(monkeypatch)
    entry_pid = _record_open_entry(
        db, source="book_fable", execution_id="exec_book", broker_order_id="broker-entry-book"
    )

    created = await book_orders.propose_move_stop(
        symbol="AAPL", stop_price=Decimal("135"), account_id="book-acct",
        source="book_fable", reason=None, db_path=db,
    )
    assert created["status"] == "proposed", created
    row = paper_orders_mod.get_proposal(created["id"], db_path=db)
    assert row["summary"].endswith("(arms when the entry fills)")
    result = await approve_proposal(created["id"], db_path=db)
    assert result["status"] == "executed", result
    assert result["outcome"]["status"] == "restamped"
    assert result["outcome"]["restamped"] == 1
    assert broker.canceled == [] and broker.placed == []
    assert paper_orders_mod.get_proposal(entry_pid, db_path=db)["proposal"]["stop_price"] == "135"


@pytest.mark.asyncio
async def test_move_stop_on_an_unfilled_entry_is_price_gated_too(monkeypatch, tmp_path: Path) -> None:
    """No stop rests yet, so nothing at the broker refuses a trigger above the
    market — the desk must, or the guard arms a stop that fires on the fill
    (adversarial review, reproduced)."""
    db = tmp_path / "exec.db"
    broker = _StubBroker([_entry_order("broker-entry-book")])
    _env(monkeypatch, db, broker)
    _book_account(monkeypatch)
    _record_open_entry(
        db, source="book_fable", execution_id="exec_book", broker_order_id="broker-entry-book"
    )

    async def _quote(_symbol: str):
        return Decimal("150")

    monkeypatch.setattr(book_orders, "_reference_price", _quote)
    created = await book_orders.propose_move_stop(
        symbol="AAPL", stop_price=Decimal("155"), account_id="book-acct",
        source="book_fable", reason=None, db_path=db,
    )
    assert created["status"] == "stop_above_market"


@pytest.mark.asyncio
async def test_cancel_entry_proposal_round_trip(monkeypatch, tmp_path: Path) -> None:
    db = tmp_path / "exec.db"
    broker = _StubBroker([_entry_order("broker-entry-book")])
    _env(monkeypatch, db, broker)
    _book_account(monkeypatch)
    _record_open_entry(
        db, source="book_fable", execution_id="exec_book", broker_order_id="broker-entry-book"
    )

    created = await book_orders.propose_cancel_entry(
        symbol="AAPL", account_id="book-acct", source="book_fable", reason="thesis broke", db_path=db
    )
    assert created["status"] == "proposed", created
    row = paper_orders_mod.get_proposal(created["id"], db_path=db)
    assert row["summary"] == "Cancel AAPL entry: 10 sh working @ limit 140"
    assert row["side"] == "buy"

    # By morning the founder approved a NEW entry on the same name — the
    # cancel minted last night must not pull it (adversarial review H1).
    broker.book["broker-entry-later"] = _entry_order("broker-entry-later")
    _record_open_entry(
        db, source="book_fable", execution_id="exec_later", broker_order_id="broker-entry-later"
    )
    result = await approve_proposal(created["id"], db_path=db)
    assert result["status"] == "executed", result
    assert broker.canceled == ["broker-entry-book"]


@pytest.mark.asyncio
async def test_trim_is_a_plain_next_open_sell_proposal(monkeypatch, tmp_path: Path) -> None:
    db = tmp_path / "exec.db"
    broker = _StubBroker([_stop_order()])
    _env(monkeypatch, db, broker)
    _book_account(monkeypatch)

    too_many = await book_orders.propose_trim(
        symbol="AAPL", qty=Decimal("60"), account_id="book-acct",
        source="book_fable", reason=None, db_path=db,
    )
    assert too_many["status"] == "invalid"
    fractional = await book_orders.propose_trim(
        symbol="AAPL", qty=Decimal("1.5"), account_id="book-acct",
        source="book_fable", reason=None, db_path=db,
    )
    assert fractional["status"] == "invalid"

    created = await book_orders.propose_trim(
        symbol="AAPL", qty=Decimal("20"), account_id="book-acct",
        source="book_fable", reason="take some off", db_path=db,
    )
    assert created["status"] == "proposed", created
    row = paper_orders_mod.get_proposal(created["id"], db_path=db)
    assert row["side"] == "sell" and float(row["qty"]) == 20.0
    assert row["proposal"]["entry_style"] == "moo"
    assert "next open auction" in row["summary"]
    assert row["proposal"].get("order_action") is None
    again = await book_orders.propose_trim(
        symbol="AAPL", qty=Decimal("20"), account_id="book-acct",
        source="book_fable", reason=None, db_path=db,
    )
    assert again["status"] == "duplicate"


@pytest.mark.asyncio
async def test_stale_trim_is_blocked_not_shorted(monkeypatch, tmp_path: Path) -> None:
    """Trim minted at night with 50 held; the stop takes the position out
    overnight; the founder taps at 9:40. A book sell is reduce-only: the answer
    is blocked, never a short-to-open (both reviewers, reproduced)."""
    db = tmp_path / "exec.db"
    broker = _StubBroker([_stop_order()])
    _env(monkeypatch, db, broker)
    _book_account(monkeypatch)
    trading_cfg = {**settings_mod.DEFAULT_SETTINGS["trading"], "execution_mode": "paper",
                   "allow_short_selling": True}
    monkeypatch.setattr("trading.risk_engine.trading_config", lambda: trading_cfg)
    monkeypatch.setattr("trading.execution_service.trading_config", lambda: trading_cfg)

    created = await book_orders.propose_trim(
        symbol="AAPL", qty=Decimal("20"), account_id="book-acct",
        source="book_fable", reason="take some off", db_path=db,
    )
    assert created["status"] == "proposed", created

    # Overnight: the stop filled, the position is gone.
    broker.position_qty = 0.0
    broker.book = {}
    sqlite_mod.save_snapshot(_ACCOUNT, [], [], broker="alpaca", db_path=db)

    result = await approve_proposal(created["id"], db_path=db)
    assert result["status"] == "blocked", result
    assert broker.placed == []


@pytest.mark.asyncio
async def test_non_book_source_cannot_mint_order_changes(monkeypatch, tmp_path: Path) -> None:
    db = tmp_path / "exec.db"
    _book_account(monkeypatch)
    out = await book_orders.propose_move_stop(
        symbol="AAPL", stop_price=Decimal("145"), account_id="x", source="chat",
        reason=None, db_path=db,
    )
    assert out["status"] == "invalid"
