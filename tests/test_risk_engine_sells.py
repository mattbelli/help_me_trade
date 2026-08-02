"""Risk engine sell-to-close — equity and option long exits only."""

from __future__ import annotations

import sqlite3
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from brokers.alpaca import AlpacaAdapter
from brokers.base import Account, Order, Position
from db import order_executions as order_executions_mod
from db import sqlite as sqlite_mod
from imports.option_symbol import OptionSymbol, to_occ_symbol
from trading.models import OrderIntent
from trading.risk_engine import RiskEngine

# RiskEngine's expiry check runs on the UTC date, so build fixture dates from
# the same clock — date.today() drifts a day ahead of it every local evening.
_UTC_TODAY = datetime.now(UTC).date()
_TSLA_CALL = to_occ_symbol(
    OptionSymbol(
        underlying="TSLA",
        expiration=_UTC_TODAY + timedelta(days=30),
        strike=Decimal("410"),
        right="C",
    )
)
_TSLA_CALL_TODAY = to_occ_symbol(
    OptionSymbol(
        underlying="TSLA",
        expiration=_UTC_TODAY,
        strike=Decimal("410"),
        right="C",
    )
)
_EXPIRED_CALL = to_occ_symbol(
    OptionSymbol(
        underlying="TSLA",
        expiration=date(2020, 1, 17),
        strike=Decimal("410"),
        right="C",
    )
)


def _equity_intent(**overrides) -> OrderIntent:
    base = dict(
        proposal_id="paper_sell123",
        session_id=None,
        symbol="AAPL",
        side="sell",
        qty=Decimal("5"),
        order_type="market",
        limit_price=None,
        broker="alpaca",
    )
    base.update(overrides)
    return OrderIntent(**base)


def _option_intent(**overrides) -> OrderIntent:
    base = dict(
        proposal_id="paper_optsell",
        session_id=None,
        symbol="TSLA",
        side="sell",
        qty=Decimal("2"),
        order_type="limit",
        limit_price=Decimal("3.50"),
        broker="alpaca",
        asset_class="us_option",
        occ_symbol=_TSLA_CALL,
        multiplier=100,
    )
    base.update(overrides)
    return OrderIntent(**base)


def _stub_alpaca(*, tradable: bool = True) -> AlpacaAdapter:
    stub = object.__new__(AlpacaAdapter)
    stub.symbol_exists = lambda _symbol: tradable  # type: ignore[method-assign]
    stub.option_contract_exists = lambda _occ: tradable  # type: ignore[method-assign]
    return stub


def _save_snapshot(
    db_path: Path,
    *,
    positions: list[Position] | None = None,
    open_orders: list[Order] | None = None,
) -> None:
    account = Account(
        id="acc1",
        status="ACTIVE",
        cash=50_000.0,
        buying_power=100_000.0,
        portfolio_value=100_000.0,
    )
    sqlite_mod.save_snapshot(
        account,
        positions or [],
        open_orders or [],
        broker="alpaca",
        db_path=db_path,
    )


def _patch_env(monkeypatch, db_path: Path) -> None:
    from db import base as db_base

    monkeypatch.setattr(db_base, "DEFAULT_DB_PATH", db_path)
    monkeypatch.setattr(sqlite_mod, "DEFAULT_DB_PATH", db_path)
    monkeypatch.setattr(order_executions_mod, "DEFAULT_DB_PATH", db_path)

    _save_snapshot(
        db_path,
        positions=[
            Position(
                symbol="AAPL",
                qty=10.0,
                side="long",
                market_value=1_500.0,
                unrealized_pl=50.0,
                avg_entry_price=145.0,
                current_price=150.0,
            )
        ],
    )

    monkeypatch.setattr(
        "trading.risk_engine.get_broker",
        lambda _name: _stub_alpaca(),
    )
    monkeypatch.setattr(
        "trading.risk_engine.market_hub.quote_snapshot",
        lambda _symbols: {"AAPL": {"price": 150.0}},
    )
    monkeypatch.setattr("trading.risk_engine.is_kill_switch_active", lambda: False)
    monkeypatch.setattr(
        "trading.risk_engine.trading_config",
        lambda: {
            "execution_mode": "paper",
            "kill_switch_active": False,
            "max_order_value_usd": 50_000,
            "max_position_pct": 30,
            "max_open_orders": 10,
            "max_daily_orders": 20,
        },
    )


@pytest.mark.asyncio
async def test_equity_sell_partial_passes(monkeypatch, tmp_path: Path) -> None:
    db = tmp_path / "sell.db"
    _patch_env(monkeypatch, db)
    result = await RiskEngine.validate(_equity_intent(qty=Decimal("5")), db_path=db)
    assert result.passed is True
    assert result.checks["sell_position"].startswith("pass: closing 5 of 10")
    assert result.checks["order_value_cap"] == "pass: sell"


@pytest.mark.asyncio
async def test_equity_sell_exceeds_held_rejected(monkeypatch, tmp_path: Path) -> None:
    db = tmp_path / "sell.db"
    _patch_env(monkeypatch, db)
    result = await RiskEngine.validate(_equity_intent(qty=Decimal("15")), db_path=db)
    assert result.passed is False
    assert "exceeds available position" in (result.blocked_reason or "")
    assert result.checks["sell_position"].startswith("fail:")


@pytest.mark.asyncio
async def test_equity_sell_no_position_rejected(monkeypatch, tmp_path: Path) -> None:
    db = tmp_path / "sell.db"
    _patch_env(monkeypatch, db)
    _save_snapshot(db, positions=[])
    result = await RiskEngine.validate(_equity_intent(), db_path=db)
    assert result.passed is False
    assert result.blocked_reason == (
        "sell-to-close requires a held long position; short selling not enabled"
    )
    assert result.checks["sell_position"] == "fail: no held long position"


@pytest.mark.asyncio
async def test_equity_sell_short_position_rejected(
    monkeypatch, tmp_path: Path
) -> None:
    db = tmp_path / "sell.db"
    _patch_env(monkeypatch, db)
    _save_snapshot(
        db,
        positions=[
            Position(
                symbol="AAPL",
                qty=10.0,
                side="short",
                market_value=-1_500.0,
                unrealized_pl=-50.0,
                avg_entry_price=145.0,
                current_price=150.0,
            )
        ],
    )
    result = await RiskEngine.validate(_equity_intent(), db_path=db)
    assert result.passed is False
    assert result.checks["sell_position"] == "fail: no held long position"


@pytest.mark.asyncio
async def test_equity_sell_pending_orders_reduce_available(
    monkeypatch, tmp_path: Path
) -> None:
    db = tmp_path / "sell.db"
    _patch_env(monkeypatch, db)
    _save_snapshot(
        db,
        positions=[
            Position(
                symbol="AAPL",
                qty=10.0,
                side="long",
                market_value=1_500.0,
                unrealized_pl=50.0,
                avg_entry_price=145.0,
                current_price=150.0,
            )
        ],
        open_orders=[
            Order(
                id="ord1",
                symbol="AAPL",
                qty=6.0,
                side="sell",
                order_type="market",
                status="new",
                filled_qty=0.0,
                limit_price=None,
            )
        ],
    )
    result = await RiskEngine.validate(_equity_intent(qty=Decimal("10")), db_path=db)
    assert result.passed is False
    assert "held 10" in (result.blocked_reason or "")
    assert "pending sells 6" in (result.blocked_reason or "")


@pytest.mark.asyncio
async def test_option_sell_held_passes(monkeypatch, tmp_path: Path) -> None:
    db = tmp_path / "sell.db"
    _patch_env(monkeypatch, db)
    _save_snapshot(
        db,
        positions=[
            Position(
                symbol=_TSLA_CALL,
                qty=2.0,
                side="long",
                market_value=700.0,
                unrealized_pl=50.0,
                avg_entry_price=3.25,
                current_price=3.50,
            )
        ],
    )
    result = await RiskEngine.validate(_option_intent(), db_path=db)
    assert result.passed is True
    assert result.checks["sell_position"].startswith("pass: closing 2 of 2")


@pytest.mark.asyncio
async def test_option_sell_not_held_rejected(monkeypatch, tmp_path: Path) -> None:
    db = tmp_path / "sell.db"
    _patch_env(monkeypatch, db)
    _save_snapshot(db, positions=[])
    result = await RiskEngine.validate(_option_intent(), db_path=db)
    assert result.passed is False
    assert result.checks["sell_position"] == "fail: no held long position"


@pytest.mark.asyncio
async def test_option_sell_high_value_passes_value_cap_buys_only(
    monkeypatch, tmp_path: Path
) -> None:
    db = tmp_path / "sell.db"
    _patch_env(monkeypatch, db)
    _save_snapshot(
        db,
        positions=[
            Position(
                symbol=_TSLA_CALL,
                qty=2.0,
                side="long",
                market_value=120_000.0,
                unrealized_pl=0.0,
                avg_entry_price=300.0,
                current_price=300.0,
            )
        ],
    )
    # $600 × 2 × 100 = $120,000 > $50k cap — must pass for sell
    result = await RiskEngine.validate(
        _option_intent(limit_price=Decimal("600")),
        db_path=db,
    )
    assert result.passed is True
    assert result.checks["order_value_cap"] == "pass: sell"


@pytest.mark.asyncio
async def test_option_sell_on_expiry_day_allowed(
    monkeypatch, tmp_path: Path
) -> None:
    db = tmp_path / "sell.db"
    _patch_env(monkeypatch, db)
    _save_snapshot(
        db,
        positions=[
            Position(
                symbol=_TSLA_CALL_TODAY,
                qty=1.0,
                side="long",
                market_value=350.0,
                unrealized_pl=0.0,
                avg_entry_price=3.50,
                current_price=3.50,
            )
        ],
    )
    result = await RiskEngine.validate(
        _option_intent(occ_symbol=_TSLA_CALL_TODAY, qty=Decimal("1")),
        db_path=db,
    )
    assert result.passed is True
    assert result.checks["option_expiry"] == "pass"


@pytest.mark.asyncio
async def test_option_sell_past_expiry_rejected(
    monkeypatch, tmp_path: Path
) -> None:
    db = tmp_path / "sell.db"
    _patch_env(monkeypatch, db)
    _save_snapshot(
        db,
        positions=[
            Position(
                symbol=_EXPIRED_CALL,
                qty=1.0,
                side="long",
                market_value=0.0,
                unrealized_pl=0.0,
                avg_entry_price=0.01,
                current_price=0.01,
            )
        ],
    )
    result = await RiskEngine.validate(
        _option_intent(occ_symbol=_EXPIRED_CALL, qty=Decimal("1")),
        db_path=db,
    )
    assert result.passed is False
    assert result.blocked_reason == "option contract expired"


# --- In-flight sell reservation ------------------------------------------------
#
# The stored snapshot only refreshes on the poll loop and stays valid for
# _SNAPSHOT_MAX_AGE, so every sell submitted inside that window reads a position
# with no pending sells against it. Two of them each validated against the whole
# long and 12 shares went out against 10 held. The reservation closes that, and
# must do so WITHOUT double-counting a sell the snapshot already shows — that
# would block a legitimate exit, which is its own kind of harm.


def _pending_sell_row(
    db: Path,
    *,
    execution_id: str,
    qty: str,
    symbol: str = "AAPL",
    status: str = "pending",
    broker_order_id: str | None = None,
    filled_qty: str | None = None,
    occ_symbol: str | None = None,
    submitted_at: str | None = None,
) -> None:
    order_executions_mod.create_execution_record(
        execution_id=execution_id,
        proposal_id=None,
        session_id=None,
        symbol=symbol,
        side="sell",
        qty=Decimal(qty),
        order_type="market",
        limit_price=None,
        execution_mode="paper",
        status=status,
        broker="alpaca",
        broker_order_id=broker_order_id,
        occ_symbol=occ_symbol,
        submitted_at=submitted_at,
        db_path=db,
    )
    if filled_qty is not None:
        order_executions_mod.update_execution_status(
            execution_id, status, filled_qty=Decimal(filled_qty), db_path=db
        )


@pytest.mark.asyncio
async def test_second_sell_in_one_snapshot_window_is_blocked(
    monkeypatch, tmp_path: Path
) -> None:
    """Two 6-share sells against 10 held: the second must not pass on the first's shares."""
    db = tmp_path / "sell.db"
    _patch_env(monkeypatch, db)  # 10 AAPL long, no open orders in the snapshot
    _pending_sell_row(db, execution_id="exec_first", qty="6")
    result = await RiskEngine.validate(_equity_intent(qty=Decimal("6")), db_path=db)
    assert result.passed is False
    assert result.checks["sell_position"] == "fail: sell qty 6 exceeds available 4.0"
    # No RESTING sell caused this, so there is nothing for the chokepoint to
    # release — see test_inflight_block_does_not_look_like_a_releasable_stop.
    assert result.blocked_by_pending_sells is None


@pytest.mark.asyncio
async def test_inflight_sell_already_in_the_snapshot_is_not_reserved_twice(
    monkeypatch, tmp_path: Path
) -> None:
    """The snapshot already counts this sell — counting the DB row too would block a real exit."""
    db = tmp_path / "sell.db"
    _patch_env(monkeypatch, db)
    _save_snapshot(
        db,
        positions=[
            Position(
                symbol="AAPL",
                qty=10.0,
                side="long",
                market_value=1_500.0,
                unrealized_pl=50.0,
                avg_entry_price=145.0,
                current_price=150.0,
            )
        ],
        open_orders=[
            Order(
                id="brk1",
                symbol="AAPL",
                qty=6.0,
                side="sell",
                order_type="market",
                status="new",
                filled_qty=0.0,
                limit_price=None,
            )
        ],
    )
    # Same order, both places: snapshot open_orders AND our own execution row.
    _pending_sell_row(
        db,
        execution_id="exec_echoed",
        qty="6",
        status="submitted",
        broker_order_id="brk1",
    )
    # 10 held - 6 pending = 4 available, counted once.
    result = await RiskEngine.validate(_equity_intent(qty=Decimal("4")), db_path=db)
    assert result.passed is True
    assert result.checks["sell_position"] == "pass: closing 4 of 10.0 long"


@pytest.mark.asyncio
async def test_own_pending_row_does_not_reserve_against_itself(
    monkeypatch, tmp_path: Path
) -> None:
    """submit() writes the pending row BEFORE validating — it must not block its own order."""
    db = tmp_path / "sell.db"
    _patch_env(monkeypatch, db)
    _pending_sell_row(db, execution_id="exec_self", qty="10")
    result = await RiskEngine.validate(
        _equity_intent(qty=Decimal("10")),
        db_path=db,
        exclude_execution_id="exec_self",
    )
    assert result.passed is True
    assert result.checks["sell_position"] == "pass: closing 10 of 10.0 long"


@pytest.mark.asyncio
async def test_partially_filled_sell_reserves_only_the_remainder(
    monkeypatch, tmp_path: Path
) -> None:
    db = tmp_path / "sell.db"
    _patch_env(monkeypatch, db)
    _pending_sell_row(
        db,
        execution_id="exec_partial",
        qty="6",
        status="partially_filled",
        filled_qty="4",
    )
    # 6 ordered, 4 already gone ⇒ 2 still working, so 8 of the 10 remain sellable.
    ok = await RiskEngine.validate(_equity_intent(qty=Decimal("8")), db_path=db)
    assert ok.passed is True
    too_many = await RiskEngine.validate(_equity_intent(qty=Decimal("9")), db_path=db)
    assert too_many.passed is False


@pytest.mark.asyncio
async def test_terminal_sell_rows_do_not_reserve(monkeypatch, tmp_path: Path) -> None:
    """A filled/canceled/rejected sell is over — it must not lock the position forever."""
    db = tmp_path / "sell.db"
    _patch_env(monkeypatch, db)
    for i, status in enumerate(("filled", "canceled", "rejected", "expired")):
        _pending_sell_row(db, execution_id=f"exec_done{i}", qty="10", status=status)
    result = await RiskEngine.validate(_equity_intent(qty=Decimal("10")), db_path=db)
    assert result.passed is True


@pytest.mark.asyncio
async def test_equity_sell_does_not_reserve_against_an_option_sell(
    monkeypatch, tmp_path: Path
) -> None:
    """`symbol` holds the UNDERLYING for options — an in-flight TSLA call exit must
    not consume TSLA *shares*, and vice versa."""
    db = tmp_path / "sell.db"
    _patch_env(monkeypatch, db)
    _save_snapshot(
        db,
        positions=[
            Position(
                symbol="AAPL",
                qty=10.0,
                side="long",
                market_value=1_500.0,
                unrealized_pl=50.0,
                avg_entry_price=145.0,
                current_price=150.0,
            )
        ],
    )
    # An in-flight AAPL *option* exit: symbol is the underlying, occ_symbol the contract.
    _pending_sell_row(
        db,
        execution_id="exec_opt",
        qty="10",
        symbol="AAPL",
        occ_symbol="AAPL260101C00150000",
    )
    result = await RiskEngine.validate(_equity_intent(qty=Decimal("10")), db_path=db)
    assert result.passed is True
    assert result.checks["sell_position"] == "pass: closing 10 of 10.0 long"


@pytest.mark.asyncio
async def test_option_sell_reserves_on_the_contract(
    monkeypatch, tmp_path: Path
) -> None:
    """The reservation must actually reach option exits — keyed on the OCC contract."""
    db = tmp_path / "sell.db"
    _patch_env(monkeypatch, db)
    _save_snapshot(
        db,
        positions=[
            Position(
                symbol=_TSLA_CALL,
                qty=2.0,
                side="long",
                market_value=700.0,
                unrealized_pl=50.0,
                avg_entry_price=3.25,
                current_price=3.50,
            )
        ],
    )
    _pending_sell_row(
        db,
        execution_id="exec_optflight",
        qty="2",
        symbol="TSLA",
        occ_symbol=_TSLA_CALL,
    )
    result = await RiskEngine.validate(
        _option_intent(qty=Decimal("2")), db_path=db
    )
    assert result.passed is False
    assert "exceeds available" in (result.checks["sell_position"] or "")


# --- Over-reservation guards (adversarial review HIGH-1 / HIGH-2) ---------------
#
# Reserving too much is its own harm: it blocks a real exit, and — because the
# stop guard's replacement stop is itself a sell — it can leave a position that
# can neither be closed nor re-protected. These pin the cases where a row must
# NOT reserve.


def _snapshot_captured_at(db: Path) -> datetime:
    snap = sqlite_mod.get_latest_snapshot("alpaca", db_path=db) or {}
    captured = datetime.fromisoformat(str(snap["captured_at"]))
    return captured if captured.tzinfo else captured.replace(tzinfo=UTC)


@pytest.mark.asyncio
async def test_filled_sell_missing_from_open_orders_does_not_reserve(
    monkeypatch, tmp_path: Path
) -> None:
    """It filled: the snapshot's position is ALREADY smaller. Counting it again
    subtracts the same shares twice and the rest becomes un-exitable."""
    db = tmp_path / "sell.db"
    _patch_env(monkeypatch, db)
    # The 6 sold; the snapshot now shows the remaining 4 and no open orders.
    _save_snapshot(
        db,
        positions=[
            Position(
                symbol="AAPL",
                qty=4.0,
                side="long",
                market_value=600.0,
                unrealized_pl=20.0,
                avg_entry_price=145.0,
                current_price=150.0,
            )
        ],
    )
    before = _snapshot_captured_at(db) - timedelta(minutes=1)
    _pending_sell_row(
        db,
        execution_id="exec_filled",
        qty="6",
        status="submitted",  # reconcile has not caught up yet
        broker_order_id="brk_gone",
        submitted_at=before.isoformat(),
    )
    result = await RiskEngine.validate(_equity_intent(qty=Decimal("4")), db_path=db)
    assert result.passed is True
    assert result.checks["sell_position"] == "pass: closing 4 of 4.0 long"


@pytest.mark.asyncio
async def test_sell_placed_after_the_snapshot_still_reserves(
    monkeypatch, tmp_path: Path
) -> None:
    """The race itself: at the broker, but placed too late for this snapshot to show."""
    db = tmp_path / "sell.db"
    _patch_env(monkeypatch, db)
    after = _snapshot_captured_at(db) + timedelta(seconds=1)
    _pending_sell_row(
        db,
        execution_id="exec_racing",
        qty="6",
        status="submitted",
        broker_order_id="brk_new",
        submitted_at=after.isoformat(),
    )
    result = await RiskEngine.validate(_equity_intent(qty=Decimal("6")), db_path=db)
    assert result.passed is False
    assert "in-flight sells 6" in (result.blocked_reason or "")


@pytest.mark.asyncio
async def test_abandoned_unplaced_sell_stops_reserving(
    monkeypatch, tmp_path: Path
) -> None:
    """A `pending` row whose submit died never reaches a terminal status outside
    paper — it must not lock the position for good."""
    db = tmp_path / "sell.db"
    _patch_env(monkeypatch, db)
    _pending_sell_row(db, execution_id="exec_orphan", qty="10")
    fresh = await RiskEngine.validate(_equity_intent(qty=Decimal("10")), db_path=db)
    assert fresh.passed is False  # still plausibly mid-submit

    stale = datetime.now(UTC) - timedelta(
        seconds=order_executions_mod._UNPLACED_RESERVATION_MAX_AGE_SECONDS + 60
    )
    with sqlite3.connect(db) as conn:
        conn.execute(
            "UPDATE order_executions SET created_at = ? WHERE id = ?",
            (stale.isoformat(), "exec_orphan"),
        )
    aged = await RiskEngine.validate(_equity_intent(qty=Decimal("10")), db_path=db)
    assert aged.passed is True


@pytest.mark.asyncio
async def test_inflight_block_does_not_look_like_a_releasable_stop(
    monkeypatch, tmp_path: Path
) -> None:
    """blocked_by_pending_sells drives cancel-then-sell. An in-flight sell is not
    something a stop cancel can free, and claiming otherwise takes a live
    protective stop down for an order that still cannot pass."""
    db = tmp_path / "sell.db"
    _patch_env(monkeypatch, db)  # no open orders in the snapshot
    _pending_sell_row(db, execution_id="exec_inflight", qty="10")
    result = await RiskEngine.validate(_equity_intent(qty=Decimal("10")), db_path=db)
    assert result.passed is False
    assert result.blocked_by_pending_sells is None

    from trading.execution_service import _release_is_warranted

    assert (
        _release_is_warranted(_equity_intent(qty=Decimal("10")), result, "paper")
        is False
    )


@pytest.mark.asyncio
async def test_resting_stop_still_reports_a_releasable_block(
    monkeypatch, tmp_path: Path
) -> None:
    """The release path must keep working — a RESTING sell is releasable."""
    db = tmp_path / "sell.db"
    _patch_env(monkeypatch, db)
    _save_snapshot(
        db,
        positions=[
            Position(
                symbol="AAPL",
                qty=10.0,
                side="long",
                market_value=1_500.0,
                unrealized_pl=50.0,
                avg_entry_price=145.0,
                current_price=150.0,
            )
        ],
        open_orders=[
            Order(
                id="brk_stop",
                symbol="AAPL",
                qty=10.0,
                side="sell",
                order_type="stop",
                status="new",
                filled_qty=0.0,
                limit_price=None,
            )
        ],
    )
    result = await RiskEngine.validate(_equity_intent(qty=Decimal("10")), db_path=db)
    assert result.passed is False
    assert result.blocked_by_pending_sells == Decimal("10")

    from trading.execution_service import _release_is_warranted

    assert (
        _release_is_warranted(_equity_intent(qty=Decimal("10")), result, "paper")
        is True
    )


@pytest.mark.asyncio
async def test_lowercase_symbol_row_still_reserves(
    monkeypatch, tmp_path: Path
) -> None:
    """SQLite `=` is case-sensitive: an un-normalised row would be invisible to the
    reservation, and the oversell this gate exists to stop would go through."""
    db = tmp_path / "sell.db"
    _patch_env(monkeypatch, db)
    _pending_sell_row(db, execution_id="exec_lower", qty="10", symbol="aapl")
    result = await RiskEngine.validate(_equity_intent(qty=Decimal("10")), db_path=db)
    assert result.passed is False
    assert "in-flight sells 10" in (result.blocked_reason or "")


@pytest.mark.asyncio
async def test_release_not_claimed_when_it_would_still_fail(
    monkeypatch, tmp_path: Path
) -> None:
    """A release cancels live downside protection and the restore asks for the
    stop's FULL size. If an in-flight sell still blocks the exit afterwards, the
    exit fails anyway AND the re-stop is refused — a bare position plus a page.
    So a whole-position stop must not be reported as releasable here."""
    db = tmp_path / "sell.db"
    _patch_env(monkeypatch, db)
    _save_snapshot(
        db,
        positions=[
            Position(
                symbol="AAPL",
                qty=10.0,
                side="long",
                market_value=1_500.0,
                unrealized_pl=50.0,
                avg_entry_price=145.0,
                current_price=150.0,
            )
        ],
        open_orders=[
            Order(
                id="brk_stop_full",
                symbol="AAPL",
                qty=10.0,  # protects the WHOLE position
                side="sell",
                order_type="stop",
                status="new",
                filled_qty=0.0,
                limit_price=None,
            )
        ],
    )
    _pending_sell_row(db, execution_id="exec_inflight2", qty="4")
    result = await RiskEngine.validate(_equity_intent(qty=Decimal("10")), db_path=db)
    assert result.passed is False
    # Releasing frees 10, but 4 are in flight ⇒ only 6 sellable. Claiming this
    # releasable would take the stop down for an order that still cannot pass.
    assert result.blocked_by_pending_sells is None

    from trading.execution_service import _release_is_warranted

    assert (
        _release_is_warranted(_equity_intent(qty=Decimal("10")), result, "paper")
        is False
    )


@pytest.mark.asyncio
async def test_release_still_claimed_when_it_would_succeed(
    monkeypatch, tmp_path: Path
) -> None:
    """The release path must keep working when it genuinely unblocks the exit."""
    db = tmp_path / "sell.db"
    _patch_env(monkeypatch, db)
    _save_snapshot(
        db,
        positions=[
            Position(
                symbol="AAPL",
                qty=10.0,
                side="long",
                market_value=1_500.0,
                unrealized_pl=50.0,
                avg_entry_price=145.0,
                current_price=150.0,
            )
        ],
        open_orders=[
            Order(
                id="brk_stop_full",
                symbol="AAPL",
                qty=10.0,
                side="sell",
                order_type="stop",
                status="new",
                filled_qty=0.0,
                limit_price=None,
            )
        ],
    )
    _pending_sell_row(db, execution_id="exec_inflight3", qty="4")
    # 6 fits inside what a release frees (10 held - 4 in flight).
    result = await RiskEngine.validate(_equity_intent(qty=Decimal("6")), db_path=db)
    assert result.passed is False
    assert result.blocked_by_pending_sells == Decimal("10")


@pytest.mark.asyncio
async def test_sell_placed_between_the_fetch_and_the_snapshot_write_reserves(
    monkeypatch, tmp_path: Path
) -> None:
    """captured_at is stamped when we ASK the broker. An order placed after that
    ask cannot be in the fetched open_orders, so it must still reserve — stamping
    at write time instead left that whole gap invisible to both mechanisms."""
    db = tmp_path / "sell.db"
    _patch_env(monkeypatch, db)
    captured = _snapshot_captured_at(db)
    _pending_sell_row(
        db,
        execution_id="exec_gap",
        qty="6",
        status="submitted",
        broker_order_id="brk_gap",
        submitted_at=(captured + timedelta(milliseconds=1)).isoformat(),
    )
    result = await RiskEngine.validate(_equity_intent(qty=Decimal("6")), db_path=db)
    assert result.passed is False
    assert "in-flight sells 6" in (result.blocked_reason or "")


@pytest.mark.asyncio
async def test_status_write_cannot_revive_a_dead_reservation(
    monkeypatch, tmp_path: Path
) -> None:
    """updated_at moves on every status write; keying off it let an unrelated
    bookkeeping update drag a filled/dead row back into reserving."""
    db = tmp_path / "sell.db"
    _patch_env(monkeypatch, db)
    _save_snapshot(
        db,
        positions=[
            Position(
                symbol="AAPL",
                qty=4.0,
                side="long",
                market_value=600.0,
                unrealized_pl=20.0,
                avg_entry_price=145.0,
                current_price=150.0,
            )
        ],
    )
    before = _snapshot_captured_at(db) - timedelta(minutes=5)
    _pending_sell_row(
        db,
        execution_id="exec_dead",
        qty="6",
        status="submitted",
        broker_order_id="brk_dead",
        submitted_at=before.isoformat(),
    )
    ok = await RiskEngine.validate(_equity_intent(qty=Decimal("4")), db_path=db)
    assert ok.passed is True
    # Any later status write must not change that verdict.
    order_executions_mod.update_execution_status("exec_dead", "submitted", db_path=db)
    still_ok = await RiskEngine.validate(_equity_intent(qty=Decimal("4")), db_path=db)
    assert still_ok.passed is True
