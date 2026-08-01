"""Cancel-then-sell: a resting protective stop must not lock the position.

The stop guard places a GTC stop sell for the whole position, the RiskEngine
counts that as a pending sell, and every exit — the founder's own included —
then fails on `available` 0 with the shares plainly there. These cover the
chokepoint releasing its OWN stops for an exit, and every way that must NOT
happen.
"""

from __future__ import annotations

import asyncio
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from brokers.alpaca import AlpacaAdapter, AlpacaError
from brokers.base import Account, Order, PlaceOrderResponse, Position
from config import settings as settings_mod
from db import order_executions as order_executions_mod
from db import sqlite as sqlite_mod
from trading.execution_service import ExecutionService
from trading.models import OrderIntent, RiskRejectedError

_ACCOUNT = Account(
    id="acc1",
    status="ACTIVE",
    cash=50_000.0,
    buying_power=100_000.0,
    portfolio_value=100_000.0,
)
_OPEN_STATUSES = {"new", "accepted", "partially_filled", "pending_cancel"}


def _position(qty: float = 50.0) -> Position:
    return Position(
        symbol="AAPL",
        qty=qty,
        side="long",
        market_value=qty * 150.0,
        unrealized_pl=50.0,
        avg_entry_price=145.0,
        current_price=150.0,
    )


def _stop_order(
    order_id: str = "broker-stop-1",
    *,
    qty: float = 50.0,
    status: str = "new",
    filled_qty: float = 0.0,
    stop_price: float | None = 140.0,
) -> Order:
    return Order(
        id=order_id,
        symbol="AAPL",
        qty=qty,
        side="sell",
        order_type="stop",
        status=status,
        filled_qty=filled_qty,
        limit_price=None,
        time_in_force="gtc",
        stop_price=stop_price,
    )


class _StubBroker(AlpacaAdapter):
    """Alpaca stand-in whose order book actually reacts to cancels/places.

    Subclasses the real adapter because the RiskEngine's symbol check demands an
    AlpacaAdapter instance; __init__ is skipped so no credentials are needed.
    """

    def __init__(self, orders: list[Order], *, position_qty: float = 50.0) -> None:
        self._base_url = "https://paper-api.alpaca.markets"
        self.book = {o.id: o for o in orders}
        self.position_qty = position_qty
        self.canceled: list[str] = []
        self.placed: list[object] = []
        self.client_orders: dict[str, str] = {}
        self.place_error: Exception | None = None
        # True when place_error stands for a LOST RESPONSE rather than a
        # rejection — the order really is live at the broker.
        self.place_error_lands = False

    def symbol_exists(self, _symbol: str) -> bool:
        return True

    def option_contract_exists(self, _occ: str) -> bool:
        return True

    def _open_orders(self) -> list[Order]:
        return [o for o in self.book.values() if o.status in _OPEN_STATUSES]

    async def aget_order(self, broker_order_id: str) -> Order:
        return self.book[broker_order_id]

    async def acancel_order(self, broker_order_id: str) -> None:
        self.canceled.append(broker_order_id)
        order = self.book[broker_order_id]
        self.book[broker_order_id] = Order(**{**order.__dict__, "status": "canceled"})

    async def afetch_snapshot(self):
        return _ACCOUNT, [_position(self.position_qty)], self._open_orders()

    async def aplace_order(self, req):
        if self.place_error is not None and req.order_type != "stop":
            if self.place_error_lands:
                # The POST reached Alpaca and the order is live; only the
                # RESPONSE was lost (a read timeout surfaces as AlpacaError).
                self.book[f"live-{req.client_order_id}"] = Order(
                    id=f"live-{req.client_order_id}",
                    symbol=req.symbol,
                    qty=float(req.qty),
                    side=req.side,
                    order_type=req.order_type,
                    status="new",
                    filled_qty=0.0,
                    limit_price=None,
                    time_in_force=req.time_in_force,
                )
                self.client_orders[req.client_order_id] = f"live-{req.client_order_id}"
            raise self.place_error
        self.placed.append(req)
        order_id = f"broker-new-{len(self.placed)}"
        self.client_orders[req.client_order_id] = order_id
        # A placed order is WORKING at the broker — it has to show up in the next
        # snapshot, or the test would never notice a release window that hides
        # the exit it just committed shares to.
        self.book[order_id] = Order(
            id=order_id,
            symbol=req.symbol,
            qty=float(req.qty),
            side=req.side,
            order_type=req.order_type,
            status="new",
            filled_qty=0.0,
            limit_price=float(req.limit_price) if req.limit_price else None,
            time_in_force=req.time_in_force,
            stop_price=float(req.stop_price) if req.stop_price else None,
        )
        return PlaceOrderResponse(
            broker_order_id=order_id,
            status="accepted",
            raw={},
        )

    async def aget_order_by_client_id(self, client_order_id: str) -> Order | None:
        order_id = self.client_orders.get(client_order_id)
        return self.book.get(order_id) if order_id else None


def _env(
    monkeypatch,
    db_path: Path,
    broker: _StubBroker,
    *,
    execution_mode: str = "paper",
) -> AsyncMock:
    """Point every DB + broker seam at the temp fixtures. Returns the notify mock."""
    from db import base as db_base

    monkeypatch.setattr(db_base, "DEFAULT_DB_PATH", db_path)
    monkeypatch.setattr(sqlite_mod, "DEFAULT_DB_PATH", db_path)
    monkeypatch.setattr(order_executions_mod, "DEFAULT_DB_PATH", db_path)
    sqlite_mod.save_snapshot(
        _ACCOUNT,
        [_position(broker.position_qty)],
        broker._open_orders(),
        broker="alpaca",
        db_path=db_path,
    )
    monkeypatch.setattr("trading.risk_engine.get_broker", lambda _n: broker)
    monkeypatch.setattr("trading.execution_service.get_broker", lambda _n: broker)
    monkeypatch.setattr(
        "trading.risk_engine.market_hub.quote_snapshot",
        lambda _symbols: {"AAPL": {"price": 150.0}},
    )
    trading_cfg = {
        **settings_mod.DEFAULT_SETTINGS["trading"],
        "execution_mode": execution_mode,
    }
    monkeypatch.setattr("trading.risk_engine.is_kill_switch_active", lambda: False)
    monkeypatch.setattr("trading.risk_engine.trading_config", lambda: trading_cfg)
    monkeypatch.setattr(
        "trading.execution_service.is_kill_switch_active", lambda: False
    )
    monkeypatch.setattr("trading.execution_service.trading_config", lambda: trading_cfg)
    notify_mock = AsyncMock(return_value={})
    monkeypatch.setattr("trading.execution_service.notify", notify_mock)
    return notify_mock


def _record_book_entry(db_path: Path) -> str:
    """An executed book BUY with its filled entry execution — what the stop guard
    walks. Returns the proposal id, so `stop|<id>` is the stop's proposal id."""
    import sqlite3

    from db import paper_orders as paper_orders_mod

    row = paper_orders_mod.record_paper_order_proposal(
        proposal={"symbol": "AAPL", "side": "buy", "qty": 50, "stop_price": 140.0},
        status="executed",
        db_path=db_path,
    )
    conn = sqlite3.connect(db_path)
    conn.execute(
        "UPDATE paper_order_proposals SET source = 'book_fable' WHERE id = ?",
        (row["id"],),
    )
    conn.commit()
    conn.close()
    order_executions_mod.create_execution_record(
        execution_id="exec_entry_1",
        proposal_id=row["id"],
        session_id=None,
        symbol="AAPL",
        side="buy",
        qty=Decimal("50"),
        order_type="market",
        limit_price=None,
        execution_mode="paper",
        status="filled",
        broker="alpaca",
        broker_order_id="broker-entry-1",
        db_path=db_path,
    )
    return str(row["id"])


def _record_stop_execution(
    db_path: Path,
    *,
    execution_id: str = "exec_stop_1",
    broker_order_id: str = "broker-stop-1",
    qty: str = "50",
    proposal_id: str = "stop|book_entry_1",
) -> None:
    """The execution row the stop guard would have written for a placed stop."""
    order_executions_mod.create_execution_record(
        execution_id=execution_id,
        proposal_id=proposal_id,
        session_id=None,
        symbol="AAPL",
        side="sell",
        qty=Decimal(qty),
        order_type="stop",
        limit_price=None,
        execution_mode="paper",
        status="submitted",
        broker="alpaca",
        broker_order_id=broker_order_id,
        db_path=db_path,
    )


def _sell(**overrides) -> OrderIntent:
    base = dict(
        proposal_id="exit_1",
        session_id=None,
        symbol="AAPL",
        side="sell",
        qty=Decimal("50"),
        order_type="market",
        limit_price=None,
        broker="alpaca",
    )
    base.update(overrides)
    return OrderIntent(**base)


@pytest.mark.asyncio
async def test_full_exit_releases_the_stop_and_leaves_nothing_stopped(
    monkeypatch, tmp_path: Path
) -> None:
    db = tmp_path / "exec.db"
    broker = _StubBroker([_stop_order()])
    _env(monkeypatch, db, broker)
    _record_stop_execution(db)

    result = await ExecutionService.submit(_sell(), db_path=db)

    assert result.status == "submitted"
    assert broker.canceled == ["broker-stop-1"]
    # The exit went out and nothing was re-stopped — there are no shares left.
    assert [req.order_type for req in broker.placed] == ["market"]
    assert (
        order_executions_mod.get_execution("exec_stop_1", db_path=db)["status"]
        == "canceled"
    )


@pytest.mark.asyncio
async def test_trim_restops_the_shares_it_did_not_sell(
    monkeypatch, tmp_path: Path
) -> None:
    db = tmp_path / "exec.db"
    broker = _StubBroker([_stop_order()])
    _env(monkeypatch, db, broker)
    _record_stop_execution(db)

    await ExecutionService.submit(_sell(qty=Decimal("20")), db_path=db)

    stops = [req for req in broker.placed if req.order_type == "stop"]
    assert len(stops) == 1
    # Sold 20 of 50 ⇒ 30 left, re-stopped at the SAME trigger. Whole-share qty
    # wires as an integer ("30", never "30.0") or a non-fractionable stop is
    # rejected at the broker.
    assert stops[0].qty == "30"
    assert stops[0].stop_price == "140.0"
    assert stops[0].time_in_force == "gtc"


@pytest.mark.asyncio
async def test_broker_error_on_the_exit_puts_the_stop_back(
    monkeypatch, tmp_path: Path
) -> None:
    db = tmp_path / "exec.db"
    broker = _StubBroker([_stop_order()])
    broker.place_error = AlpacaError("insufficient qty")
    _env(monkeypatch, db, broker)
    _record_stop_execution(db)

    with pytest.raises(RiskRejectedError):
        await ExecutionService.submit(_sell(), db_path=db)

    assert broker.canceled == ["broker-stop-1"]
    restored = [req for req in broker.placed if req.order_type == "stop"]
    assert len(restored) == 1
    assert restored[0].qty == "50"
    assert restored[0].stop_price == "140.0"


@pytest.mark.asyncio
async def test_a_stop_that_cannot_be_restored_pages_the_founder(
    monkeypatch, tmp_path: Path
) -> None:
    # The position vanished under us: the exit fails revalidation AND the stop
    # cannot go back, which leaves real shares bare — the one case that must
    # reach the founder's phone rather than a log line.
    db = tmp_path / "exec.db"
    broker = _StubBroker([_stop_order()])
    notify_mock = _env(monkeypatch, db, broker)
    _record_stop_execution(db)
    broker.position_qty = 0.0

    with pytest.raises(RiskRejectedError):
        await ExecutionService.submit(_sell(), db_path=db)

    pages = [
        call.kwargs
        for call in notify_mock.call_args_list
        if call.kwargs.get("severity") == "critical"
    ]
    assert len(pages) == 1
    assert "UNPROTECTED position: AAPL" in pages[0]["title"]
    assert pages[0]["push"] is True


@pytest.mark.asyncio
async def test_never_cancels_an_order_this_app_did_not_place(
    monkeypatch, tmp_path: Path
) -> None:
    # Same resting stop at the broker, but no execution row for it — a stop the
    # founder set by hand in Alpaca's UI. The exit stays blocked; we do not
    # reach into his account and cancel it.
    db = tmp_path / "exec.db"
    broker = _StubBroker([_stop_order()])
    _env(monkeypatch, db, broker)

    with pytest.raises(RiskRejectedError, match="exceeds available"):
        await ExecutionService.submit(_sell(), db_path=db)

    assert broker.canceled == []
    assert broker.placed == []


@pytest.mark.asyncio
async def test_a_partly_filled_stop_is_left_alone(
    monkeypatch, tmp_path: Path
) -> None:
    # Shares are already leaving on the stop, so the snapshot overstates the
    # position. Cancelling would let the sell validate against shares that are
    # gone and flip the account short.
    db = tmp_path / "exec.db"
    broker = _StubBroker([_stop_order(filled_qty=10.0, status="partially_filled")])
    _env(monkeypatch, db, broker)
    _record_stop_execution(db)

    with pytest.raises(RiskRejectedError, match="exceeds available"):
        await ExecutionService.submit(_sell(), db_path=db)

    assert broker.canceled == []


@pytest.mark.asyncio
async def test_a_stop_with_no_trigger_price_is_left_alone(
    monkeypatch, tmp_path: Path
) -> None:
    # We could not put this one back as it stood, so we do not take it down.
    db = tmp_path / "exec.db"
    broker = _StubBroker([_stop_order(stop_price=None)])
    _env(monkeypatch, db, broker)
    _record_stop_execution(db)

    with pytest.raises(RiskRejectedError, match="exceeds available"):
        await ExecutionService.submit(_sell(), db_path=db)

    assert broker.canceled == []


@pytest.mark.asyncio
async def test_a_stop_order_never_releases_another_stop(
    monkeypatch, tmp_path: Path
) -> None:
    # The stop guard owns that lifecycle, and swapping one protective stop for
    # another protects nothing. tif stays "day" so ONLY the order_type clause
    # can be what refuses this — a gtc stop would pass whatever that clause says.
    db = tmp_path / "exec.db"
    broker = _StubBroker([_stop_order()])
    _env(monkeypatch, db, broker)
    _record_stop_execution(db)

    with pytest.raises(RiskRejectedError):
        await ExecutionService.submit(
            _sell(order_type="stop", stop_price=Decimal("139")), db_path=db
        )

    assert broker.canceled == []


@pytest.mark.asyncio
async def test_a_resting_limit_exit_never_releases_a_stop(
    monkeypatch, tmp_path: Path
) -> None:
    # A resting exit can sit unfilled for a session or a week; trading the stop
    # for one leaves the position with no downside protection for all of it.
    # Release is market-only — the right mechanism for a resting exit is a
    # broker-side OCO pair, not cancel-then-sell.
    db = tmp_path / "exec.db"
    broker = _StubBroker([_stop_order()])
    _env(monkeypatch, db, broker)
    _record_stop_execution(db)

    with pytest.raises(RiskRejectedError, match="exceeds available"):
        await ExecutionService.submit(
            _sell(order_type="limit", limit_price=Decimal("151")), db_path=db
        )

    assert broker.canceled == []


@pytest.mark.asyncio
async def test_an_option_exit_never_touches_the_underlyings_equity_stop(
    monkeypatch, tmp_path: Path
) -> None:
    # An option intent carries the bare UNDERLYING in intent.symbol, so an
    # unguarded lookup would find — and cancel — the equity stop on AAPL, then
    # re-stop a share count computed from a contract count.
    from imports.option_symbol import OptionSymbol, to_occ_symbol

    db = tmp_path / "exec.db"
    broker = _StubBroker([_stop_order()])
    _env(monkeypatch, db, broker)
    _record_stop_execution(db)
    occ = to_occ_symbol(
        OptionSymbol(
            underlying="AAPL",
            expiration=date.today() + timedelta(days=30),
            strike=Decimal("150"),
            right="C",
        )
    )

    with pytest.raises(RiskRejectedError):
        await ExecutionService.submit(
            _sell(
                qty=Decimal("2"),
                order_type="limit",
                limit_price=Decimal("3.50"),
                asset_class="us_option",
                occ_symbol=occ,
                multiplier=100,
            ),
            db_path=db,
        )

    assert broker.canceled == []


@pytest.mark.asyncio
async def test_a_genuine_oversell_does_not_release_anything(
    monkeypatch, tmp_path: Path
) -> None:
    # 50 held with a 20-share stop resting ⇒ available 30, and pending sells ARE
    # non-zero, so the release path genuinely runs. It frees 20 shares; 60 is
    # still 10 more than exist, so the revalidation must reject and the stop must
    # come back. (The earlier version of this test had no stop at all, so it
    # passed no matter what the release path did.)
    db = tmp_path / "exec.db"
    broker = _StubBroker([_stop_order(qty=20.0)])
    _env(monkeypatch, db, broker)
    _record_stop_execution(db, qty="20")

    with pytest.raises(RiskRejectedError, match="exceeds available"):
        await ExecutionService.submit(_sell(qty=Decimal("60")), db_path=db)

    restored = [req for req in broker.placed if req.order_type == "stop"]
    assert len(restored) == 1
    assert restored[0].qty == "20"


@pytest.mark.asyncio
async def test_the_stop_guard_does_not_re_protect_after_a_release(
    monkeypatch, tmp_path: Path
) -> None:
    """Pins a KNOWN LIMITATION, so nobody assumes a safety net that isn't there.

    list_book_entries_needing_stop treats any non-failed stop row as proof of
    protection, so the 'canceled' row a release leaves behind keeps the entry out
    of the guard's work list — the guard will NOT put a stop back. That is why
    every failure path in this module pages critically instead of relying on it.
    Making the guard self-heal instead looks like a two-line status-set change and
    is not: it re-arms placement with no ceiling (a hand cancel at the broker
    ping-pongs at poll cadence and can exhaust max_daily_orders, which then
    blocks a DIFFERENT position's first stop), it re-fires every poll in
    dry_run mode, and it sizes re-protection from the entry fill so a trimmed
    position can never be re-protected. Board: unprotected-position-monitor.
    """
    from db import paper_orders as paper_orders_mod

    db = tmp_path / "exec.db"
    broker = _StubBroker([_stop_order()])
    _env(monkeypatch, db, broker)
    monkeypatch.setattr(paper_orders_mod, "DEFAULT_DB_PATH", db)
    entry_id = _record_book_entry(db)
    _record_stop_execution(db, proposal_id=f"stop|{entry_id}")

    assert paper_orders_mod.list_book_entries_needing_stop(db_path=db) == []

    await ExecutionService.submit(_sell(qty=Decimal("20")), db_path=db)

    # A live residual stop exists, so staying out is correct here...
    assert paper_orders_mod.list_book_entries_needing_stop(db_path=db) == []
    # ...and it stays out even once that stop is dead, which is the limitation.
    residual = order_executions_mod.get_execution_by_proposal(
        f"stop|{entry_id}", db_path=db
    )
    order_executions_mod.update_execution_status(
        residual["id"], "canceled", db_path=db
    )
    assert paper_orders_mod.list_book_entries_needing_stop(db_path=db) == []


@pytest.mark.asyncio
async def test_a_stop_is_never_restopped_beyond_what_is_actually_uncommitted(
    monkeypatch, tmp_path: Path
) -> None:
    # A stop outlives the position it was sized for: the founder trimmed 20 in
    # Alpaca's own UI, leaving a 50-share stop resting over 30 shares. Sizing the
    # re-stop off the stop's own qty would commit 30 (exit) + 20 (new stop) = 50
    # sells against 30 held — a resting short-to-open.
    db = tmp_path / "exec.db"
    broker = _StubBroker([_stop_order()], position_qty=30.0)
    _env(monkeypatch, db, broker)
    _record_stop_execution(db)

    result = await ExecutionService.submit(_sell(qty=Decimal("30")), db_path=db)

    assert result.status == "submitted"
    # The whole position is committed to the exit; there is nothing left to stop.
    assert [req.order_type for req in broker.placed] == ["market"]


@pytest.mark.asyncio
async def test_a_concurrent_sell_cannot_walk_the_release_window(
    monkeypatch, tmp_path: Path
) -> None:
    # Between the cancel and the placed exit the position looks entirely
    # uncommitted: the stop is gone and the sell is not in any snapshot yet. A
    # reviewer drove a second submit into that gap and got two 50-share sells
    # against 50 held. The release path therefore holds the submit lock all the
    # way to a placed exit, so a concurrent sell cannot validate inside it.
    db = tmp_path / "exec.db"
    broker = _StubBroker([_stop_order()])
    _env(monkeypatch, db, broker)
    _record_stop_execution(db)

    original = broker.aplace_order
    racer: list[asyncio.Task] = []

    async def _place_and_race(req):
        if req.order_type == "market" and not racer:
            task = asyncio.create_task(
                ExecutionService.submit(_sell(proposal_id="exit_2"), db_path=db)
            )
            racer.append(task)
            for _ in range(20):
                await asyncio.sleep(0)
            # Still parked on the lock — it never reached the RiskEngine, so it
            # never saw the uncommitted-looking position.
            assert not task.done()
        return await original(req)

    broker.aplace_order = _place_and_race  # type: ignore[method-assign]

    await ExecutionService.submit(_sell(), db_path=db)
    with pytest.raises(RiskRejectedError, match="exceeds available"):
        await racer[0]

    # Exactly one exit reached the broker for the whole 50-share position.
    assert [req.qty for req in broker.placed if req.order_type == "market"] == ["50"]


@pytest.mark.asyncio
async def test_the_snapshot_left_behind_shows_the_exit_it_just_placed(
    monkeypatch, tmp_path: Path
) -> None:
    # The snapshot published by the release path gates the NEXT order. If it
    # shows the stop gone but not the exit that replaced it, a second sell walks
    # straight through and doubles the commitment.
    db = tmp_path / "exec.db"
    broker = _StubBroker([_stop_order()])
    _env(monkeypatch, db, broker)
    _record_stop_execution(db)

    await ExecutionService.submit(_sell(), db_path=db)

    with pytest.raises(RiskRejectedError, match="exceeds available"):
        await ExecutionService.submit(
            _sell(proposal_id="exit_2"), db_path=db
        )
    assert [req.order_type for req in broker.placed] == ["market"]


@pytest.mark.asyncio
async def test_a_lost_place_response_does_not_stack_a_stop_on_a_live_exit(
    monkeypatch, tmp_path: Path
) -> None:
    # A read timeout on the place POST surfaces as AlpacaError, indistinguishable
    # from a rejection — but the order IS live. Restoring a full-size stop on top
    # of it would commit 100 shares against 50.
    db = tmp_path / "exec.db"
    broker = _StubBroker([_stop_order()])
    broker.place_error = AlpacaError("Network error: read timeout")
    broker.place_error_lands = True
    notify_mock = _env(monkeypatch, db, broker)
    _record_stop_execution(db)

    with pytest.raises(RiskRejectedError):
        await ExecutionService.submit(_sell(), db_path=db)

    assert [req for req in broker.placed if req.order_type == "stop"] == []
    pages = [
        c.kwargs for c in notify_mock.call_args_list
        if c.kwargs.get("severity") == "critical"
    ]
    assert len(pages) == 1
    assert "may be resting at the broker" in pages[0]["body"]


@pytest.mark.asyncio
async def test_an_unconfirmed_cancel_pages_because_the_stop_is_dying(
    monkeypatch, tmp_path: Path
) -> None:
    # The DELETE was ACCEPTED and we could not watch it land. The stop is not
    # "still resting" — its state is unknown — so the row stays dead (the guard
    # re-protects) and the founder is told rather than left to find out.
    db = tmp_path / "exec.db"
    broker = _StubBroker([_stop_order()])

    async def _stuck_cancel(broker_order_id: str) -> None:
        broker.canceled.append(broker_order_id)
        order = broker.book[broker_order_id]
        broker.book[broker_order_id] = Order(
            **{**order.__dict__, "status": "pending_cancel"}
        )

    broker.acancel_order = _stuck_cancel  # type: ignore[method-assign]
    monkeypatch.setattr("trading.execution_service._CANCEL_CONFIRM_ATTEMPTS", 2)
    monkeypatch.setattr("trading.execution_service._CANCEL_CONFIRM_DELAY_S", 0.0)
    notify_mock = _env(monkeypatch, db, broker)
    _record_stop_execution(db)

    with pytest.raises(RiskRejectedError, match="exceeds available"):
        await ExecutionService.submit(_sell(), db_path=db)

    assert broker.placed == []
    assert (
        order_executions_mod.get_execution("exec_stop_1", db_path=db)["status"]
        == "canceled"
    )
    pages = [
        c.kwargs for c in notify_mock.call_args_list
        if c.kwargs.get("severity") == "critical"
    ]
    assert len(pages) == 1
    assert "never confirmed" in pages[0]["body"]


@pytest.mark.asyncio
async def test_a_refused_cancel_puts_the_row_back(
    monkeypatch, tmp_path: Path
) -> None:
    # The broker refused the DELETE, so the stop is untouched and still working.
    # The write-ahead 'canceled' must not stick, or reconcile and the guard would
    # both be reasoning about an order that is actually live.
    db = tmp_path / "exec.db"
    broker = _StubBroker([_stop_order()])

    async def _refuse(broker_order_id: str) -> None:
        raise AlpacaError("order is not cancelable")

    broker.acancel_order = _refuse  # type: ignore[method-assign]
    _env(monkeypatch, db, broker)
    _record_stop_execution(db)

    with pytest.raises(RiskRejectedError, match="exceeds available"):
        await ExecutionService.submit(_sell(), db_path=db)

    assert (
        order_executions_mod.get_execution("exec_stop_1", db_path=db)["status"]
        == "submitted"
    )


@pytest.mark.asyncio
async def test_a_residual_stop_through_the_market_is_paged_not_placed(
    monkeypatch, tmp_path: Path
) -> None:
    # Price came to the trigger while we were exiting. Re-placing now would
    # market-sell the remainder the moment the broker accepts it. RiskEngine has
    # no breach gate — that check lives in the stop guard — so this path owns it.
    db = tmp_path / "exec.db"
    broker = _StubBroker([_stop_order(stop_price=155.0)])
    notify_mock = _env(monkeypatch, db, broker)
    _record_stop_execution(db)

    await ExecutionService.submit(_sell(qty=Decimal("20")), db_path=db)

    assert [req for req in broker.placed if req.order_type == "stop"] == []
    pages = [
        c.kwargs for c in notify_mock.call_args_list
        if c.kwargs.get("severity") == "critical"
    ]
    assert len(pages) == 1
    assert "through the 155.0 trigger" in pages[0]["body"]


@pytest.mark.asyncio
async def test_multiple_stops_keep_the_tightest_protection(
    monkeypatch, tmp_path: Path
) -> None:
    # Two book entries on one symbol. Selling 30 of 50 must retire the LOOSEST
    # stop's shares first, so what survives is the tighter trigger.
    db = tmp_path / "exec.db"
    broker = _StubBroker(
        [
            _stop_order("broker-stop-1", qty=20.0, stop_price=130.0),
            _stop_order("broker-stop-2", qty=30.0, stop_price=145.0),
        ]
    )
    _env(monkeypatch, db, broker)
    _record_stop_execution(db, execution_id="exec_stop_1", qty="20")
    _record_stop_execution(
        db,
        execution_id="exec_stop_2",
        broker_order_id="broker-stop-2",
        qty="30",
        proposal_id="stop|book_entry_2",
    )

    await ExecutionService.submit(_sell(qty=Decimal("30")), db_path=db)

    stops = [req for req in broker.placed if req.order_type == "stop"]
    assert [(req.qty, req.stop_price) for req in stops] == [("20", "145.0")]


@pytest.mark.asyncio
async def test_dry_run_never_touches_a_real_order(
    monkeypatch, tmp_path: Path
) -> None:
    db = tmp_path / "exec.db"
    broker = _StubBroker([_stop_order()])
    _env(monkeypatch, db, broker, execution_mode="dry_run")
    _record_stop_execution(db)

    with pytest.raises(RiskRejectedError, match="exceeds available"):
        await ExecutionService.submit(_sell(), db_path=db)

    assert broker.canceled == []
