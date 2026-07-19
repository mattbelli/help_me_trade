"""Daily momentum bars refresh job — api/research_jobs.py."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import pytest

from config import settings as settings_mod

_ET = ZoneInfo("America/New_York")


def _init_db(tmp_path: Path) -> Path:
    from db.multi_account import migrate_multi_account
    from db.order_executions import init_execution_tables
    from db.paper_orders import init_paper_order_tables
    from db.sqlite import init_db
    from db.strategies import init_strategy_tables
    from db.trades import init_trade_tables

    db = tmp_path / "momentum_refresh.db"
    init_db(db)
    init_trade_tables(db)
    init_execution_tables(db)
    init_paper_order_tables(db)
    migrate_multi_account(db)
    init_strategy_tables(db)
    return db


@pytest.mark.asyncio
async def test_momentum_bars_refresh_once_when_enabled(
    monkeypatch, tmp_path: Path
) -> None:
    db = _init_db(tmp_path)
    settings_path = tmp_path / "settings.json"
    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", settings_path)
    settings_mod.clear_settings_cache()

    from db.accounts import default_execution_account
    from db.base import connect, now_iso
    from db.strategies import seed_default_strategies

    seed_default_strategies(db_path=db)
    account_id = default_execution_account(db_path=db)["id"]
    now = now_iso()
    with connect(db) as conn:
        conn.execute(
            """
            INSERT INTO strategies (
                id, name, enabled, account_id, notional_usd,
                exit_mode, entry_mode, params_json, created_at, updated_at, universe
            ) VALUES (?, ?, 1, ?, 500, 'propose', 'auto', '{}', ?, ?, 'sp500')
            """,
            ("momentum_residual", "Momentum Residual", account_id, now, now),
        )
        conn.commit()

    refresh_mock = AsyncMock(return_value=42)
    monkeypatch.setattr("api.research_jobs.refresh_universe_bars", refresh_mock)
    monkeypatch.setattr(
        "api.research_jobs.list_strategies",
        lambda **kw: __import__(
            "db.strategies", fromlist=["list_strategies"]
        ).list_strategies(**kw, db_path=db),
    )
    monkeypatch.setattr("api.research_jobs._momentum_boot_freshness_checked", True)

    from api.research_jobs import _momentum_bars_refresh_once

    n = await _momentum_bars_refresh_once()
    assert n == 42
    refresh_mock.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_momentum_bars_refresh_once_skips_when_no_enabled_momentum(
    monkeypatch, tmp_path: Path
) -> None:
    db = _init_db(tmp_path)
    refresh_mock = AsyncMock(return_value=99)
    monkeypatch.setattr("api.research_jobs.refresh_universe_bars", refresh_mock)
    monkeypatch.setattr(
        "api.research_jobs.list_strategies",
        lambda **kw: __import__(
            "db.strategies", fromlist=["list_strategies"]
        ).list_strategies(**kw, db_path=db),
    )

    from api.research_jobs import _momentum_bars_refresh_once

    n = await _momentum_bars_refresh_once()
    assert n == 0
    refresh_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_boot_pass_skips_when_bars_current(monkeypatch) -> None:
    refresh_mock = AsyncMock(return_value=42)
    monkeypatch.setattr("api.research_jobs.refresh_universe_bars", refresh_mock)
    monkeypatch.setattr("api.research_jobs._momentum_strategy_enabled", lambda: True)
    monkeypatch.setattr("api.research_jobs._momentum_boot_freshness_checked", False)
    # Today always satisfies >= last-completed-weekday, whatever day it is.
    today_iso = datetime.now(_ET).date().isoformat()
    monkeypatch.setattr("api.research_jobs.latest_universe_bar_date", lambda: today_iso)

    from api.research_jobs import _momentum_bars_refresh_once

    n = await _momentum_bars_refresh_once()
    assert n == 0
    refresh_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_boot_pass_refreshes_when_bars_stale(monkeypatch) -> None:
    refresh_mock = AsyncMock(return_value=42)
    monkeypatch.setattr("api.research_jobs.refresh_universe_bars", refresh_mock)
    monkeypatch.setattr("api.research_jobs._momentum_strategy_enabled", lambda: True)
    monkeypatch.setattr("api.research_jobs._momentum_boot_freshness_checked", False)
    monkeypatch.setattr("api.research_jobs.latest_universe_bar_date", lambda: "2020-01-02")

    from api.research_jobs import _momentum_bars_refresh_once

    n = await _momentum_bars_refresh_once()
    assert n == 42
    refresh_mock.assert_awaited_once_with()
