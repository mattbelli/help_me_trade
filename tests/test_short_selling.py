"""Short-selling gate — RiskEngine relaxation behind the paper-only toggle.

Shorting (sell with no held long) is allowed ONLY when allow_short_selling is on
AND execution_mode is dry_run/paper. LIVE stays hard-blocked. A short-open is
subject to the same exposure caps as a buy (it increases exposure).
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from brokers.alpaca import AlpacaAdapter
from brokers.base import Account, Position
from db import order_executions as order_executions_mod
from db import sqlite as sqlite_mod
from trading.models import OrderIntent
from trading.risk_engine import RiskEngine


def _intent(**overrides) -> OrderIntent:
    base = dict(
        proposal_id="paper_test123",
        session_id=None,
        symbol="TSLA",  # NOT in the snapshot → no held long → short path
        side="sell",
        qty=Decimal("10"),
        order_type="market",
        limit_price=None,
        broker="alpaca",
    )
    base.update(overrides)
    return OrderIntent(**base)


def _save_snapshot(db_path: Path) -> None:
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
            side="long",
            market_value=1_500.0,
            unrealized_pl=50.0,
            avg_entry_price=145.0,
            current_price=150.0,
        )
    ]
    sqlite_mod.save_snapshot(account, positions, [], broker="alpaca", db_path=db_path)


def _stub_alpaca() -> AlpacaAdapter:
    stub = object.__new__(AlpacaAdapter)
    stub.symbol_exists = lambda _symbol: True  # type: ignore[method-assign]
    return stub


def _patch(monkeypatch, db_path: Path, *, allow_short: bool, mode: str = "paper") -> None:
    from db import base as db_base

    monkeypatch.setattr(db_base, "DEFAULT_DB_PATH", db_path)
    monkeypatch.setattr(sqlite_mod, "DEFAULT_DB_PATH", db_path)
    monkeypatch.setattr(order_executions_mod, "DEFAULT_DB_PATH", db_path)
    _save_snapshot(db_path)
    monkeypatch.setattr("trading.risk_engine.get_broker", lambda _name: _stub_alpaca())
    monkeypatch.setattr(
        "trading.risk_engine.market_hub.quote_snapshot",
        lambda _symbols: {"TSLA": {"price": 150.0}, "AAPL": {"price": 150.0}},
    )
    monkeypatch.setattr("trading.risk_engine.is_kill_switch_active", lambda: False)
    monkeypatch.setattr(
        "trading.risk_engine.trading_config",
        lambda: {
            "execution_mode": mode,
            "kill_switch_active": False,
            "max_order_value_usd": 50_000,
            "max_position_pct": 30,
            "max_open_orders": 10,
            "max_daily_orders": 20,
            "allow_short_selling": allow_short,
        },
    )


@pytest.mark.asyncio
async def test_short_blocked_when_toggle_off(monkeypatch, tmp_path: Path) -> None:
    db = tmp_path / "s.db"
    _patch(monkeypatch, db, allow_short=False)
    result = await RiskEngine.validate(_intent(), db_path=db)
    assert result.passed is False
    assert "short selling not enabled" in (result.blocked_reason or "")
    assert result.is_short_open is False


@pytest.mark.asyncio
async def test_short_allowed_when_toggle_on_paper(monkeypatch, tmp_path: Path) -> None:
    db = tmp_path / "s.db"
    _patch(monkeypatch, db, allow_short=True, mode="paper")
    result = await RiskEngine.validate(_intent(), db_path=db)
    assert result.passed is True
    assert result.is_short_open is True
    assert "short-to-open" in result.checks["sell_position"]


@pytest.mark.asyncio
async def test_short_blocked_in_live_even_with_toggle(monkeypatch, tmp_path: Path) -> None:
    db = tmp_path / "s.db"
    _patch(monkeypatch, db, allow_short=True, mode="live")
    result = await RiskEngine.validate(_intent(), db_path=db)
    assert result.passed is False
    assert "short selling not enabled" in (result.blocked_reason or "")


@pytest.mark.asyncio
async def test_short_respects_value_cap(monkeypatch, tmp_path: Path) -> None:
    db = tmp_path / "s.db"
    _patch(monkeypatch, db, allow_short=True)
    # 400 * 150 = 60k > 50k value cap.
    result = await RiskEngine.validate(_intent(qty=Decimal("400")), db_path=db)
    assert result.passed is False
    assert result.blocked_reason == "order exceeds max value"


@pytest.mark.asyncio
async def test_short_respects_concentration(monkeypatch, tmp_path: Path) -> None:
    db = tmp_path / "s.db"
    _patch(monkeypatch, db, allow_short=True)
    # 250 * 150 = 37.5k / 100k = 37.5% > 30% concentration cap (under value cap).
    result = await RiskEngine.validate(_intent(qty=Decimal("250")), db_path=db)
    assert result.passed is False
    assert result.blocked_reason == "concentration limit exceeded"


@pytest.mark.asyncio
async def test_sell_to_close_still_works(monkeypatch, tmp_path: Path) -> None:
    db = tmp_path / "s.db"
    _patch(monkeypatch, db, allow_short=False)  # shorting off, but a real close
    result = await RiskEngine.validate(
        _intent(symbol="AAPL", qty=Decimal("10")), db_path=db
    )
    assert result.passed is True
    assert result.is_short_open is False
    assert "closing" in result.checks["sell_position"]


@pytest.mark.asyncio
async def test_option_naked_write_blocked_even_with_short_on(
    monkeypatch, tmp_path: Path
) -> None:
    """The equity short toggle must NOT enable naked option writing (options stay
    long-only). A sell-to-open on an unheld option is blocked even when shorting
    is enabled."""
    db = tmp_path / "s.db"
    _patch(monkeypatch, db, allow_short=True, mode="paper")
    intent = _intent(
        symbol="TSLA",
        occ_symbol="TSLA260116C00200000",
        asset_class="us_option",
        order_type="limit",
        limit_price=Decimal("2.50"),
        qty=Decimal("1"),
    )
    result = await RiskEngine.validate(intent, db_path=db)
    assert result.passed is False
    assert result.is_short_open is False


@pytest.mark.asyncio
async def test_oversell_held_long_still_blocked(monkeypatch, tmp_path: Path) -> None:
    db = tmp_path / "s.db"
    _patch(monkeypatch, db, allow_short=True)  # even with shorting on
    # Held 10 AAPL long; selling 25 would flip into a short mid-close → blocked.
    result = await RiskEngine.validate(
        _intent(symbol="AAPL", qty=Decimal("25")), db_path=db
    )
    assert result.passed is False
    assert "exceeds available" in (result.blocked_reason or "")
