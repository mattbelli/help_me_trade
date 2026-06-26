"""Portfolio-kind strategy scan — read-only rank → diff (Inc-2)."""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from db.managed_positions import init_managed_position_tables, record_open_position
from db.momentum_bars import upsert_daily_bars
from db.multi_account import migrate_multi_account
from db.order_executions import init_execution_tables
from db.paper_orders import init_paper_order_tables
from db.sqlite import init_db
from db.strategies import get_strategy, init_strategy_tables, seed_default_strategies
from db.trades import init_trade_tables
from signals.momentum import production_target_set
from signals.strategy_scan import _run_portfolio_strategy, run_strategy_scan


def _trading_dates(end: str, count: int) -> list[str]:
    d = date.fromisoformat(end)
    out: list[str] = []
    while len(out) < count:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d -= timedelta(days=1)
    out.reverse()
    return out


def _synthetic_bar_rows(
    symbols: list[str],
    *,
    end: str = "2024-12-31",
    n_days: int = 280,
) -> tuple[list[tuple[str, str, float, float]], str]:
    dates = _trading_dates(end, n_days)
    rows: list[tuple[str, str, float, float]] = []
    for i, sym in enumerate(symbols):
        base = 50.0 + i
        for j, dt in enumerate(dates):
            close = base * (1.001 ** (j * (1.0 + i * 0.05)))
            open_ = close * 0.999
            rows.append((sym, dt, open_, close))
    return rows, dates[-1]


def _momentum_universe_symbols() -> list[str]:
    return [
        "AAA1",
        "AAA2",
        "AAA3",
        "BBB1",
        "BBB2",
        "BBB3",
        "CCC1",
        "CCC2",
        "CCC3",
        "DDD1",
        "DDD2",
        "DDD3",
        "EEE1",
        "EEE2",
        "EEE3",
        "FFF1",
        "FFF2",
        "FFF3",
        "GGG1",
        "GGG2",
        "GGG3",
        "HHH1",
        "HHH2",
        "HHH3",
        "III1",
        "III2",
        "III3",
    ]


def _momentum_sector_map(symbols: list[str]) -> dict[str, str]:
    sectors = ["SectorA", "SectorB", "SectorC", "SectorD", "SectorE", "SectorF", "SectorG", "SectorH", "SectorI"]
    return {sym: sectors[i // 3] for i, sym in enumerate(symbols)}


def _init_db(tmp_path: Path) -> Path:
    db = tmp_path / "portfolio_scan.db"
    init_db(db)
    init_trade_tables(db)
    init_execution_tables(db)
    init_paper_order_tables(db)
    migrate_multi_account(db)
    init_strategy_tables(db)
    init_managed_position_tables(db)
    seed_default_strategies(db_path=db)
    return db


@pytest.mark.asyncio
async def test_run_portfolio_strategy_diff_and_no_execution(
    tmp_path: Path, monkeypatch
) -> None:
    db = _init_db(tmp_path)
    symbols = _momentum_universe_symbols()
    rows, as_of = _synthetic_bar_rows(symbols)
    upsert_daily_bars(rows, db_path=db)

    monkeypatch.setattr(
        "signals.momentum.resolve_universe_spec",
        lambda _spec: symbols,
    )
    monkeypatch.setattr(
        "signals.momentum.load_sector_map",
        lambda: _momentum_sector_map(symbols),
    )

    target = {d["symbol"] for d in production_target_set(as_of=as_of, db_path=db)}
    assert target

    held_in_target = sorted(target)[:2]
    held_outside = ["ZZZ1", "ZZZ2"]
    for sym in held_in_target + held_outside:
        record_open_position(
            account_id="acct_paper",
            symbol=sym,
            strategy_id="momentum_sp500",
            qty="10",
            exit_mode="propose",
            params={},
            db_path=db,
        )

    submit_mock = AsyncMock()
    monkeypatch.setattr("trading.execution_service.ExecutionService.submit", submit_mock)
    propose_mock = AsyncMock()
    monkeypatch.setattr("signals.strategy_scan.propose_trade", propose_mock)
    auto_mock = AsyncMock()
    monkeypatch.setattr("trading.proposal_intake.auto_execute_entry", auto_mock)

    strategy = get_strategy("momentum_sp500", db_path=db)
    assert strategy is not None

    result = await _run_portfolio_strategy(strategy, db_path=db)

    assert len(result) == 1
    summary = result[0]
    assert summary["status"] == "planned"
    assert summary["strategy_id"] == "momentum_sp500"
    assert summary["n_target"] == len(target)
    assert summary["n_hold"] == len(held_in_target)
    assert summary["n_sell"] == len(held_outside)
    assert summary["n_buy"] == len(target - set(held_in_target))

    submit_mock.assert_not_awaited()
    propose_mock.assert_not_awaited()
    auto_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_run_portfolio_strategy_empty_target_returns_empty(
    tmp_path: Path,
) -> None:
    db = _init_db(tmp_path)
    strategy = get_strategy("momentum_sp500", db_path=db)
    assert strategy is not None

    result = await _run_portfolio_strategy(strategy, db_path=db)
    assert result == []


@pytest.mark.asyncio
async def test_run_strategy_scan_includes_portfolio_when_enabled(
    tmp_path: Path, monkeypatch
) -> None:
    db = _init_db(tmp_path)
    symbols = _momentum_universe_symbols()
    rows, as_of = _synthetic_bar_rows(symbols)
    upsert_daily_bars(rows, db_path=db)

    monkeypatch.setattr(
        "signals.momentum.resolve_universe_spec",
        lambda _spec: symbols,
    )
    monkeypatch.setattr(
        "signals.momentum.load_sector_map",
        lambda: _momentum_sector_map(symbols),
    )

    from db.strategies import update_strategy

    update_strategy("momentum_sp500", enabled=True, db_path=db)

    submit_mock = AsyncMock()
    monkeypatch.setattr("trading.execution_service.ExecutionService.submit", submit_mock)

    results = await run_strategy_scan(db_path=db)

    planned = [r for r in results if r.get("status") == "planned"]
    assert len(planned) == 1
    assert planned[0]["strategy_id"] == "momentum_sp500"
    submit_mock.assert_not_awaited()
