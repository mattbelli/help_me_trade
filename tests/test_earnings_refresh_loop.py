"""Daily earnings calendar refresh loop — api/main.py."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from config import settings as settings_mod
from db.strategies import init_strategy_tables, update_strategy


def _init_db(tmp_path: Path) -> Path:
    from db.multi_account import migrate_multi_account
    from db.order_executions import init_execution_tables
    from db.paper_orders import init_paper_order_tables
    from db.sqlite import init_db
    from db.trades import init_trade_tables

    db = tmp_path / "earnings_refresh.db"
    init_db(db)
    init_trade_tables(db)
    init_execution_tables(db)
    init_paper_order_tables(db)
    migrate_multi_account(db)
    init_strategy_tables(db)
    return db


@pytest.mark.asyncio
async def test_earnings_refresh_once_enabled_earnings_strategy(
    monkeypatch, tmp_path: Path
) -> None:
    db = _init_db(tmp_path)
    settings_path = tmp_path / "settings.json"
    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", settings_path)
    settings_mod.clear_settings_cache()

    from db.accounts import default_execution_account
    from db.strategies import seed_default_strategies

    seed_default_strategies(db_path=db)
    account_id = default_execution_account(db_path=db)["id"]
    update_strategy(
        "earnings_drift",
        enabled=True,
        account_id=account_id,
        universe="AAPL,MSFT",
        db_path=db,
    )

    refresh_mock = AsyncMock(return_value=3)
    monkeypatch.setattr("api.main.arefresh_earnings_calendar", refresh_mock)
    monkeypatch.setattr("api.main.list_strategies", lambda **kw: __import__(
        "db.strategies", fromlist=["list_strategies"]
    ).list_strategies(**kw, db_path=db))
    monkeypatch.setattr(
        "api.main.resolve_universe_spec",
        lambda spec: [s.strip().upper() for s in str(spec).split(",") if s.strip()],
    )

    from api.main import _earnings_refresh_once

    n = await _earnings_refresh_once()
    assert n == 3
    refresh_mock.assert_awaited_once_with(["AAPL", "MSFT"])


@pytest.mark.asyncio
async def test_earnings_refresh_once_skips_when_no_enabled_earnings(
    monkeypatch, tmp_path: Path
) -> None:
    db = _init_db(tmp_path)
    refresh_mock = AsyncMock(return_value=99)
    monkeypatch.setattr("api.main.arefresh_earnings_calendar", refresh_mock)
    monkeypatch.setattr(
        "api.main.list_strategies",
        lambda **kw: __import__(
            "db.strategies", fromlist=["list_strategies"]
        ).list_strategies(**kw, db_path=db),
    )

    from api.main import _earnings_refresh_once

    n = await _earnings_refresh_once()
    assert n == 0
    refresh_mock.assert_not_awaited()
