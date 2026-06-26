"""Momentum rebalance execution — safety-critical orchestration (Inc-3b Part B)."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from db import order_executions as order_executions_mod
from db.accounts import default_execution_account
from db.managed_positions import (
    init_managed_position_tables,
    list_open_positions,
    record_open_position,
)
from db.momentum_bars import upsert_daily_bars
from db.momentum_rebalance import complete_run, get_run, init_rebalance_table, start_run
from db.multi_account import migrate_multi_account
from db.order_executions import create_execution_record, init_execution_tables
from db.paper_orders import init_paper_order_tables
from db.sqlite import init_db
from db.strategies import (
    get_strategy,
    init_strategy_tables,
    seed_default_strategies,
    update_strategy,
)
from db.trades import init_trade_tables
from signals.momentum import _compute_rebalance_plan, _current_rebalance_month, execute_rebalance
from signals.strategy_scan import _run_portfolio_strategy
from trading.models import ExecutionResult


def _trading_dates(end: str, count: int) -> list[str]:
    d = date.fromisoformat(end)
    out: list[str] = []
    while len(out) < count:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d -= timedelta(days=1)
    out.reverse()
    return out


def _bar_row(sym: str, *, price: float = 100.0, end: str = "2024-12-31") -> list[tuple[str, str, float, float]]:
    dates = _trading_dates(end, 280)
    return [(sym, dt, price * 0.999, price) for dt in dates]


def _init_db(tmp_path: Path) -> Path:
    db = tmp_path / "rebalance.db"
    init_db(db)
    init_trade_tables(db)
    init_execution_tables(db)
    init_paper_order_tables(db)
    migrate_multi_account(db)
    init_strategy_tables(db)
    init_managed_position_tables(db)
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
    return db, account_id


def _patch_db(monkeypatch, db: Path) -> None:
    from db import base as db_base

    monkeypatch.setattr(db_base, "DEFAULT_DB_PATH", db)
    monkeypatch.setattr(order_executions_mod, "DEFAULT_DB_PATH", db)
    # Rebalances only fire during the regular session; default tests to market-open.
    monkeypatch.setattr("signals.momentum.is_market_open", lambda: True)


def _mock_submit(monkeypatch) -> AsyncMock:
    submit_mock = AsyncMock(
        return_value=ExecutionResult(
            execution_id="exec_test",
            broker_order_id=None,
            execution_mode="dry_run",
            status="submitted",
            dry_run_log="ok",
        )
    )
    monkeypatch.setattr(
        "trading.execution_service.ExecutionService.submit",
        submit_mock,
    )
    return submit_mock


@pytest.mark.asyncio
async def test_monthly_idempotency(tmp_path: Path, monkeypatch) -> None:
    db, _account_id = _init_db(tmp_path)
    _patch_db(monkeypatch, db)
    month = _current_rebalance_month()
    update_strategy(
        "momentum_sp500",
        last_rebalanced_month=month,
        db_path=db,
    )
    submit_mock = _mock_submit(monkeypatch)

    strategy = get_strategy("momentum_sp500", db_path=db)
    assert strategy is not None
    result = await execute_rebalance(strategy, db_path=db)

    assert result["status"] == "already_rebalanced"
    assert result["month"] == month
    submit_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_diff_correctness(tmp_path: Path, monkeypatch) -> None:
    db, account_id = _init_db(tmp_path)
    _patch_db(monkeypatch, db)
    upsert_daily_bars(_bar_row("AAA") + _bar_row("BBB") + _bar_row("CCC"), db_path=db)

    record_open_position(
        account_id=account_id,
        symbol="AAA",
        strategy_id="momentum_sp500",
        qty="5",
        exit_mode="propose",
        params={},
        db_path=db,
    )
    record_open_position(
        account_id=account_id,
        symbol="BBB",
        strategy_id="momentum_sp500",
        qty="5",
        exit_mode="propose",
        params={},
        db_path=db,
    )

    strategy = get_strategy("momentum_sp500", db_path=db)
    assert strategy is not None

    monkeypatch.setattr(
        "signals.momentum.production_target_set",
        # ≥ MINIMUM_MOMENTUM_TARGET names so the min-target guard doesn't abort. The filler
        # names have no bars and are skipped as buys, leaving the real diff (sell AAA, buy CCC).
        lambda **kwargs: [{"symbol": "BBB"}, {"symbol": "CCC"}]
        + [{"symbol": f"F{i:02d}"} for i in range(48)],
    )
    plan = _compute_rebalance_plan(strategy, db_path=db)
    assert plan is not None
    assert [leg["symbol"] for leg in plan["sells"]] == ["AAA"]
    assert [leg["symbol"] for leg in plan["buys"]] == ["CCC"]


@pytest.mark.asyncio
async def test_empty_target_places_nothing(tmp_path: Path, monkeypatch) -> None:
    db, _account_id = _init_db(tmp_path)
    _patch_db(monkeypatch, db)
    submit_mock = _mock_submit(monkeypatch)

    strategy = get_strategy("momentum_sp500", db_path=db)
    assert strategy is not None
    result = await execute_rebalance(strategy, db_path=db)

    assert result["status"] == "no_target"
    submit_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_every_submit_carries_rebalance_true(tmp_path: Path, monkeypatch) -> None:
    db, account_id = _init_db(tmp_path)
    _patch_db(monkeypatch, db)
    upsert_daily_bars(_bar_row("AAA") + _bar_row("ZZZ"), db_path=db)

    record_open_position(
        account_id=account_id,
        symbol="ZZZ",
        strategy_id="momentum_sp500",
        qty="3",
        exit_mode="propose",
        params={},
        db_path=db,
    )

    month = _current_rebalance_month()
    start_run(
        "momentum_sp500",
        month,
        {
            "sells": [{"symbol": "ZZZ", "qty": "3"}],
            "buys": [{"symbol": "AAA", "qty": "10"}],
        },
        db_path=db,
    )

    submit_mock = _mock_submit(monkeypatch)
    monkeypatch.setattr("signals.momentum.asyncio.sleep", AsyncMock())
    monkeypatch.setattr(
        "signals.momentum.production_target_set",
        lambda **kwargs: [{"symbol": "AAA"}],
    )
    push_mock = AsyncMock(return_value=0)
    monkeypatch.setattr("signals.momentum.send_alert_push", push_mock)

    strategy = get_strategy("momentum_sp500", db_path=db)
    assert strategy is not None
    await execute_rebalance(strategy, db_path=db)

    assert submit_mock.await_count == 2
    for call in submit_mock.await_args_list:
        intent = call.args[0]
        assert intent.rebalance is True


@pytest.mark.asyncio
async def test_sell_closes_managed_row_buy_uses_auto_entry_proposal_id(
    tmp_path: Path, monkeypatch
) -> None:
    db, account_id = _init_db(tmp_path)
    _patch_db(monkeypatch, db)
    upsert_daily_bars(_bar_row("AAA") + _bar_row("ZZZ"), db_path=db)

    record_open_position(
        account_id=account_id,
        symbol="ZZZ",
        strategy_id="momentum_sp500",
        qty="3",
        exit_mode="propose",
        params={},
        db_path=db,
    )

    month = _current_rebalance_month()
    start_run(
        "momentum_sp500",
        month,
        {
            "sells": [{"symbol": "ZZZ", "qty": "3"}],
            "buys": [{"symbol": "AAA", "qty": "10"}],
        },
        db_path=db,
    )

    submit_mock = _mock_submit(monkeypatch)
    monkeypatch.setattr("signals.momentum.asyncio.sleep", AsyncMock())
    monkeypatch.setattr(
        "signals.momentum.production_target_set",
        lambda **kwargs: [{"symbol": "AAA"}],
    )
    push_mock = AsyncMock(return_value=0)
    monkeypatch.setattr("signals.momentum.send_alert_push", push_mock)

    strategy = get_strategy("momentum_sp500", db_path=db)
    assert strategy is not None
    await execute_rebalance(strategy, db_path=db)

    sell_intent = submit_mock.await_args_list[0].args[0]
    buy_intent = submit_mock.await_args_list[1].args[0]
    assert sell_intent.side == "sell"
    assert sell_intent.proposal_id.startswith("rebalance_sell|momentum_sp500|")
    assert buy_intent.side == "buy"
    assert buy_intent.proposal_id.startswith("auto_entry|momentum_sp500|")

    from db.base import connect

    with connect(db) as conn:
        closed = conn.execute(
            "SELECT status, close_reason FROM managed_positions WHERE symbol = 'ZZZ'"
        ).fetchone()
    assert closed is not None
    assert closed["status"] == "closed"
    assert closed["close_reason"] == "rebalance_dropout"
    open_rows = list_open_positions(account_id=account_id, db_path=db)
    assert all(row["symbol"] != "ZZZ" for row in open_rows)


@pytest.mark.asyncio
async def test_resume_skips_already_submitted_order(tmp_path: Path, monkeypatch) -> None:
    db, account_id = _init_db(tmp_path)
    _patch_db(monkeypatch, db)
    upsert_daily_bars(_bar_row("AAA") + _bar_row("BBB"), db_path=db)

    month = _current_rebalance_month()
    start_run(
        "momentum_sp500",
        month,
        {
            "sells": [],
            "buys": [
                {"symbol": "AAA", "qty": "10"},
                {"symbol": "BBB", "qty": "10"},
            ],
        },
        db_path=db,
    )

    create_execution_record(
        execution_id="exec_existing",
        proposal_id="auto_entry|momentum_sp500|2026-01-01T00:00:00+00:00",
        session_id=None,
        symbol="AAA",
        side="buy",
        qty=Decimal("10"),
        order_type="market",
        limit_price=None,
        execution_mode="dry_run",
        status="submitted",
        broker="alpaca",
        account_id=account_id,
        db_path=db,
    )

    submit_mock = _mock_submit(monkeypatch)
    monkeypatch.setattr("signals.momentum.asyncio.sleep", AsyncMock())
    monkeypatch.setattr(
        "signals.momentum.production_target_set",
        lambda **kwargs: [{"symbol": "AAA"}, {"symbol": "BBB"}],
    )
    push_mock = AsyncMock(return_value=0)
    monkeypatch.setattr("signals.momentum.send_alert_push", push_mock)

    strategy = get_strategy("momentum_sp500", db_path=db)
    assert strategy is not None
    await execute_rebalance(strategy, db_path=db)

    assert submit_mock.await_count == 1
    intent = submit_mock.await_args_list[0].args[0]
    assert intent.symbol == "BBB"


@pytest.mark.asyncio
async def test_paper_fence_live_falls_back_to_read_only_log(
    tmp_path: Path, monkeypatch
) -> None:
    db, _account_id = _init_db(tmp_path)
    _patch_db(monkeypatch, db)
    symbols = ["AAA1", "BBB1", "CCC1"]
    rows: list[tuple[str, str, float, float]] = []
    for sym in symbols:
        rows.extend(_bar_row(sym))
    upsert_daily_bars(rows, db_path=db)

    monkeypatch.setattr(
        "signals.momentum.resolve_universe_spec",
        lambda _spec: symbols,
    )
    monkeypatch.setattr(
        "signals.momentum.load_sector_map",
        lambda: {sym: "SectorA" for sym in symbols},
    )
    monkeypatch.setattr(
        "signals.strategy_scan.trading_config",
        lambda: {"execution_mode": "live"},
    )
    monkeypatch.setattr(
        "signals.momentum.production_target_set",
        lambda **kwargs: [{"symbol": "AAA1"}, {"symbol": "BBB1"}],
    )

    execute_mock = AsyncMock()
    monkeypatch.setattr("signals.momentum.execute_rebalance", execute_mock)

    strategy = get_strategy("momentum_sp500", db_path=db)
    assert strategy is not None
    result = await _run_portfolio_strategy(strategy, db_path=db)

    execute_mock.assert_not_awaited()
    assert len(result) == 1
    assert result[0]["status"] == "planned"


@pytest.mark.asyncio
async def test_one_summary_push_zero_per_trade_pushes(
    tmp_path: Path, monkeypatch
) -> None:
    db, _account_id = _init_db(tmp_path)
    _patch_db(monkeypatch, db)
    upsert_daily_bars(_bar_row("AAA"), db_path=db)

    month = _current_rebalance_month()
    start_run(
        "momentum_sp500",
        month,
        {"sells": [], "buys": [{"symbol": "AAA", "qty": "10"}]},
        db_path=db,
    )

    _mock_submit(monkeypatch)
    monkeypatch.setattr("signals.momentum.asyncio.sleep", AsyncMock())
    monkeypatch.setattr(
        "signals.momentum.production_target_set",
        lambda **kwargs: [{"symbol": "AAA"}],
    )
    push_mock = AsyncMock(return_value=1)
    monkeypatch.setattr("signals.momentum.send_alert_push", push_mock)

    strategy = get_strategy("momentum_sp500", db_path=db)
    assert strategy is not None
    result = await execute_rebalance(strategy, db_path=db)

    assert result["status"] == "rebalanced"
    push_mock.assert_awaited_once()
    assert push_mock.await_args.kwargs["title"] == "Momentum rebalance"
    assert push_mock.await_args.kwargs["data"]["type"] == "rebalance_summary"

    run = get_run("momentum_sp500", month, db_path=db)
    assert run is not None
    assert run["status"] == "complete"


@pytest.mark.asyncio
async def test_shrunken_target_aborts_no_mass_sell(tmp_path: Path, monkeypatch) -> None:
    """A collapsed target (< MINIMUM_MOMENTUM_TARGET) aborts — never mass-sells the book."""
    db, account_id = _init_db(tmp_path)
    _patch_db(monkeypatch, db)
    submit = _mock_submit(monkeypatch)
    for s in ("AAA", "BBB"):
        record_open_position(
            account_id=account_id, symbol=s, strategy_id="momentum_sp500",
            qty="5", exit_mode="propose", params={}, db_path=db,
        )
    monkeypatch.setattr("signals.momentum.production_target_set", lambda **k: [{"symbol": "CCC"}])
    strategy = get_strategy("momentum_sp500", db_path=db)
    result = await execute_rebalance(strategy, db_path=db)
    assert result["status"] == "no_target"
    submit.assert_not_called()


@pytest.mark.asyncio
async def test_resume_skips_already_held_buy(tmp_path: Path, monkeypatch) -> None:
    """Resume is idempotent-by-state: a planned buy already in holdings is NOT re-bought."""
    db, account_id = _init_db(tmp_path)
    _patch_db(monkeypatch, db)
    submit = _mock_submit(monkeypatch)
    upsert_daily_bars(_bar_row("CCC"), db_path=db)
    record_open_position(
        account_id=account_id, symbol="CCC", strategy_id="momentum_sp500",
        qty="5", exit_mode="propose", params={}, db_path=db,
    )
    month = _current_rebalance_month()
    start_run("momentum_sp500", month, {"buys": [{"symbol": "CCC", "qty": "1"}], "sells": []}, db_path=db)
    monkeypatch.setattr("signals.momentum.production_target_set", lambda **k: [{"symbol": "CCC"}])
    strategy = get_strategy("momentum_sp500", db_path=db)
    await execute_rebalance(strategy, db_path=db)
    bought = [c.args[0].symbol for c in submit.call_args_list]
    assert "CCC" not in bought  # already held → not re-submitted on resume


@pytest.mark.asyncio
async def test_complete_run_blocks_re_execution(tmp_path: Path, monkeypatch) -> None:
    """A 'complete' run blocks re-execution even if last_rebalanced_month never stamped."""
    db, _account_id = _init_db(tmp_path)
    _patch_db(monkeypatch, db)
    submit = _mock_submit(monkeypatch)
    month = _current_rebalance_month()
    start_run("momentum_sp500", month, {"buys": [], "sells": []}, db_path=db)
    complete_run("momentum_sp500", month, db_path=db)
    strategy = get_strategy("momentum_sp500", db_path=db)  # last_rebalanced_month still unset
    result = await execute_rebalance(strategy, db_path=db)
    assert result["status"] == "already_rebalanced"
    submit.assert_not_called()


@pytest.mark.asyncio
async def test_stuck_run_abandoned_after_timeout(tmp_path: Path, monkeypatch) -> None:
    """A run stuck 'executing' past MAX_REBALANCE_AGE is abandoned (stamped + alerted),
    not retried forever."""
    db, _account_id = _init_db(tmp_path)
    _patch_db(monkeypatch, db)
    submit = _mock_submit(monkeypatch)
    push = AsyncMock(return_value=0)
    monkeypatch.setattr("signals.momentum.send_alert_push", push)
    month = _current_rebalance_month()
    start_run("momentum_sp500", month, {"buys": [{"symbol": "AAA", "qty": "1"}], "sells": []}, db_path=db)
    monkeypatch.setattr("signals.momentum._rebalance_run_age", lambda run: 9999.0)

    strategy = get_strategy("momentum_sp500", db_path=db)
    result = await execute_rebalance(strategy, db_path=db)

    assert result["status"] == "abandoned"
    submit.assert_not_called()           # gave up — no infinite retry of the stuck leg
    push.assert_awaited_once()           # founder alerted
    assert get_strategy("momentum_sp500", db_path=db)["last_rebalanced_month"] == month


@pytest.mark.asyncio
async def test_max_deployed_drops_lowest_residual_not_alphabetical(tmp_path: Path, monkeypatch) -> None:
    """A tight max_deployed_usd drops the LOWEST-residual names, not late-alphabet ones."""
    db, _account_id = _init_db(tmp_path)
    _patch_db(monkeypatch, db)
    upsert_daily_bars(_bar_row("ZHI", price=100) + _bar_row("ALO", price=100), db_path=db)
    monkeypatch.setattr(
        "signals.momentum.production_target_set",
        lambda **k: [
            {"symbol": "ZHI", "residual_momentum": 9.0},   # high conviction, late alphabet
            {"symbol": "ALO", "residual_momentum": 1.0},   # low conviction, early alphabet
        ] + [{"symbol": f"F{i:02d}", "residual_momentum": 0.0} for i in range(48)],
    )
    update_strategy("momentum_sp500", notional_usd=5000, max_deployed_usd=150, db_path=db)
    strategy = get_strategy("momentum_sp500", db_path=db)

    plan = _compute_rebalance_plan(strategy, db_path=db)
    # per_name=100; cap=150 fits one buy → the HIGH-residual ZHI, not alphabetically-first ALO.
    assert [b["symbol"] for b in plan["buys"]] == ["ZHI"]


@pytest.mark.asyncio
async def test_rebalance_deferred_when_market_closed(tmp_path: Path, monkeypatch) -> None:
    """The rebalance defers (no orders) outside the regular session — the month ticks at
    midnight ET, so without this it would fire after-hours and reject everything."""
    db, _account_id = _init_db(tmp_path)
    _patch_db(monkeypatch, db)
    submit = _mock_submit(monkeypatch)
    monkeypatch.setattr("signals.momentum.is_market_open", lambda: False)

    strategy = get_strategy("momentum_sp500", db_path=db)
    result = await execute_rebalance(strategy, db_path=db)

    assert result["status"] == "market_closed"
    submit.assert_not_called()
