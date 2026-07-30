"""Book execution round (2026-07-23): MOO tif, overnight proposals, stop guard."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from brokers.base import Account
from config import settings as settings_mod
from db import order_executions as order_executions_mod
from db import paper_orders as paper_orders_mod
from db import sqlite as sqlite_mod
from db.order_executions import create_execution_record, update_execution_status
from trading import stop_guard as stop_guard_mod
from trading.market_hours import moo_time_in_force, next_session_close
from trading.models import ExecutionResult, OrderIntent, RiskRejectedError
from trading.order_proposal import build_order_proposal_from_args
from trading.risk_engine import RiskEngine
from trading.stop_guard import place_protective_stops

_ET = ZoneInfo("America/New_York")


# ── moo_time_in_force ──────────────────────────────────────────────────────


def test_moo_tif_overnight_is_opg() -> None:
    # Wed 2026-07-22 20:00 ET — next auction is Thursday's open.
    assert moo_time_in_force(datetime(2026, 7, 22, 20, 0, tzinfo=_ET)) == "opg"


def test_moo_tif_premarket_is_opg() -> None:
    assert moo_time_in_force(datetime(2026, 7, 23, 8, 0, tzinfo=_ET)) == "opg"


def test_moo_tif_paper_overnight_is_day() -> None:
    # Alpaca paper never runs the opening auction, so an opg order expires
    # unfilled at the open — paper must queue a market DAY fill instead.
    assert (
        moo_time_in_force(datetime(2026, 7, 22, 20, 0, tzinfo=_ET), paper=True) == "day"
    )


def test_moo_tif_paper_weekend_is_day() -> None:
    assert (
        moo_time_in_force(datetime(2026, 7, 25, 11, 0, tzinfo=_ET), paper=True) == "day"
    )


def test_moo_tif_after_cutoff_premarket_is_day() -> None:
    # 09:29 on a trading day: opg would queue to TOMORROW's auction (stale
    # thesis) — a day market order releases at this morning's open instead.
    assert moo_time_in_force(datetime(2026, 7, 23, 9, 29, tzinfo=_ET)) == "day"


def test_moo_tif_market_open_is_day() -> None:
    assert moo_time_in_force(datetime(2026, 7, 23, 11, 0, tzinfo=_ET)) == "day"


def test_moo_tif_weekend_midday_is_opg_for_next_auction() -> None:
    # Sat 11:00 is outside the 09:28→09:30 dead zone — opg queues to Monday's
    # opening auction, exactly the intended fill.
    assert moo_time_in_force(datetime(2026, 7, 25, 11, 0, tzinfo=_ET)) == "opg"


# ── next_session_close ─────────────────────────────────────────────────────


def test_next_session_close_overnight_is_next_day() -> None:
    close = next_session_close(datetime(2026, 7, 22, 20, 0, tzinfo=_ET))
    assert close == datetime(2026, 7, 23, 16, 0, tzinfo=_ET)


def test_next_session_close_friday_night_is_monday() -> None:
    close = next_session_close(datetime(2026, 7, 24, 20, 0, tzinfo=_ET))
    assert close == datetime(2026, 7, 27, 16, 0, tzinfo=_ET)


def test_next_session_close_midsession_is_today() -> None:
    close = next_session_close(datetime(2026, 7, 23, 11, 0, tzinfo=_ET))
    assert close == datetime(2026, 7, 23, 16, 0, tzinfo=_ET)


# ── build_order_proposal_from_args ─────────────────────────────────────────


def _patch_builder_account(monkeypatch) -> None:
    monkeypatch.setattr(
        "trading.order_proposal.default_execution_account",
        lambda db_path=None: {"id": "acct_book", "broker": "alpaca"},
    )


def test_builder_moo_with_stop_rides_intent_fields(monkeypatch, tmp_path: Path) -> None:
    _patch_builder_account(monkeypatch)
    built = build_order_proposal_from_args(
        {
            "symbol": "GOOGL",
            "side": "buy",
            "qty": 68,
            "order_type": "market",
            "entry_style": "moo",
            "stop_price": "314.90",
        },
        db_path=tmp_path / "b.db",
    )
    assert "error" not in built
    assert built["intent_fields"]["entry_style"] == "moo"
    assert built["intent_fields"]["stop_price"] == "314.90"
    assert built["proposal"]["entry_style"] == "moo"
    assert built["proposal"]["stop_price"] == 314.90


def test_builder_rejects_moo_limit(monkeypatch, tmp_path: Path) -> None:
    _patch_builder_account(monkeypatch)
    built = build_order_proposal_from_args(
        {
            "symbol": "GOOGL",
            "side": "buy",
            "qty": 5,
            "order_type": "limit",
            "limit_price": "300",
            "entry_style": "moo",
        },
        db_path=tmp_path / "b.db",
    )
    assert "error" in built


def test_builder_rejects_stop_price_on_sell(monkeypatch, tmp_path: Path) -> None:
    _patch_builder_account(monkeypatch)
    built = build_order_proposal_from_args(
        {"symbol": "GOOGL", "side": "sell", "qty": 5, "stop_price": "300"},
        db_path=tmp_path / "b.db",
    )
    assert "error" in built


def test_builder_rejects_bad_entry_style(monkeypatch, tmp_path: Path) -> None:
    _patch_builder_account(monkeypatch)
    built = build_order_proposal_from_args(
        {"symbol": "GOOGL", "side": "buy", "qty": 5, "entry_style": "loo"},
        db_path=tmp_path / "b.db",
    )
    assert "error" in built


# ── RiskEngine order-shape gates ───────────────────────────────────────────


def _intent(**overrides) -> OrderIntent:
    base = dict(
        proposal_id="paper_test123",
        session_id=None,
        symbol="AAPL",
        side="buy",
        qty=Decimal("10"),
        order_type="market",
        limit_price=None,
        broker="alpaca",
    )
    base.update(overrides)
    return OrderIntent(**base)


def _no_kill(monkeypatch) -> None:
    monkeypatch.setattr("trading.risk_engine.is_kill_switch_active", lambda: False)


@pytest.mark.asyncio
async def test_risk_rejects_unknown_tif(monkeypatch, tmp_path: Path) -> None:
    _no_kill(monkeypatch)
    result = await RiskEngine.validate(
        _intent(time_in_force="ioc"), db_path=tmp_path / "r.db"
    )
    assert result.passed is False
    assert "time_in_force" in (result.blocked_reason or "")


@pytest.mark.asyncio
async def test_risk_rejects_buy_stop(monkeypatch, tmp_path: Path) -> None:
    _no_kill(monkeypatch)
    result = await RiskEngine.validate(
        _intent(order_type="stop", stop_price=Decimal("100")),
        db_path=tmp_path / "r.db",
    )
    assert result.passed is False
    assert result.blocked_reason == "invalid stop order shape"


@pytest.mark.asyncio
async def test_risk_rejects_stop_without_price(monkeypatch, tmp_path: Path) -> None:
    _no_kill(monkeypatch)
    result = await RiskEngine.validate(
        _intent(side="sell", order_type="stop"), db_path=tmp_path / "r.db"
    )
    assert result.passed is False
    assert result.blocked_reason == "invalid stop order shape"


@pytest.mark.asyncio
async def test_risk_rejects_stray_stop_price(monkeypatch, tmp_path: Path) -> None:
    _no_kill(monkeypatch)
    result = await RiskEngine.validate(
        _intent(stop_price=Decimal("100")), db_path=tmp_path / "r.db"
    )
    assert result.passed is False
    assert result.blocked_reason == "stop_price on a non-stop order"


@pytest.mark.asyncio
async def test_risk_rejects_fractional_opg(monkeypatch, tmp_path: Path) -> None:
    _no_kill(monkeypatch)
    result = await RiskEngine.validate(
        _intent(qty=Decimal("1.5"), time_in_force="opg", allow_fractional=True),
        db_path=tmp_path / "r.db",
    )
    assert result.passed is False
    assert result.blocked_reason == "opg orders must be whole-share"


# ── RiskEngine off-hours reference price (opg + protective stop only) ──────


def _patch_risk_env(
    monkeypatch, db_path: Path, *, account_id: str | None = None
) -> None:
    """Patch the risk env and seed a snapshot.

    ``account_id`` keys the snapshot the way the pollers do (api/main.py). Tests
    that validate an intent bound to an account MUST pass the same id — an
    account-scoped read never falls back to an unkeyed run.
    """
    from db import base as db_base

    monkeypatch.setattr(db_base, "DEFAULT_DB_PATH", db_path)
    monkeypatch.setattr(sqlite_mod, "DEFAULT_DB_PATH", db_path)
    monkeypatch.setattr(order_executions_mod, "DEFAULT_DB_PATH", db_path)
    account = Account(
        id="acc1",
        status="ACTIVE",
        cash=100_000.0,
        buying_power=100_000.0,
        portfolio_value=100_000.0,
    )
    sqlite_mod.save_snapshot(
        account, [], [], broker="alpaca", account_id=account_id, db_path=db_path
    )

    class _Stub:
        symbol_exists = staticmethod(lambda _s: True)

    from brokers.alpaca import AlpacaAdapter

    stub = object.__new__(AlpacaAdapter)
    stub.symbol_exists = lambda _s: True  # type: ignore[method-assign]
    monkeypatch.setattr("trading.risk_engine.get_broker", lambda _n: stub)
    monkeypatch.setattr("trading.risk_engine.is_kill_switch_active", lambda: False)
    monkeypatch.setattr("trading.risk_engine.is_entries_halted", lambda: False)
    monkeypatch.setattr(
        "trading.risk_engine.trading_config",
        lambda: {
            **settings_mod.DEFAULT_SETTINGS["trading"],
            "execution_mode": "dry_run",
        },
    )


@pytest.mark.asyncio
async def test_opg_buy_falls_back_to_reference_close(monkeypatch, tmp_path: Path) -> None:
    db = tmp_path / "r.db"
    _patch_risk_env(monkeypatch, db)

    async def _no_live(_symbol: str):
        return None

    async def _reference(_symbol: str):
        return Decimal("320.00")

    monkeypatch.setattr("trading.risk_engine._live_market_price", _no_live)
    monkeypatch.setattr("trading.risk_engine._offhours_reference_price", _reference)
    result = await RiskEngine.validate(
        _intent(symbol="GOOGL", qty=Decimal("10"), time_in_force="opg"), db_path=db
    )
    assert result.passed is True
    assert result.checks["live_quote"] == "pass: off-hours reference close"
    assert result.risk_notional == Decimal("3200.00")


@pytest.mark.asyncio
async def test_day_market_buy_keeps_failing_closed_offhours(
    monkeypatch, tmp_path: Path
) -> None:
    db = tmp_path / "r.db"
    _patch_risk_env(monkeypatch, db)

    async def _no_live(_symbol: str):
        return None

    async def _reference(_symbol: str):
        raise AssertionError("reference price must not be consulted for day orders")

    monkeypatch.setattr("trading.risk_engine._live_market_price", _no_live)
    monkeypatch.setattr("trading.risk_engine._offhours_reference_price", _reference)
    result = await RiskEngine.validate(_intent(), db_path=db)
    assert result.passed is False
    assert result.blocked_reason == "no live quote"


@pytest.mark.asyncio
async def test_stop_sell_on_flat_position_never_shorts(
    monkeypatch, tmp_path: Path
) -> None:
    """Adversarial review H1: with allow_short_selling on, a protective stop
    validated against a FLAT position must fail reduce-only — never arm a
    resting short-to-open."""
    db = tmp_path / "r.db"
    _patch_risk_env(monkeypatch, db)  # snapshot has NO positions
    monkeypatch.setattr(
        "trading.risk_engine.trading_config",
        lambda: {
            **settings_mod.DEFAULT_SETTINGS["trading"],
            "execution_mode": "paper",
            "allow_short_selling": True,
        },
    )

    async def _live(_symbol: str):
        return Decimal("300.00")

    monkeypatch.setattr("trading.risk_engine._live_market_price", _live)
    result = await RiskEngine.validate(
        _intent(
            symbol="GOOGL",
            side="sell",
            order_type="stop",
            stop_price=Decimal("290"),
            time_in_force="gtc",
        ),
        db_path=db,
    )
    assert result.passed is False
    assert result.is_short_open is False
    assert "held long" in (result.blocked_reason or "")


@pytest.mark.asyncio
async def test_stop_sell_exempt_from_count_throttles(
    monkeypatch, tmp_path: Path
) -> None:
    """Adversarial review M3: a reduce-only protective stop must not be blocked
    by the open-orders/daily-order count throttles."""
    db = tmp_path / "r.db"
    from brokers.base import Position

    _patch_risk_env(monkeypatch, db)
    account = Account(
        id="acc1",
        status="ACTIVE",
        cash=100_000.0,
        buying_power=100_000.0,
        portfolio_value=100_000.0,
    )
    positions = [
        Position(
            symbol="GOOGL",
            qty=68.0,
            side="long",
            market_value=21_000.0,
            unrealized_pl=0.0,
            avg_entry_price=310.0,
            current_price=310.0,
        )
    ]
    sqlite_mod.save_snapshot(account, positions, [], broker="alpaca", db_path=db)
    monkeypatch.setattr(
        "trading.risk_engine.trading_config",
        lambda: {
            **settings_mod.DEFAULT_SETTINGS["trading"],
            "execution_mode": "dry_run",
            "max_open_orders": 0,
            "max_daily_orders": 0,
        },
    )

    async def _live(_symbol: str):
        return Decimal("310.00")

    monkeypatch.setattr("trading.risk_engine._live_market_price", _live)
    result = await RiskEngine.validate(
        _intent(
            symbol="GOOGL",
            side="sell",
            qty=Decimal("68"),
            order_type="stop",
            stop_price=Decimal("300"),
            time_in_force="gtc",
        ),
        db_path=db,
    )
    assert result.passed is True
    assert result.checks["open_orders_cap"] == "exempt: protective stop"
    assert result.checks["daily_orders"] == "exempt: protective stop"


def test_builder_rejects_moo_sell(monkeypatch, tmp_path: Path) -> None:
    _patch_builder_account(monkeypatch)
    built = build_order_proposal_from_args(
        {"symbol": "GOOGL", "side": "sell", "qty": 5, "entry_style": "moo"},
        db_path=tmp_path / "b.db",
    )
    assert "error" in built


# ── stop guard ─────────────────────────────────────────────────────────────


def _seed_book_entry(
    db_path: Path,
    *,
    proposal_id: str = "paper_bk1",
    status: str = "filled",
    filled_qty: float | None = 68.0,
    stop_price: str | None = "314.90",
    account_id: str = "acct_alpaca_deepseek",
) -> None:
    """Executed book proposal + its entry execution row."""
    intent_fields = {
        "symbol": "GOOGL",
        "side": "buy",
        "qty": "68",
        "order_type": "market",
        "limit_price": None,
        "broker": "alpaca",
        "asset_class": "us_equity",
        "occ_symbol": None,
        "multiplier": "1",
        "account_id": account_id,
        "allow_fractional": False,
        "entry_style": "moo",
    }
    if stop_price is not None:
        intent_fields["stop_price"] = stop_price
    row = paper_orders_mod.create_pending_proposal(
        proposal={"symbol": "GOOGL", "side": "buy", "qty": 68.0},
        intent_fields=intent_fields,
        session_id=None,
        ttl_seconds=3600,
        summary="BUY 68 GOOGL @ market",
        source="book_deepseek",
        account_id=account_id,
        db_path=db_path,
    )
    # Re-key to a deterministic id for the test, then mark executed.
    import sqlite3

    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "UPDATE paper_order_proposals SET id = ?, status = 'executed' WHERE id = ?",
            (proposal_id, row["id"]),
        )
        conn.commit()
    create_execution_record(
        execution_id=f"exec_{proposal_id}",
        proposal_id=proposal_id,
        session_id=None,
        symbol="GOOGL",
        side="buy",
        qty=Decimal("68"),
        order_type="market",
        limit_price=None,
        execution_mode="paper",
        status="submitted",
        broker="alpaca",
        account_id=account_id,
        risk_checks_json={},
        db_path=db_path,
    )
    update_execution_status(
        f"exec_{proposal_id}",
        status,
        filled_qty=filled_qty,
        db_path=db_path,
    )


@pytest.fixture(autouse=True)
def _reset_stop_guard_state():
    stop_guard_mod._attempts.clear()
    stop_guard_mod._paged.clear()
    yield
    stop_guard_mod._attempts.clear()
    stop_guard_mod._paged.clear()


@pytest.mark.asyncio
async def test_stop_guard_places_gtc_stop_once(monkeypatch, tmp_path: Path) -> None:
    db = tmp_path / "sg.db"
    _seed_book_entry(db)
    submitted: list[OrderIntent] = []

    async def _fake_submit(intent: OrderIntent, *, db_path: Path):
        submitted.append(intent)
        # Durable idempotency marker, exactly like the real chokepoint's audit row.
        create_execution_record(
            execution_id=f"exec_stop_{len(submitted)}",
            proposal_id=intent.proposal_id,
            session_id=None,
            symbol=intent.symbol,
            side=intent.side,
            qty=intent.qty,
            order_type=intent.order_type,
            limit_price=None,
            execution_mode="paper",
            status="submitted",
            broker=intent.broker,
            account_id=intent.account_id,
            risk_checks_json={},
            db_path=db_path,
        )
        return ExecutionResult(
            execution_id=f"exec_stop_{len(submitted)}",
            broker_order_id="bo1",
            execution_mode="paper",
            status="submitted",
            dry_run_log=None,
        )

    monkeypatch.setattr(stop_guard_mod.ExecutionService, "submit", _fake_submit)
    placed = await place_protective_stops("alpaca", db_path=db)
    assert placed == 1
    intent = submitted[0]
    assert intent.proposal_id == "stop|paper_bk1"
    assert intent.side == "sell"
    assert intent.order_type == "stop"
    assert intent.time_in_force == "gtc"
    assert intent.stop_price == Decimal("314.90")
    assert intent.qty == Decimal("68")
    assert intent.account_id == "acct_alpaca_deepseek"

    # Second pass: the durable execution row blocks a duplicate.
    placed_again = await place_protective_stops("alpaca", db_path=db)
    assert placed_again == 0
    assert len(submitted) == 1


@pytest.mark.asyncio
async def test_stop_guard_scopes_to_the_owning_account(monkeypatch, tmp_path: Path) -> None:
    """The poll loop ALWAYS passes an account_id (api/main.py `_poll_account`), so the
    scope filter is the production path — every other stop-guard test here calls with
    account_id=None and never exercises it.

    Regression (2026-07-28): `_row_to_proposal` omitted account_id, so the filter
    compared None against a real id, skipped every candidate, and the guard never
    placed a single stop in production — silently, because the `continue` sits above
    the attempt counter that would otherwise page. A filled GOOGL sat unprotected for
    three sessions. Assert both halves: the owning account's entry IS protected, and
    a sibling account's entry is left for its own poll rather than dropped.
    """
    db = tmp_path / "sg.db"
    _seed_book_entry(db, proposal_id="paper_ds", account_id="acct_alpaca_deepseek")
    _seed_book_entry(db, proposal_id="paper_fb", account_id="acct_alpaca_fable")
    submitted: list[OrderIntent] = []

    async def _fake_submit(intent: OrderIntent, *, db_path: Path):
        submitted.append(intent)
        create_execution_record(
            execution_id=f"exec_stop_{len(submitted)}",
            proposal_id=intent.proposal_id,
            session_id=None,
            symbol=intent.symbol,
            side=intent.side,
            qty=intent.qty,
            order_type=intent.order_type,
            limit_price=None,
            execution_mode="paper",
            status="submitted",
            broker=intent.broker,
            account_id=intent.account_id,
            risk_checks_json={},
            db_path=db_path,
        )
        return ExecutionResult(
            execution_id=f"exec_stop_{len(submitted)}",
            broker_order_id="bo1",
            execution_mode="paper",
            status="submitted",
            dry_run_log=None,
        )

    monkeypatch.setattr(stop_guard_mod.ExecutionService, "submit", _fake_submit)

    placed = await place_protective_stops(
        "alpaca", account_id="acct_alpaca_deepseek", db_path=db
    )
    assert placed == 1
    assert [i.proposal_id for i in submitted] == ["stop|paper_ds"]
    assert submitted[0].account_id == "acct_alpaca_deepseek"

    # The sibling was skipped, not consumed: its own account's poll still protects it.
    placed_sibling = await place_protective_stops(
        "alpaca", account_id="acct_alpaca_fable", db_path=db
    )
    assert placed_sibling == 1
    assert [i.proposal_id for i in submitted] == ["stop|paper_ds", "stop|paper_fb"]
    assert submitted[1].account_id == "acct_alpaca_fable"


def test_proposal_rows_carry_account_id(tmp_path: Path) -> None:
    """Pins the dropped field at the db seam, where the bug actually lived — the
    account-scoped guard test above can only fail one layer up."""
    db = tmp_path / "acct.db"
    _seed_book_entry(db, proposal_id="paper_acct", account_id="acct_alpaca_fable")
    rows = paper_orders_mod.list_book_entries_needing_stop(db_path=db)
    assert [r["account_id"] for r in rows] == ["acct_alpaca_fable"]
    assert (
        paper_orders_mod.get_proposal("paper_acct", db_path=db)["account_id"]
        == "acct_alpaca_fable"
    )


@pytest.mark.asyncio
async def test_stop_guard_skips_unfilled_and_stopless(monkeypatch, tmp_path: Path) -> None:
    db = tmp_path / "sg.db"
    _seed_book_entry(db, proposal_id="paper_uf", status="expired", filled_qty=0.0)
    _seed_book_entry(db, proposal_id="paper_ns", status="filled", stop_price=None)

    async def _fail(*_a, **_k):
        raise AssertionError("no stop should be attempted")

    monkeypatch.setattr(stop_guard_mod.ExecutionService, "submit", _fail)
    assert await place_protective_stops("alpaca", db_path=db) == 0


@pytest.mark.asyncio
async def test_stop_guard_retries_rejection_with_backoff(
    monkeypatch, tmp_path: Path
) -> None:
    db = tmp_path / "sg.db"
    _seed_book_entry(db)

    calls = {"n": 0}

    async def _reject(intent: OrderIntent, *, db_path: Path):
        calls["n"] += 1
        raise RiskRejectedError("no held long position")

    monkeypatch.setattr(stop_guard_mod.ExecutionService, "submit", _reject)
    assert await place_protective_stops("alpaca", db_path=db) == 0
    assert calls["n"] == 1
    # Within the cooloff window nothing retries…
    assert await place_protective_stops("alpaca", db_path=db) == 0
    assert calls["n"] == 1
    # …after the cooloff it does.
    attempts, _ = stop_guard_mod._attempts["paper_bk1"]
    stop_guard_mod._attempts["paper_bk1"] = (attempts, 0.0)
    assert await place_protective_stops("alpaca", db_path=db) == 0
    assert calls["n"] == 2


@pytest.mark.asyncio
async def test_stop_guard_pages_once_after_budget_exhausted(
    monkeypatch, tmp_path: Path
) -> None:
    db = tmp_path / "sg.db"
    _seed_book_entry(db)
    pages: list[str] = []

    async def _reject(intent: OrderIntent, *, db_path: Path):
        raise RiskRejectedError("no held long position")

    async def _page(row, *, db_path):
        pages.append(str(row.get("id")))

    monkeypatch.setattr(stop_guard_mod.ExecutionService, "submit", _reject)
    monkeypatch.setattr(stop_guard_mod, "_page_unprotected", _page)
    stop_guard_mod._attempts["paper_bk1"] = (stop_guard_mod._MAX_ATTEMPTS, 0.0)
    assert await place_protective_stops("alpaca", db_path=db) == 0
    assert await place_protective_stops("alpaca", db_path=db) == 0
    assert pages == ["paper_bk1"]


@pytest.mark.asyncio
async def test_dead_zone_moo_day_order_prices_off_reference(
    monkeypatch, tmp_path: Path
) -> None:
    """Adversarial review H1-lifecycle: an approval in the 9:28–9:30 dead zone
    submits tif=day pre-open — validate must still price it (book_entry flag),
    never terminally block the proposal on 'no live quote'."""
    db = tmp_path / "r.db"
    _patch_risk_env(monkeypatch, db)

    async def _no_live(_symbol: str):
        return None

    async def _reference(_symbol: str):
        return Decimal("320.00")

    monkeypatch.setattr("trading.risk_engine._live_market_price", _no_live)
    monkeypatch.setattr("trading.risk_engine._offhours_reference_price", _reference)
    monkeypatch.setattr("trading.risk_engine.is_market_open", lambda: False)
    result = await RiskEngine.validate(
        _intent(symbol="GOOGL", time_in_force="day", book_entry=True), db_path=db
    )
    assert result.passed is True
    assert result.checks["live_quote"] == "pass: off-hours reference close"


@pytest.mark.asyncio
async def test_moo_day_order_no_reference_when_market_open(
    monkeypatch, tmp_path: Path
) -> None:
    """A mid-session quote outage must never re-price a live moo market order
    off a stale close — the book_entry arm is closed-market-gated."""
    db = tmp_path / "r.db"
    _patch_risk_env(monkeypatch, db)

    async def _no_live(_symbol: str):
        return None

    async def _reference(_symbol: str):
        raise AssertionError("reference must not be consulted mid-session")

    monkeypatch.setattr("trading.risk_engine._live_market_price", _no_live)
    monkeypatch.setattr("trading.risk_engine._offhours_reference_price", _reference)
    monkeypatch.setattr("trading.risk_engine.is_market_open", lambda: True)
    result = await RiskEngine.validate(
        _intent(symbol="GOOGL", time_in_force="day", book_entry=True), db_path=db
    )
    assert result.passed is False
    assert result.blocked_reason == "no live quote"


def test_intent_builder_sets_book_entry(monkeypatch, tmp_path: Path) -> None:
    from trading.order_proposal import order_intent_from_fields

    monkeypatch.setattr(
        "trading.order_proposal.default_execution_account",
        lambda db_path=None: {"id": "acct_book", "broker": "alpaca"},
    )
    fields = {
        "symbol": "GOOGL",
        "side": "buy",
        "qty": "68",
        "order_type": "market",
        "limit_price": None,
        "broker": "alpaca",
        "entry_style": "moo",
    }
    intent = order_intent_from_fields(
        fields, proposal_id="p1", session_id=None, db_path=tmp_path / "i.db"
    )
    assert intent.book_entry is True
    assert intent.time_in_force in ("opg", "day")


@pytest.mark.asyncio
async def test_stop_guard_protects_settled_partial(monkeypatch, tmp_path: Path) -> None:
    """Adversarial review M1-lifecycle: a partially_filled entry quiet past the
    settle window is protected at its current share count."""
    db = tmp_path / "sg.db"
    _seed_book_entry(db, status="partially_filled", filled_qty=40.0)
    # Backdate the execution row past the settle window.
    import sqlite3
    from datetime import UTC as _UTC
    from datetime import datetime as _dt
    from datetime import timedelta as _td

    old = (_dt.now(_UTC) - _td(seconds=1200)).isoformat()
    with sqlite3.connect(db) as conn:
        conn.execute(
            "UPDATE order_executions SET updated_at = ? WHERE id = 'exec_paper_bk1'",
            (old,),
        )
        conn.commit()

    submitted: list[OrderIntent] = []

    async def _fake_submit(intent: OrderIntent, *, db_path: Path):
        submitted.append(intent)
        create_execution_record(
            execution_id="exec_stop_p",
            proposal_id=intent.proposal_id,
            session_id=None,
            symbol=intent.symbol,
            side=intent.side,
            qty=intent.qty,
            order_type=intent.order_type,
            limit_price=None,
            execution_mode="paper",
            status="submitted",
            broker=intent.broker,
            account_id=intent.account_id,
            risk_checks_json={},
            db_path=db_path,
        )
        return ExecutionResult(
            execution_id="exec_stop_p",
            broker_order_id="bo1",
            execution_mode="paper",
            status="submitted",
            dry_run_log=None,
        )

    monkeypatch.setattr(stop_guard_mod.ExecutionService, "submit", _fake_submit)
    assert await place_protective_stops("alpaca", db_path=db) == 1
    assert submitted[0].qty == Decimal("40")


@pytest.mark.asyncio
async def test_stop_guard_waits_on_fresh_partial(monkeypatch, tmp_path: Path) -> None:
    db = tmp_path / "sg.db"
    _seed_book_entry(db, status="partially_filled", filled_qty=40.0)

    async def _fail(*_a, **_k):
        raise AssertionError("fresh partial must not be protected yet")

    monkeypatch.setattr(stop_guard_mod.ExecutionService, "submit", _fail)
    assert await place_protective_stops("alpaca", db_path=db) == 0


# ── entry-type choice: moo | limit | ext (founder 07-24) ───────────────────


def test_builder_book_limit_gtc_rides_intent_fields(monkeypatch, tmp_path: Path) -> None:
    _patch_builder_account(monkeypatch)
    built = build_order_proposal_from_args(
        {
            "symbol": "GOOGL",
            "side": "buy",
            "qty": 68,
            "order_type": "limit",
            "limit_price": "318.50",
            "entry_style": "book_limit",
            "stop_price": "314.90",
        },
        db_path=tmp_path / "b.db",
    )
    assert "error" not in built
    assert built["intent_fields"]["entry_style"] == "book_limit"
    assert built["intent_fields"]["limit_price"] == "318.50"


def test_builder_rejects_book_limit_without_price(monkeypatch, tmp_path: Path) -> None:
    _patch_builder_account(monkeypatch)
    built = build_order_proposal_from_args(
        {"symbol": "GOOGL", "side": "buy", "qty": 68, "entry_style": "book_ext"},
        db_path=tmp_path / "b.db",
    )
    assert "error" in built


def test_intent_builder_maps_styles(monkeypatch, tmp_path: Path) -> None:
    from trading.order_proposal import order_intent_from_fields

    monkeypatch.setattr(
        "trading.order_proposal.default_execution_account",
        lambda db_path=None: {"id": "acct_book", "broker": "alpaca"},
    )
    base = {
        "symbol": "GOOGL",
        "side": "buy",
        "qty": "68",
        "order_type": "limit",
        "limit_price": "318.50",
        "broker": "alpaca",
    }
    lim = order_intent_from_fields(
        {**base, "entry_style": "book_limit"},
        proposal_id="p1", session_id=None, db_path=tmp_path / "i.db",
    )
    assert (lim.time_in_force, lim.extended_hours, lim.book_entry) == ("gtc", False, True)
    ext = order_intent_from_fields(
        {**base, "entry_style": "book_ext"},
        proposal_id="p2", session_id=None, db_path=tmp_path / "i.db",
    )
    assert (ext.time_in_force, ext.extended_hours, ext.book_entry) == ("gtc", True, True)


@pytest.mark.asyncio
async def test_risk_rejects_extended_hours_market(monkeypatch, tmp_path: Path) -> None:
    _no_kill(monkeypatch)
    result = await RiskEngine.validate(
        _intent(order_type="market", extended_hours=True), db_path=tmp_path / "r.db"
    )
    assert result.passed is False
    assert "extended_hours" in (result.blocked_reason or "")


@pytest.mark.asyncio
async def test_risk_allows_gtc_extended_limit_offhours_book(
    monkeypatch, tmp_path: Path
) -> None:
    """The ext style end-state: GTC extended-hours limit, closed market,
    priced off the reference close, cap-exempt on a book account."""
    db = tmp_path / "r.db"
    _patch_risk_env(monkeypatch, db, account_id="acct_book")
    monkeypatch.setattr(
        "trading.risk_engine._book_account_ids", lambda _db: frozenset({"acct_book"})
    )
    monkeypatch.setattr(
        "trading.risk_engine.account_is_execution_capable", lambda *_a, **_k: True
    )

    async def _no_live(_symbol: str):
        return None

    async def _reference(_symbol: str):
        return Decimal("320.00")

    monkeypatch.setattr("trading.risk_engine._live_market_price", _no_live)
    monkeypatch.setattr("trading.risk_engine._offhours_reference_price", _reference)
    monkeypatch.setattr("trading.risk_engine.is_market_open", lambda: False)
    result = await RiskEngine.validate(
        _intent(
            symbol="GOOGL",
            qty=Decimal("204"),
            order_type="limit",
            limit_price=Decimal("318.50"),
            time_in_force="gtc",
            extended_hours=True,
            book_entry=True,
            account_id="acct_book",
        ),
        db_path=db,
    )
    assert result.passed is True, result.blocked_reason
    assert result.checks["order_value_cap"] == "exempt: book account self-sizing"


# ── founder "no caps" for book accounts (07-23) ────────────────────────────


@pytest.mark.asyncio
async def test_book_account_moo_buy_exempt_from_value_and_concentration(
    monkeypatch, tmp_path: Path
) -> None:
    """$65k on a $100k book must pass — the analyst sizes its own book; the
    remaining walls are buying power / cash / daily backstops / approval."""
    db = tmp_path / "r.db"
    _patch_risk_env(monkeypatch, db, account_id="acct_book")
    monkeypatch.setattr(
        "trading.risk_engine._book_account_ids", lambda _db: frozenset({"acct_book"})
    )
    monkeypatch.setattr(
        "trading.risk_engine.account_is_execution_capable", lambda *_a, **_k: True
    )

    async def _no_live(_symbol: str):
        return None

    async def _reference(_symbol: str):
        return Decimal("320.00")

    monkeypatch.setattr("trading.risk_engine._live_market_price", _no_live)
    monkeypatch.setattr("trading.risk_engine._offhours_reference_price", _reference)
    result = await RiskEngine.validate(
        _intent(
            symbol="GOOGL",
            qty=Decimal("203"),  # ≈ $65k at the reference close
            time_in_force="opg",
            book_entry=True,
            account_id="acct_book",
        ),
        db_path=db,
    )
    assert result.passed is True, result.blocked_reason
    assert result.checks["order_value_cap"] == "exempt: book account self-sizing"
    assert result.checks["concentration"] == "exempt: book account self-sizing"


@pytest.mark.asyncio
async def test_non_book_moo_keeps_caps(monkeypatch, tmp_path: Path) -> None:
    db = tmp_path / "r.db"
    _patch_risk_env(monkeypatch, db, account_id="acct_book")
    monkeypatch.setattr(
        "trading.risk_engine._book_account_ids", lambda _db: frozenset()
    )
    monkeypatch.setattr(
        "trading.risk_engine.account_is_execution_capable", lambda *_a, **_k: True
    )

    async def _no_live(_symbol: str):
        return None

    async def _reference(_symbol: str):
        return Decimal("320.00")

    monkeypatch.setattr("trading.risk_engine._live_market_price", _no_live)
    monkeypatch.setattr("trading.risk_engine._offhours_reference_price", _reference)
    result = await RiskEngine.validate(
        _intent(
            symbol="GOOGL",
            qty=Decimal("203"),
            time_in_force="opg",
            book_entry=True,
            account_id="acct_book",
        ),
        db_path=db,
    )
    assert result.passed is False
    assert result.blocked_reason == "order exceeds max value"


def test_book_account_ids_reads_strategies(tmp_path: Path) -> None:
    from db.strategies import seed_default_strategies
    from trading.risk_engine import _book_account_ids

    db = tmp_path / "s.db"
    seed_default_strategies(db_path=db)
    ids = _book_account_ids(db)
    assert "acct_alpaca_deepseek" in ids
    assert "acct_alpaca_fable" in ids
    assert "acct_alpaca_open" in ids


def test_account_label_fail_soft(tmp_path: Path) -> None:
    from db.accounts import account_label

    assert account_label(None, db_path=tmp_path / "a.db") is None
    assert account_label("acct_missing", db_path=tmp_path / "a.db") is None


# ── book driver stop parsing ───────────────────────────────────────────────


def test_parse_stop_price_forms() -> None:
    from research.book_driver import _parse_stop_price

    assert _parse_stop_price("314.90") == Decimal("314.90")
    assert _parse_stop_price("$1,250.50") == Decimal("1250.50")
    assert _parse_stop_price("close below 312") is None
    assert _parse_stop_price("") is None
    assert _parse_stop_price(None) is None
    assert _parse_stop_price("-5") is None

# ── entry-type review fixes (2026-07-24) ───────────────────────────────────


def test_done_for_day_gtc_zero_fill_stays_open() -> None:
    """H1: a GTC book entry parked done_for_day reworks next session — mapping
    it terminal would drop it from the reconcile pool and a later fill becomes
    an invisible, unprotected position."""
    from trading.fill_tracker import _map_broker_status

    assert _map_broker_status("done_for_day", 0, "gtc") is None
    assert _map_broker_status("done_for_day", 0, "day") == "expired"
    assert _map_broker_status("done_for_day", 0, None) == "expired"
    # A partial keeps its shares tracked regardless of TIF.
    assert _map_broker_status("done_for_day", 3, "gtc") == "partially_filled"
    assert _map_broker_status("filled", 5, "gtc") == "filled"


def test_alpaca_order_parse_carries_tif() -> None:
    from brokers.alpaca import AlpacaAdapter

    item = {
        "id": "o1",
        "symbol": "AAPL",
        "qty": "1",
        "side": "buy",
        "type": "limit",
        "status": "new",
        "filled_qty": "0",
        "limit_price": "1",
        "time_in_force": "gtc",
    }
    assert AlpacaAdapter._parse_order_item(item).time_in_force == "gtc"
    del item["time_in_force"]
    assert AlpacaAdapter._parse_order_item(item).time_in_force is None


@pytest.mark.asyncio
async def test_stop_guard_covers_gtc_entry_older_than_any_window(
    monkeypatch, tmp_path: Path
) -> None:
    """H2: a GTC limit/ext entry can fill weeks after the proposal night — the
    guard is execution-state driven, never created_at-window driven."""
    import sqlite3
    from datetime import UTC, timedelta
    from datetime import datetime as dt

    db = tmp_path / "sg.db"
    _seed_book_entry(db, proposal_id="paper_old")
    backdated = (dt.now(UTC) - timedelta(days=30)).isoformat()
    with sqlite3.connect(db) as conn:
        conn.execute(
            "UPDATE paper_order_proposals SET created_at = ? WHERE id = 'paper_old'",
            (backdated,),
        )
        conn.commit()

    submitted: list[OrderIntent] = []

    async def _fake_submit(intent: OrderIntent, *, db_path: Path):
        submitted.append(intent)
        create_execution_record(
            execution_id="exec_stop_old",
            proposal_id=intent.proposal_id,
            session_id=None,
            symbol=intent.symbol,
            side=intent.side,
            qty=intent.qty,
            order_type=intent.order_type,
            limit_price=None,
            execution_mode="paper",
            status="submitted",
            broker=intent.broker,
            account_id=intent.account_id,
            risk_checks_json={},
            db_path=db_path,
        )
        return ExecutionResult(
            execution_id="exec_stop_old",
            broker_order_id="bo_old",
            execution_mode="paper",
            status="submitted",
            dry_run_log=None,
        )

    monkeypatch.setattr(stop_guard_mod.ExecutionService, "submit", _fake_submit)
    assert await place_protective_stops("alpaca", db_path=db) == 1
    assert submitted[0].proposal_id == "stop|paper_old"
    # And the durable stop row drops it from the needing-stop set.
    assert await place_protective_stops("alpaca", db_path=db) == 0


def test_proposal_summary_carries_entry_style_markers() -> None:
    """D1: the approval tap must say what the founder is committing to —
    ext's overnight eligibility is material risk, not decoration."""
    from trading.proposals import _proposal_summary

    ext = {
        "side": "buy",
        "qty": "68",
        "symbol": "GOOGL",
        "order_type": "limit",
        "limit_price": "318.50",
        "entry_style": "book_ext",
    }
    assert (
        _proposal_summary(ext)
        == "BUY 68 GOOGL @ limit 318.50 · GTC + overnight/ext-hours"
    )
    assert (
        _proposal_summary({**ext, "entry_style": "book_limit"})
        == "BUY 68 GOOGL @ limit 318.50 · GTC, rests until filled"
    )
    moo = {
        "side": "buy",
        "qty": "12",
        "symbol": "MU",
        "order_type": "market",
        "limit_price": None,
        "entry_style": "moo",
    }
    assert _proposal_summary(moo) == "BUY 12 MU @ market · next open auction"
    immediate = {**moo, "entry_style": None}
    assert _proposal_summary(immediate) == "BUY 12 MU @ market"


@pytest.mark.asyncio
async def test_phone_proposal_rejects_book_style_from_non_book_source(
    tmp_path: Path,
) -> None:
    """D5: chat surfaces (web + mobile) must not mint book-style proposals —
    the styles come with cap exemptions and belong to the nightly driver."""
    from trading.proposals import create_phone_proposal

    for style in ("moo", "book_limit", "book_ext"):
        row = await create_phone_proposal(
            {
                "symbol": "AAPL",
                "side": "buy",
                "qty": 1,
                "order_type": "market",
                "entry_style": style,
            },
            source="mobile_chat",
            db_path=tmp_path / "guard.db",
        )
        assert row == {"error": "entry_style is reserved for the nightly book driver"}
