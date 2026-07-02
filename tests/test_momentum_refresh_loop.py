"""Daily momentum bars refresh loop — api/main.py."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from config import settings as settings_mod


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
    monkeypatch.setattr("api.main.refresh_universe_bars", refresh_mock)
    monkeypatch.setattr(
        "api.main.list_strategies",
        lambda **kw: __import__(
            "db.strategies", fromlist=["list_strategies"]
        ).list_strategies(**kw, db_path=db),
    )

    from api.main import _momentum_bars_refresh_once

    n = await _momentum_bars_refresh_once()
    assert n == 42
    refresh_mock.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_momentum_bars_refresh_once_skips_when_no_enabled_momentum(
    monkeypatch, tmp_path: Path
) -> None:
    db = _init_db(tmp_path)
    refresh_mock = AsyncMock(return_value=99)
    monkeypatch.setattr("api.main.refresh_universe_bars", refresh_mock)
    monkeypatch.setattr(
        "api.main.list_strategies",
        lambda **kw: __import__(
            "db.strategies", fromlist=["list_strategies"]
        ).list_strategies(**kw, db_path=db),
    )

    from api.main import _momentum_bars_refresh_once

    n = await _momentum_bars_refresh_once()
    assert n == 0
    refresh_mock.assert_not_awaited()
