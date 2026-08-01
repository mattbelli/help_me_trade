"""Execution safety hardening — paper endpoint, caps, breaker, notional persistence."""

from __future__ import annotations

import asyncio
import threading
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from brokers.alpaca import AlpacaAdapter
from brokers.base import Account, PlaceOrderResponse, Position
from config import settings as settings_mod
from db import order_executions as order_executions_mod
from db import risk_breaker as risk_breaker_mod
from db import sqlite as sqlite_mod
from db.order_executions import (
    create_execution_record,
    get_execution,
    init_execution_tables,
    sum_inflight_notional,
    sum_today_notional,
    update_execution_status,
)
from db.risk_breaker import get_or_create_baseline, init_risk_breaker_tables, trip
from trading.execution_service import ExecutionService
from trading.models import OrderIntent, RiskRejectedError
from trading.risk_engine import RiskEngine


def _intent(**overrides) -> OrderIntent:
    base = dict(
        proposal_id="safety_test",
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


def _save_fresh_snapshot(
    db_path: Path,
    *,
    buying_power: float = 100_000.0,
    portfolio_value: float = 100_000.0,
) -> None:
    account = Account(
        id="acc1",
        status="ACTIVE",
        cash=50_000.0,
        buying_power=buying_power,
        portfolio_value=portfolio_value,
    )
    positions = [
        Position(
            symbol="AAPL",
            qty=10.0,
            side="long",
            market_value=1_500.0,
            unrealized_pl=50.0,
            avg_entry_price=145.0,
            current_price=150.0,
        )
    ]
    sqlite_mod.save_snapshot(account, positions, [], broker="alpaca", db_path=db_path)


def _paper_stub(*, paper: bool = True) -> AlpacaAdapter:
    stub = object.__new__(AlpacaAdapter)
    stub._base_url = (
        "https://paper-api.alpaca.markets"
        if paper
        else "https://api.alpaca.markets"
    )
    stub.symbol_exists = lambda _symbol: True  # type: ignore[method-assign]
    stub.option_contract_exists = lambda _occ: True  # type: ignore[method-assign]
    stub.aplace_order = AsyncMock(  # type: ignore[method-assign]
        return_value=PlaceOrderResponse(
            broker_order_id="broker-ord-1",
            status="accepted",
            raw={"id": "broker-ord-1"},
        )
    )
    return stub


def _unknown_stub() -> object:
    class _Stub:
        def __init__(self) -> None:
            self.aplace_order = AsyncMock()
            self.symbol_exists = lambda _s: True

    return _Stub()


def _patch_db(monkeypatch, db_path: Path) -> None:
    from db import base as db_base

    monkeypatch.setattr(db_base, "DEFAULT_DB_PATH", db_path)
    monkeypatch.setattr(sqlite_mod, "DEFAULT_DB_PATH", db_path)
    monkeypatch.setattr(order_executions_mod, "DEFAULT_DB_PATH", db_path)
    monkeypatch.setattr(risk_breaker_mod, "DEFAULT_DB_PATH", db_path)


def _trading_cfg(**overrides) -> dict:
    cfg = dict(settings_mod.DEFAULT_SETTINGS["trading"])
    cfg.update(overrides)
    return cfg


def _patch_risk_env(monkeypatch, db_path: Path, **cfg_overrides) -> None:
    _patch_db(monkeypatch, db_path)
    _save_fresh_snapshot(db_path)
    stub = _paper_stub()
    monkeypatch.setattr("trading.risk_engine.get_broker", lambda _n: stub)
    monkeypatch.setattr("trading.execution_service.get_broker", lambda _n: stub)
    monkeypatch.setattr(
        "trading.risk_engine.market_hub.quote_snapshot",
        lambda _symbols: {"AAPL": {"price": 150.0}},
    )
    monkeypatch.setattr("trading.risk_engine.is_kill_switch_active", lambda: False)
    monkeypatch.setattr("trading.execution_service.is_kill_switch_active", lambda: False)
    trading_cfg = _trading_cfg(**cfg_overrides)
    monkeypatch.setattr("trading.risk_engine.trading_config", lambda: trading_cfg)
    monkeypatch.setattr("trading.execution_service.trading_config", lambda: trading_cfg)
    monkeypatch.setattr(
        "trading.execution_service.notify",
        AsyncMock(return_value={}),
    )
    monkeypatch.setattr(
        "trading.risk_engine.notify",
        AsyncMock(return_value={}),
    )


def _seed_execution(
    db_path: Path,
    *,
    execution_id: str,
    status: str,
    notional_usd: Decimal | None,
    side: str = "buy",
    short_open: int = 0,
    account_id: str | None = None,
) -> None:
    init_execution_tables(db_path)
    create_execution_record(
        execution_id=execution_id,
        proposal_id="seed",
        session_id=None,
        symbol="AAPL",
        side=side,
        qty=Decimal("10"),
        order_type="market",
        limit_price=None,
        execution_mode="paper",
        status=status,
        broker="alpaca",
        account_id=account_id,
        db_path=db_path,
    )
    if notional_usd is not None or short_open:
        update_execution_status(
            execution_id,
            status,
            notional_usd=notional_usd,
            short_open=short_open,
            db_path=db_path,
        )


@pytest.mark.asyncio
async def test_paper_live_endpoint_rejected(monkeypatch, tmp_path: Path) -> None:
    db = tmp_path / "paper.db"
    _patch_risk_env(monkeypatch, db, execution_mode="paper")
    live_stub = _paper_stub(paper=False)
    monkeypatch.setattr("trading.execution_service.get_broker", lambda _n: live_stub)
    notify_mock = AsyncMock(return_value={})
    monkeypatch.setattr("trading.execution_service.notify", notify_mock)

    with pytest.raises(RiskRejectedError, match="not a paper endpoint"):
        await ExecutionService.submit(_intent(), db_path=db)

    exec_id = await _last_execution_id(db)
    row = get_execution(exec_id, db_path=db)
    assert row is not None
    assert row["status"] == "rejected"
    assert row["notional_usd"] is None
    assert "paper_endpoint" in row["risk_checks_json"]
    critical_calls = [
        c for c in notify_mock.call_args_list if c.kwargs.get("kind") == "risk_block"
    ]
    assert len(critical_calls) == 1
    assert critical_calls[0].kwargs["severity"] == "critical"
    assert critical_calls[0].kwargs["push"] is True
    live_stub.aplace_order.assert_not_called()


@pytest.mark.asyncio
async def test_paper_endpoint_proceeds(monkeypatch, tmp_path: Path) -> None:
    db = tmp_path / "paper.db"
    _patch_risk_env(monkeypatch, db, execution_mode="paper")
    stub = _paper_stub(paper=True)
    monkeypatch.setattr("trading.execution_service.get_broker", lambda _n: stub)

    result = await ExecutionService.submit(_intent(), db_path=db)
    assert result.status == "submitted"
    stub.aplace_order.assert_called_once()


@pytest.mark.asyncio
async def test_unknown_adapter_rejected(monkeypatch, tmp_path: Path) -> None:
    db = tmp_path / "paper.db"
    _patch_risk_env(monkeypatch, db, execution_mode="paper")
    unknown = _unknown_stub()
    monkeypatch.setattr("trading.execution_service.get_broker", lambda _n: unknown)
    notify_mock = AsyncMock(return_value={})
    monkeypatch.setattr("trading.execution_service.notify", notify_mock)

    with pytest.raises(RiskRejectedError, match="not a paper endpoint"):
        await ExecutionService.submit(_intent(), db_path=db)

    unknown.aplace_order.assert_not_called()


async def _last_execution_id(db: Path) -> str:
    import sqlite3

    conn = sqlite3.connect(db)
    row = conn.execute(
        "SELECT id FROM order_executions ORDER BY created_at DESC LIMIT 1"
    ).fetchone()
    conn.close()
    assert row is not None
    return str(row[0])


@pytest.mark.asyncio
async def test_inflight_buying_power_blocks_second_order(
    monkeypatch, tmp_path: Path
) -> None:
    db = tmp_path / "inflight.db"
    _patch_risk_env(
        monkeypatch,
        db,
        execution_mode="dry_run",
        max_order_value_usd=50_000,
    )
    _save_fresh_snapshot(db, buying_power=100_000.0)
    # In-flight 60k + new 40k spend exceeds 95k buying-power cushion.
    _seed_execution(
        db,
        execution_id="exec_inflight",
        status="submitted",
        notional_usd=Decimal("60000"),
    )

    result = await RiskEngine.validate(
        _intent(qty=Decimal("267"), side="buy"),
        db_path=db,
    )
    assert result.passed is False
    assert "in-flight" in (result.checks.get("buying_power") or "").lower()


@pytest.mark.asyncio
async def test_inflight_concentration_blocks_second_order(
    monkeypatch, tmp_path: Path
) -> None:
    db = tmp_path / "conc.db"
    _patch_risk_env(
        monkeypatch,
        db,
        max_position_pct=30,
        max_order_value_usd=50_000,
    )
    account = Account(
        id="acc1",
        status="ACTIVE",
        cash=10_000.0,
        buying_power=200_000.0,
        portfolio_value=10_000.0,
    )
    positions = [
        Position(
            symbol="AAPL",
            qty=1.0,
            side="long",
            market_value=2_000.0,
            unrealized_pl=0.0,
            avg_entry_price=2000.0,
            current_price=2000.0,
        )
    ]
    sqlite_mod.save_snapshot(account, positions, [], broker="alpaca", db_path=db)
    _seed_execution(
        db,
        execution_id="exec_sym",
        status="submitted",
        notional_usd=Decimal("800"),
    )

    result = await RiskEngine.validate(_intent(qty=Decimal("10")), db_path=db)
    assert result.passed is False
    assert result.blocked_reason == "concentration limit exceeded"


@pytest.mark.asyncio
async def test_daily_notional_cap_blocks_buy(monkeypatch, tmp_path: Path) -> None:
    db = tmp_path / "daily.db"
    _patch_risk_env(
        monkeypatch,
        db,
        max_daily_notional_usd=50_000,
        max_order_value_usd=50_000,
    )
    _seed_execution(db, execution_id="e1", status="filled", notional_usd=Decimal("40000"))
    _seed_execution(db, execution_id="e2", status="submitted", notional_usd=Decimal("9000"))

    result = await RiskEngine.validate(_intent(qty=Decimal("10")), db_path=db)
    assert result.passed is False
    assert result.blocked_reason == "daily notional cap exceeded"


@pytest.mark.asyncio
async def test_daily_notional_cap_sell_passes(monkeypatch, tmp_path: Path) -> None:
    db = tmp_path / "daily_sell.db"
    _patch_risk_env(monkeypatch, db, max_daily_notional_usd=1_000)
    _save_fresh_snapshot(db)
    result = await RiskEngine.validate(
        _intent(side="sell", qty=Decimal("5")),
        db_path=db,
    )
    assert result.passed is True
    assert result.checks["daily_notional_cap"] == "pass: sell"


@pytest.mark.asyncio
async def test_daily_notional_cap_blocks_rebalance(
    monkeypatch, tmp_path: Path
) -> None:
    db = tmp_path / "rebal_cap.db"
    _patch_risk_env(
        monkeypatch,
        db,
        max_daily_notional_usd=50_000,
        max_order_value_usd=50_000,
    )
    _seed_execution(db, execution_id="e1", status="filled", notional_usd=Decimal("49000"))

    result = await RiskEngine.validate(
        _intent(qty=Decimal("10"), rebalance=True),
        db_path=db,
    )
    assert result.passed is False
    assert result.blocked_reason == "daily notional cap exceeded"


@pytest.mark.asyncio
async def test_daily_notional_cap_includes_short_open(
    monkeypatch, tmp_path: Path
) -> None:
    db = tmp_path / "short_cap.db"
    _patch_risk_env(
        monkeypatch,
        db,
        max_daily_notional_usd=50_000,
        max_order_value_usd=50_000,
    )
    _seed_execution(
        db,
        execution_id="short_open",
        status="filled",
        notional_usd=Decimal("49000"),
        side="sell",
        short_open=1,
    )

    assert sum_today_notional(db_path=db) == Decimal("49000")

    result = await RiskEngine.validate(_intent(qty=Decimal("10")), db_path=db)
    assert result.passed is False
    assert result.blocked_reason == "daily notional cap exceeded"


@pytest.mark.asyncio
async def test_partial_fill_counts_in_both_notional_caps(
    monkeypatch, tmp_path: Path
) -> None:
    """H2: a partially_filled order is a LIVE working order (remainder still resting at
    the broker) whose full notional must keep counting in BOTH the in-flight reservation
    and the daily cap. Before the fix its notional vanished from both sums the instant it
    partially filled — so the daily rebalance below would have wrongly PASSED."""
    db = tmp_path / "partial_cap.db"
    _patch_risk_env(
        monkeypatch,
        db,
        max_daily_notional_usd=50_000,
        max_order_value_usd=50_000,
    )
    _seed_execution(
        db,
        execution_id="p1",
        status="partially_filled",
        notional_usd=Decimal("49000"),
    )

    # Concrete cap math: the full $49k of the partial is counted in BOTH sums
    # (each was $0 before the fix, because 'partially_filled' was in neither status list).
    assert sum_inflight_notional(db_path=db) == Decimal("49000")
    assert sum_today_notional(db_path=db) == Decimal("49000")

    # ...so the daily cap now blocks the next order, exactly as a 'filled' row would.
    result = await RiskEngine.validate(
        _intent(qty=Decimal("10"), rebalance=True),
        db_path=db,
    )
    assert result.passed is False
    assert result.blocked_reason == "daily notional cap exceeded"


@pytest.mark.asyncio
async def test_concurrent_buys_exactly_one_passes_daily_cap(
    monkeypatch, tmp_path: Path
) -> None:
    db = tmp_path / "conc_buy.db"
    _patch_risk_env(
        monkeypatch,
        db,
        execution_mode="paper",
        max_daily_notional_usd=25_000,
        max_order_value_usd=50_000,
    )

    at_sum = {"n": 0}
    sum_release = asyncio.Event()
    validate_release = asyncio.Event()
    validate_entered = {"n": 0}
    real_sum = order_executions_mod.sum_today_notional
    real_to_thread = asyncio.to_thread
    loop = asyncio.get_running_loop()

    stub = _paper_stub(paper=True)

    def symbol_exists(_symbol: str) -> bool:
        return True

    stub.symbol_exists = symbol_exists  # type: ignore[method-assign]
    monkeypatch.setattr("trading.risk_engine.get_broker", lambda _n: stub)

    async def gated_to_thread(fn, /, *args, **kwargs):
        if getattr(fn, "__name__", "") == "symbol_exists":
            validate_entered["n"] += 1
            await validate_release.wait()
        return await real_to_thread(fn, *args, **kwargs)

    monkeypatch.setattr("trading.risk_engine.asyncio.to_thread", gated_to_thread)

    def gated_sum_today_notional(**kwargs):
        if "account_id" in kwargs:
            at_sum["n"] += 1
            if at_sum["n"] >= 2:
                loop.call_soon_threadsafe(sum_release.set)
        return real_sum(**kwargs)

    monkeypatch.setattr(
        "trading.risk_engine.sum_today_notional",
        gated_sum_today_notional,
    )

    intent_a = _intent(proposal_id="a", qty=Decimal("100"))
    intent_b = _intent(proposal_id="b", qty=Decimal("100"))

    task_a = asyncio.create_task(ExecutionService.submit(intent_a, db_path=db))
    task_b = asyncio.create_task(ExecutionService.submit(intent_b, db_path=db))

    for _ in range(100):
        if validate_entered["n"] >= 2:
            break
        await asyncio.sleep(0.01)
    validate_release.set()

    try:
        await asyncio.wait_for(sum_release.wait(), timeout=3)
    except TimeoutError:
        pass

    results = await asyncio.gather(task_a, task_b, return_exceptions=True)
    passed = [r for r in results if not isinstance(r, Exception)]
    rejected = [r for r in results if isinstance(r, Exception)]
    assert len(passed) == 1, (
        f"expected 1 pass, got {len(passed)} (at_sum={at_sum['n']}): {results}"
    )
    assert len(rejected) == 1
    assert isinstance(rejected[0], RiskRejectedError)
    assert passed[0].status == "submitted"


@pytest.mark.asyncio
async def test_breaker_baseline_race_same_row(tmp_path: Path) -> None:
    db = tmp_path / "race.db"
    init_risk_breaker_tables(db)
    barrier = threading.Barrier(2)
    results: list[dict] = []
    errors: list[BaseException] = []

    def worker(equity: Decimal) -> None:
        try:
            barrier.wait(timeout=5)
            results.append(
                get_or_create_baseline("alpaca:primary", equity, db_path=db)
            )
        except BaseException as exc:
            errors.append(exc)

    t1 = threading.Thread(target=worker, args=(Decimal("100000"),))
    t2 = threading.Thread(target=worker, args=(Decimal("99999"),))
    t1.start()
    t2.start()
    t1.join(timeout=10)
    t2.join(timeout=10)

    assert not errors
    assert len(results) == 2
    assert results[0]["baseline_equity"] == results[1]["baseline_equity"]


@pytest.mark.asyncio
async def test_buy_to_cover_passes_soft_breaker(
    monkeypatch, tmp_path: Path
) -> None:
    db = tmp_path / "cover.db"
    _patch_risk_env(monkeypatch, db, max_daily_loss_usd=2500)
    account = Account(
        id="acc1",
        status="ACTIVE",
        cash=50_000.0,
        buying_power=100_000.0,
        portfolio_value=100_000.0,
    )
    positions = [
        Position(
            symbol="AAPL",
            qty=10.0,
            side="short",
            market_value=-1_500.0,
            unrealized_pl=-50.0,
            avg_entry_price=145.0,
            current_price=150.0,
        )
    ]
    sqlite_mod.save_snapshot(account, positions, [], broker="alpaca", db_path=db)
    init_risk_breaker_tables(db)
    account_key = "alpaca:primary"
    get_or_create_baseline(account_key, Decimal("100000"), db_path=db)
    trip(account_key, "soft", db_path=db)

    cover = await RiskEngine.validate(
        _intent(side="buy", qty=Decimal("5")),
        db_path=db,
    )
    assert cover.passed is True
    assert cover.checks["daily_loss_breaker"] == "pass: buy-to-cover"
    assert cover.checks["daily_notional_cap"] == "pass: buy-to-cover"

    flip = await RiskEngine.validate(
        _intent(side="buy", qty=Decimal("15")),
        db_path=db,
    )
    assert flip.passed is False
    assert "daily loss breaker" in (flip.blocked_reason or "")


@pytest.mark.asyncio
async def test_rebalance_skips_leg_without_price(
    monkeypatch, tmp_path: Path
) -> None:
    from db.accounts import default_execution_account
    from db.momentum_rebalance import init_rebalance_table, start_run
    from db.multi_account import migrate_multi_account
    from db.paper_orders import init_paper_order_tables
    from db.sqlite import init_db
    from db.strategies import (
        get_strategy,
        init_strategy_tables,
        seed_default_strategies,
        update_strategy,
    )
    from db.trades import init_trade_tables
    from signals.momentum import _current_rebalance_month, execute_rebalance

    db = tmp_path / "no_price.db"
    init_db(db)
    init_trade_tables(db)
    init_execution_tables(db)
    init_paper_order_tables(db)
    migrate_multi_account(db)
    init_strategy_tables(db)
    init_rebalance_table(db)
    seed_default_strategies(db_path=db)
    account_id = default_execution_account(db_path=db)["id"]
    update_strategy(
        "momentum_sp500",
        enabled=True,
        account_id=account_id,
        entry_mode="auto",
        db_path=db,
    )
    _patch_db(monkeypatch, db)
    monkeypatch.setattr("signals.momentum.is_market_open", lambda: True)
    monkeypatch.setattr(
        "signals.momentum.trading_config",
        lambda: _trading_cfg(
            execution_mode="dry_run",
            max_rebalance_run_notional_usd=50_000,
        ),
    )
    notify_mock = AsyncMock(return_value={})
    monkeypatch.setattr("signals.momentum.notify", notify_mock)

    call_count = {"n": 0}

    async def counting_submit(intent, *, db_path):
        call_count["n"] += 1
        return type(
            "R",
            (),
            {
                "execution_id": f"exec_{call_count['n']}",
                "broker_order_id": None,
                "execution_mode": "dry_run",
                "status": "dry_run",
                "dry_run_log": "ok",
            },
        )()

    monkeypatch.setattr(
        "signals.momentum.ExecutionService.submit",
        counting_submit,
    )

    def price_for_symbol(symbol: str, **kw) -> Decimal | None:
        if symbol == "NOPRICE":
            return None
        return Decimal("100")

    monkeypatch.setattr("signals.momentum._latest_close", price_for_symbol)

    plan = {
        "sells": [],
        "buys": [
            {"symbol": "NOPRICE", "qty": 10},
            {"symbol": "GOOD", "qty": 10},
        ],
    }
    month = _current_rebalance_month()
    start_run("momentum_sp500", month, plan, db_path=db)
    strategy = get_strategy("momentum_sp500", db_path=db)
    assert strategy is not None

    await execute_rebalance(strategy, db_path=db)
    assert call_count["n"] == 1
    complete_notify = [
        c for c in notify_mock.call_args_list if c.kwargs.get("kind") == "rebalance"
    ]
    assert complete_notify
    assert "skipped (no price)" in complete_notify[0].kwargs["body"]


@pytest.mark.asyncio
async def test_breaker_baseline_on_first_check(monkeypatch, tmp_path: Path) -> None:
    db = tmp_path / "breaker.db"
    _patch_risk_env(monkeypatch, db, max_daily_loss_usd=2500)
    init_risk_breaker_tables(db)

    result = await RiskEngine.validate(_intent(), db_path=db)
    assert result.passed is True
    assert result.checks["daily_loss_breaker"] == "pass"

    import sqlite3

    conn = sqlite3.connect(db)
    row = conn.execute(
        "SELECT baseline_equity, stage FROM risk_breaker_state"
    ).fetchone()
    conn.close()
    assert row is not None
    assert Decimal(str(row[0])) == Decimal("100000")
    assert row[1] == "armed"


@pytest.mark.asyncio
async def test_breaker_soft_trip_blocks_buy_allows_sell(
    monkeypatch, tmp_path: Path
) -> None:
    db = tmp_path / "soft.db"
    _patch_risk_env(monkeypatch, db, max_daily_loss_usd=2500)
    init_risk_breaker_tables(db)
    account_key = "alpaca:primary"
    get_or_create_baseline(account_key, Decimal("100000"), db_path=db)
    trip(account_key, "soft", db_path=db)

    buy = await RiskEngine.validate(_intent(), db_path=db)
    assert buy.passed is False
    assert "daily loss breaker" in (buy.blocked_reason or "")

    sell = await RiskEngine.validate(
        _intent(side="sell", qty=Decimal("5")),
        db_path=db,
    )
    assert sell.passed is True


@pytest.mark.asyncio
async def test_breaker_hard_trip_arms_halt_not_kill_switch(
    monkeypatch, tmp_path: Path
) -> None:
    # Retargeted behavior: the hard daily-loss breaker arms HALT (entries only),
    # NEVER the kill switch — protective exits must keep flowing on a bleeding
    # day. The kill switch stays reachable by human action only.
    db = tmp_path / "hard.db"
    _patch_risk_env(monkeypatch, db, max_daily_loss_usd=2500)
    _save_fresh_snapshot(db, portfolio_value=95_000.0)
    notify_mock = AsyncMock(return_value={})
    monkeypatch.setattr("trading.risk_engine.notify", notify_mock)
    armed = {"kill_switch_active": False, "entries_halted": False}

    def _apply_patch(patch):
        trading = patch.get("trading", {})
        if "kill_switch_active" in trading:
            armed["kill_switch_active"] = bool(trading["kill_switch_active"])
        if "entries_halted" in trading:
            armed["entries_halted"] = bool(trading["entries_halted"])
        return settings_mod.load_settings()

    monkeypatch.setattr("trading.risk_engine.apply_settings_patch", _apply_patch)
    monkeypatch.setattr(
        "trading.risk_engine.get_or_create_baseline",
        lambda key, eq, **kw: {
            "baseline_equity": Decimal("100000"),
            "stage": "armed",
        },
    )

    result = await RiskEngine.validate(_intent(), db_path=db)
    assert result.passed is False
    assert armed["entries_halted"] is True
    assert armed["kill_switch_active"] is False
    critical = [c for c in notify_mock.call_args_list if c.kwargs.get("kind") == "risk_breaker"]
    assert critical
    assert critical[0].kwargs["severity"] == "critical"


@pytest.mark.asyncio
async def test_breaker_fresh_et_date_new_baseline(
    monkeypatch, tmp_path: Path
) -> None:
    db = tmp_path / "et.db"
    _patch_risk_env(monkeypatch, db, max_daily_loss_usd=2500)
    init_risk_breaker_tables(db)
    monkeypatch.setattr(risk_breaker_mod, "et_today", lambda: "2026-07-01")
    get_or_create_baseline("alpaca:primary", Decimal("100000"), db_path=db)
    trip("alpaca:primary", "soft", db_path=db)

    monkeypatch.setattr(risk_breaker_mod, "et_today", lambda: "2026-07-02")
    result = await RiskEngine.validate(_intent(), db_path=db)
    assert result.passed is True
    assert result.checks["daily_loss_breaker"] == "pass"


@pytest.mark.asyncio
async def test_breaker_error_path_proceeds(monkeypatch, tmp_path: Path) -> None:
    db = tmp_path / "err.db"
    _patch_risk_env(monkeypatch, db, max_daily_loss_usd=2500)

    def _boom(*_a, **_k):
        raise RuntimeError("db down")

    monkeypatch.setattr("trading.risk_engine.current_stage", _boom)
    notify_mock = AsyncMock(return_value={})
    monkeypatch.setattr("trading.risk_engine.notify", notify_mock)

    result = await RiskEngine.validate(_intent(), db_path=db)
    assert result.passed is True
    assert "error:" in result.checks["daily_loss_breaker"]
    assert "(not enforced)" in result.checks["daily_loss_breaker"]
    warn = [c for c in notify_mock.call_args_list if c.kwargs.get("severity") == "warning"]
    assert warn


@pytest.mark.asyncio
async def test_notional_usd_persisted_after_validate(
    monkeypatch, tmp_path: Path
) -> None:
    db = tmp_path / "notional.db"
    _patch_risk_env(monkeypatch, db, execution_mode="dry_run")

    result = await ExecutionService.submit(_intent(qty=Decimal("10")), db_path=db)
    row = get_execution(result.execution_id, db_path=db)
    assert row is not None
    assert Decimal(str(row["notional_usd"])) == Decimal("1500")
    assert row["short_open"] == 0


@pytest.mark.asyncio
async def test_short_open_persisted(monkeypatch, tmp_path: Path) -> None:
    db = tmp_path / "short.db"
    _patch_risk_env(
        monkeypatch,
        db,
        execution_mode="dry_run",
        allow_short_selling=True,
    )
    account = Account(
        id="acc1",
        status="ACTIVE",
        cash=50_000.0,
        buying_power=100_000.0,
        portfolio_value=100_000.0,
    )
    sqlite_mod.save_snapshot(account, [], [], broker="alpaca", db_path=db)

    result = await ExecutionService.submit(
        _intent(side="sell", qty=Decimal("5")),
        db_path=db,
    )
    row = get_execution(result.execution_id, db_path=db)
    assert row is not None
    assert row["short_open"] == 1


@pytest.mark.asyncio
async def test_rebalance_run_ceiling_stops_legs(
    monkeypatch, tmp_path: Path
) -> None:
    from db.accounts import default_execution_account
    from db.momentum_rebalance import init_rebalance_table, start_run
    from db.multi_account import migrate_multi_account
    from db.paper_orders import init_paper_order_tables
    from db.sqlite import init_db
    from db.strategies import (
        get_strategy,
        init_strategy_tables,
        seed_default_strategies,
        update_strategy,
    )
    from db.trades import init_trade_tables
    from signals.momentum import _current_rebalance_month, execute_rebalance

    db = tmp_path / "rebal.db"
    init_db(db)
    init_trade_tables(db)
    init_execution_tables(db)
    init_paper_order_tables(db)
    migrate_multi_account(db)
    init_strategy_tables(db)
    init_rebalance_table(db)
    seed_default_strategies(db_path=db)
    account_id = default_execution_account(db_path=db)["id"]
    update_strategy(
        "momentum_sp500",
        enabled=True,
        account_id=account_id,
        entry_mode="auto",
        db_path=db,
    )
    _patch_db(monkeypatch, db)
    monkeypatch.setattr("signals.momentum.is_market_open", lambda: True)
    monkeypatch.setattr(
        "signals.momentum.trading_config",
        lambda: _trading_cfg(
            execution_mode="dry_run",
            max_rebalance_run_notional_usd=5_000,
        ),
    )
    notify_mock = AsyncMock(return_value={})
    monkeypatch.setattr("signals.momentum.notify", notify_mock)

    call_count = {"n": 0}

    async def counting_submit(intent, *, db_path):
        call_count["n"] += 1
        return type(
            "R",
            (),
            {
                "execution_id": f"exec_{call_count['n']}",
                "broker_order_id": None,
                "execution_mode": "dry_run",
                "status": "dry_run",
                "dry_run_log": "ok",
            },
        )()

    monkeypatch.setattr(
        "signals.momentum.ExecutionService.submit",
        counting_submit,
    )
    monkeypatch.setattr(
        "signals.momentum._latest_close",
        lambda _sym, **kw: Decimal("100"),
    )

    plan = {
        "sells": [],
        "buys": [
            {"symbol": "AAA", "qty": 30},
            {"symbol": "BBB", "qty": 30},
            {"symbol": "CCC", "qty": 30},
        ],
    }
    month = _current_rebalance_month()
    start_run("momentum_sp500", month, plan, db_path=db)
    strategy = get_strategy("momentum_sp500", db_path=db)
    assert strategy is not None

    await execute_rebalance(strategy, db_path=db)
    assert call_count["n"] == 1
    cap_notify = [
        c
        for c in notify_mock.call_args_list
        if "notional cap" in c.kwargs.get("title", "").lower()
    ]
    assert cap_notify


@pytest.mark.asyncio
async def test_alpaca_is_paper_endpoint_property() -> None:
    paper = AlpacaAdapter(
        base_url="https://paper-api.alpaca.markets",
        key_id="k",
        secret="s",
    )
    live = AlpacaAdapter(
        base_url="https://api.alpaca.markets",
        key_id="k",
        secret="s",
    )
    assert paper.is_paper_endpoint is True
    assert live.is_paper_endpoint is False


# ── HALT (entries_halted) — blocks exposure-increasing orders, exits keep flowing ──


@pytest.mark.asyncio
async def test_halt_blocks_buy_entry(monkeypatch, tmp_path: Path) -> None:
    # A strategy BUY into a name with no existing position — a fresh entry.
    db = tmp_path / "halt_entry.db"
    _patch_risk_env(monkeypatch, db)
    account = Account(
        id="acc1",
        status="ACTIVE",
        cash=50_000.0,
        buying_power=100_000.0,
        portfolio_value=100_000.0,
    )
    sqlite_mod.save_snapshot(account, [], [], broker="alpaca", db_path=db)
    monkeypatch.setattr("trading.risk_engine.is_entries_halted", lambda: True)

    result = await RiskEngine.validate(
        _intent(side="buy", qty=Decimal("1")), db_path=db
    )
    assert result.passed is False
    assert result.blocked_reason == "entries halted"
    assert result.checks["entries_halted"] == "fail: entries halted"


@pytest.mark.asyncio
async def test_halt_blocks_add(monkeypatch, tmp_path: Path) -> None:
    # A BUY that ADDS to an already-held long is exposure-increasing → blocked.
    db = tmp_path / "halt_add.db"
    _patch_risk_env(monkeypatch, db)  # snapshot holds 10 AAPL long
    monkeypatch.setattr("trading.risk_engine.is_entries_halted", lambda: True)

    result = await RiskEngine.validate(
        _intent(side="buy", qty=Decimal("1")), db_path=db
    )
    assert result.passed is False
    assert result.blocked_reason == "entries halted"


@pytest.mark.asyncio
async def test_halt_does_not_block_exit_sell(monkeypatch, tmp_path: Path) -> None:
    # A reduce-only exit (sell closing a held long) is exposure-DECREASING and
    # must flow through HALT untouched — the whole point of the state.
    db = tmp_path / "halt_exit.db"
    _patch_risk_env(monkeypatch, db)  # snapshot holds 10 AAPL long
    monkeypatch.setattr("trading.risk_engine.is_entries_halted", lambda: True)

    result = await RiskEngine.validate(
        _intent(side="sell", qty=Decimal("5")), db_path=db
    )
    assert result.passed is True
    assert result.checks["entries_halted"] == "pass: reduce-only exempt"


@pytest.mark.asyncio
async def test_kill_switch_still_blocks_exit_sell(monkeypatch, tmp_path: Path) -> None:
    # The kill switch is a TOTAL freeze — it blocks even protective exits.
    # Unchanged: "off means OFF".
    db = tmp_path / "kill_exit.db"
    _patch_risk_env(monkeypatch, db)  # snapshot holds 10 AAPL long
    monkeypatch.setattr("trading.risk_engine.is_kill_switch_active", lambda: True)

    result = await RiskEngine.validate(
        _intent(side="sell", qty=Decimal("5")), db_path=db
    )
    assert result.passed is False
    assert result.blocked_reason == "kill switch active"
    assert result.checks["kill_switch"] == "fail: kill switch active"


def test_halt_persists_across_restart(monkeypatch, tmp_path: Path) -> None:
    # HALT persists via data/settings.json — same family as the kill switch.
    settings_path = tmp_path / "settings.json"
    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", settings_path)
    settings_mod.clear_settings_cache()

    assert settings_mod.is_entries_halted() is False
    settings_mod.set_entries_halted(True)
    assert settings_mod.is_entries_halted() is True
    assert settings_path.exists()

    # Simulate a process restart: drop the in-memory cache, re-read from disk.
    settings_mod.clear_settings_cache()
    assert settings_mod.is_entries_halted() is True


def test_ambiguous_state_denies_entries(monkeypatch) -> None:
    # Deny-by-default: if trading settings are unreadable, HALT reads True.
    settings_mod.clear_settings_cache()

    def _boom() -> dict:
        raise RuntimeError("settings unreadable")

    monkeypatch.setattr(settings_mod, "_build_settings", _boom)
    assert settings_mod.is_entries_halted() is True


def test_halt_human_arm_disarm_round_trip(monkeypatch, tmp_path: Path) -> None:
    # The human disarm path: arm then disarm via the setter clears the state.
    settings_path = tmp_path / "settings.json"
    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", settings_path)
    settings_mod.clear_settings_cache()

    settings_mod.set_entries_halted(True)
    assert settings_mod.is_entries_halted() is True
    settings_mod.set_entries_halted(False)
    assert settings_mod.is_entries_halted() is False


@pytest.mark.asyncio
async def test_disarming_halt_mid_bleed_does_not_resume_entries(
    monkeypatch, tmp_path: Path
) -> None:
    """The pin that justifies symmetric (normal-token) disarm auth for HALT.

    Full real-path walk, no stubs on the HALT machinery: the hard breaker trip
    persists BOTH a settings-level HALT flag AND a per-account et_date breaker
    stage. Disarming HALT (what a normal-token /api/entries-halt does) clears
    only the flag — the breaker stage must INDEPENDENTLY keep blocking
    exposure-increasing orders for that account until the ET day rolls. A future
    refactor that clears the stage on disarm would turn this test red.
    """
    db = tmp_path / "midbleed.db"
    settings_path = tmp_path / "settings.json"
    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", settings_path)
    settings_mod.clear_settings_cache()
    _patch_risk_env(monkeypatch, db, max_daily_loss_usd=2500)
    # Real persistence end-to-end: un-stub nothing — apply_settings_patch and
    # is_entries_halted in the risk engine are the real functions here.
    init_risk_breaker_tables(db)
    account_key = "alpaca:primary"
    get_or_create_baseline(account_key, Decimal("100000"), db_path=db)
    # Bleeding day: equity 95k vs 100k baseline → loss 5000 >= 2500*1.5 → hard.
    _save_fresh_snapshot(db, portfolio_value=95_000.0)

    # 1. The hard trip fires: order blocked, HALT armed (persisted), kill switch NOT.
    first = await RiskEngine.validate(_intent(), db_path=db)
    assert first.passed is False
    assert first.blocked_reason == "hard daily loss limit breached"
    assert settings_mod.is_entries_halted() is True
    assert settings_mod.load_settings()["trading"]["kill_switch_active"] is False

    # 2. While HALT is armed, the HALT gate blocks before the breaker even runs.
    second = await RiskEngine.validate(_intent(), db_path=db)
    assert second.passed is False
    assert second.blocked_reason == "entries halted"

    # 3. Human disarms HALT mid-bleed (the normal-token /api/entries-halt path).
    settings_mod.set_entries_halted(False)
    assert settings_mod.is_entries_halted() is False

    # 4. THE PIN: the same buy is STILL blocked — the per-account breaker stage
    # holds independently until the ET day rolls.
    third = await RiskEngine.validate(_intent(), db_path=db)
    assert third.passed is False
    assert "daily loss breaker tripped" in (third.blocked_reason or "")
    assert "daily loss breaker" in third.checks["daily_loss_breaker"]

    # 5. And protective exits flowed through ALL of it.
    exit_sell = await RiskEngine.validate(
        _intent(side="sell", qty=Decimal("5")), db_path=db
    )
    assert exit_sell.passed is True


@pytest.mark.asyncio
async def test_halt_pauses_momentum_rebalance_entirely(
    monkeypatch, tmp_path: Path
) -> None:
    # A rebalance is not a protective exit: under HALT it must dispatch ZERO
    # legs — sells AND buys — or it would sell winners to raise cash for buys
    # that never happen. The run defers (month not stamped), it is not abandoned.
    from db.accounts import default_execution_account
    from db.momentum_rebalance import get_run, init_rebalance_table, start_run
    from db.multi_account import migrate_multi_account
    from db.paper_orders import init_paper_order_tables
    from db.sqlite import init_db
    from db.strategies import (
        get_strategy,
        init_strategy_tables,
        seed_default_strategies,
        update_strategy,
    )
    from db.trades import init_trade_tables
    from signals.momentum import _current_rebalance_month, execute_rebalance

    db = tmp_path / "halt_rebal.db"
    init_db(db)
    init_trade_tables(db)
    init_execution_tables(db)
    init_paper_order_tables(db)
    migrate_multi_account(db)
    init_strategy_tables(db)
    init_rebalance_table(db)
    seed_default_strategies(db_path=db)
    account_id = default_execution_account(db_path=db)["id"]
    update_strategy(
        "momentum_sp500",
        enabled=True,
        account_id=account_id,
        entry_mode="auto",
        db_path=db,
    )
    _patch_db(monkeypatch, db)
    monkeypatch.setattr("signals.momentum.is_market_open", lambda: True)
    monkeypatch.setattr("signals.momentum.is_entries_halted", lambda: True)
    monkeypatch.setattr("signals.momentum.notify", AsyncMock(return_value={}))
    submit_mock = AsyncMock()
    monkeypatch.setattr(
        "trading.execution_service.ExecutionService.submit", submit_mock
    )
    # An in-flight run with BOTH directions pending — a resume WOULD dispatch
    # these legs if HALT didn't pause the whole rebalance.
    month = _current_rebalance_month()
    start_run(
        "momentum_sp500",
        month,
        {
            "sells": [{"symbol": "AAPL", "qty": "5"}],
            "buys": [{"symbol": "MSFT", "qty": "3", "est_price": "100"}],
        },
        db_path=db,
    )

    strategy = get_strategy("momentum_sp500", db_path=db)
    result = await execute_rebalance(strategy, db_path=db)

    assert result["status"] == "entries_halted"
    submit_mock.assert_not_awaited()  # zero legs, both directions
    run = get_run("momentum_sp500", month, db_path=db)
    assert run is not None
    assert run.get("status") != "complete"  # deferred, not stamped/abandoned
    fresh = get_strategy("momentum_sp500", db_path=db)
    assert fresh.get("last_rebalanced_month") != month
