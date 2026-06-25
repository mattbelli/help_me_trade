"""Risk engine validation — one failure per check + happy path."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from brokers.alpaca import AlpacaAdapter
from brokers.base import Account, Order, Position
from config import settings as settings_mod
from db import order_executions as order_executions_mod
from db import sqlite as sqlite_mod
from trading.models import OrderIntent
from trading.risk_engine import RiskEngine


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


def _save_fresh_snapshot(db_path: Path, *, buying_power: float = 100_000.0) -> None:
    account = Account(
        id="acc1",
        status="ACTIVE",
        cash=50_000.0,
        buying_power=buying_power,
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
    sqlite_mod.save_snapshot(
        account,
        positions,
        [],
        broker="alpaca",
        db_path=db_path,
    )


def _stub_alpaca(*, tradable: bool = True) -> AlpacaAdapter:
    stub = object.__new__(AlpacaAdapter)
    stub.symbol_exists = lambda _symbol: tradable  # type: ignore[method-assign]
    return stub


def _patch_db(monkeypatch, db_path: Path) -> None:
    from db import base as db_base

    monkeypatch.setattr(db_base, "DEFAULT_DB_PATH", db_path)
    monkeypatch.setattr(sqlite_mod, "DEFAULT_DB_PATH", db_path)
    monkeypatch.setattr(order_executions_mod, "DEFAULT_DB_PATH", db_path)


def _patch_passing_env(monkeypatch, db_path: Path) -> None:
    _patch_db(monkeypatch, db_path)
    _save_fresh_snapshot(db_path)

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
            "execution_mode": "dry_run",
            "kill_switch_active": False,
            "max_order_value_usd": 50_000,
            "max_position_pct": 30,
            "max_open_orders": 10,
            "max_daily_orders": 20,
        },
    )


@pytest.mark.asyncio
async def test_risk_engine_all_pass(monkeypatch, tmp_path: Path) -> None:
    db = tmp_path / "risk.db"
    _patch_passing_env(monkeypatch, db)
    result = await RiskEngine.validate(_intent(), db_path=db)
    assert result.passed is True
    assert result.blocked_reason is None
    assert result.checks["kill_switch"] == "pass"


@pytest.mark.asyncio
async def test_risk_engine_kill_switch(monkeypatch, tmp_path: Path) -> None:
    db = tmp_path / "risk.db"
    _patch_passing_env(monkeypatch, db)
    monkeypatch.setattr("trading.risk_engine.is_kill_switch_active", lambda: True)
    result = await RiskEngine.validate(_intent(), db_path=db)
    assert result.passed is False
    assert result.blocked_reason == "kill switch active"


@pytest.mark.asyncio
async def test_risk_engine_invalid_symbol(monkeypatch, tmp_path: Path) -> None:
    db = tmp_path / "risk.db"
    _patch_passing_env(monkeypatch, db)
    result = await RiskEngine.validate(_intent(symbol=""), db_path=db)
    assert result.passed is False
    assert result.blocked_reason == "invalid symbol"


@pytest.mark.asyncio
async def test_risk_engine_symbol_not_tradable(monkeypatch, tmp_path: Path) -> None:
    db = tmp_path / "risk.db"
    _patch_passing_env(monkeypatch, db)
    monkeypatch.setattr(
        "trading.risk_engine.get_broker",
        lambda _n: _stub_alpaca(tradable=False),
    )
    result = await RiskEngine.validate(_intent(), db_path=db)
    assert result.passed is False
    assert "not tradable" in (result.blocked_reason or "")


@pytest.mark.asyncio
async def test_risk_engine_symbol_lookup_error(monkeypatch, tmp_path: Path) -> None:
    db = tmp_path / "risk.db"
    _patch_passing_env(monkeypatch, db)

    def _boom(_name):
        raise RuntimeError("broker down")

    monkeypatch.setattr("trading.risk_engine.get_broker", _boom)
    result = await RiskEngine.validate(_intent(), db_path=db)
    assert result.passed is False
    assert result.blocked_reason == "symbol lookup failed"


@pytest.mark.parametrize(
    ("qty", "reason"),
    [
        (Decimal("0"), "qty out of bounds"),
        (Decimal("10001"), "qty out of bounds"),
        (Decimal("1.5"), "fractional shares not supported"),
    ],
)
@pytest.mark.asyncio
async def test_risk_engine_qty_bounds(
    monkeypatch, tmp_path: Path, qty: Decimal, reason: str
) -> None:
    db = tmp_path / "risk.db"
    _patch_passing_env(monkeypatch, db)
    result = await RiskEngine.validate(_intent(qty=qty), db_path=db)
    assert result.passed is False
    assert result.blocked_reason == reason


@pytest.mark.asyncio
async def test_risk_engine_missing_snapshot(monkeypatch, tmp_path: Path) -> None:
    db = tmp_path / "risk.db"
    _patch_db(monkeypatch, db)
    monkeypatch.setattr(
        "trading.risk_engine.get_broker",
        lambda _n: _stub_alpaca(),
    )
    monkeypatch.setattr("trading.risk_engine.is_kill_switch_active", lambda: False)
    monkeypatch.setattr(
        "trading.risk_engine.trading_config",
        lambda: settings_mod.DEFAULT_SETTINGS["trading"],
    )
    result = await RiskEngine.validate(_intent(), db_path=db)
    assert result.passed is False
    assert result.blocked_reason == "no portfolio snapshot"


@pytest.mark.asyncio
async def test_risk_engine_stale_snapshot(monkeypatch, tmp_path: Path) -> None:
    db = tmp_path / "risk.db"
    _patch_passing_env(monkeypatch, db)
    stale = (datetime.now(UTC) - timedelta(minutes=10)).isoformat()
    monkeypatch.setattr(
        "trading.risk_engine.get_latest_snapshot",
        lambda *_a, **_k: {
            "captured_at": stale,
            "account": {"buying_power": 100_000, "portfolio_value": 100_000},
            "open_orders": [],
            "positions": [],
        },
    )
    result = await RiskEngine.validate(_intent(), db_path=db)
    assert result.passed is False
    assert result.blocked_reason == "snapshot stale"


@pytest.mark.asyncio
async def test_risk_engine_no_live_quote(monkeypatch, tmp_path: Path) -> None:
    db = tmp_path / "risk.db"
    _patch_passing_env(monkeypatch, db)
    monkeypatch.setattr(
        "trading.risk_engine.market_hub.quote_snapshot",
        lambda _symbols: {},
    )

    async def _no_rest(_symbol):
        return None

    monkeypatch.setattr("trading.risk_engine._rest_quote_price", _no_rest)
    result = await RiskEngine.validate(_intent(), db_path=db)
    assert result.passed is False
    assert result.blocked_reason == "no live quote"


@pytest.mark.asyncio
async def test_live_quote_rest_fallback(monkeypatch, tmp_path: Path) -> None:
    db = tmp_path / "risk.db"
    _patch_passing_env(monkeypatch, db)
    monkeypatch.setattr(
        "trading.risk_engine.market_hub.quote_snapshot",
        lambda _symbols: {},
    )

    async def _rest_price(_symbol):
        return 150.0

    monkeypatch.setattr("trading.risk_engine._rest_quote_price", _rest_price)
    result = await RiskEngine.validate(_intent(), db_path=db)
    assert result.passed is True
    assert result.checks["live_quote"] == "pass"


@pytest.mark.asyncio
async def test_rest_quote_price_off_hours_returns_none(monkeypatch) -> None:
    """The REST fallback is RTH-only: off-hours it must NOT return a (stale) price,
    so the live-quote gate stays fail-closed exactly as before the fallback existed."""
    from trading.risk_engine import _rest_quote_price

    monkeypatch.setattr("trading.market_hours.is_market_open", lambda: False)

    async def _boom(*_a, **_k):  # provider must never be consulted off-hours
        raise AssertionError("provider should not be called outside RTH")

    monkeypatch.setattr("brokers.factory.get_market_data_provider", _boom)
    assert await _rest_quote_price("AAPL") is None


class _Bar:
    def __init__(self, time: str, close: float) -> None:
        self.time, self.close = time, close


class _BarsOnlyProvider:
    """Provider with no `aget_quotes` (e.g. Alpaca) — forces the bars fallback path."""

    def __init__(self, bar: "_Bar") -> None:
        self._bar = bar

    async def aget_stock_bars_batch(self, symbols, timeframe=None, days=None):
        return {symbols[0]: [self._bar]}


@pytest.mark.asyncio
async def test_rest_quote_price_bars_rejects_stale_open_bar(monkeypatch) -> None:
    """At the open, before today's first 5-min bar prints, bars[-1] is yesterday's
    close — it must be rejected (return None) so we never size on a prior-day gap."""
    from trading.risk_engine import _rest_quote_price

    monkeypatch.setattr("trading.market_hours.is_market_open", lambda: True)
    stale = _BarsOnlyProvider(_Bar("2020-01-02T20:55:00Z", 100.0))
    monkeypatch.setattr("brokers.factory.get_market_data_provider", lambda *a, **k: stale)
    assert await _rest_quote_price("AAPL") is None


@pytest.mark.asyncio
async def test_rest_quote_price_bars_accepts_todays_bar(monkeypatch) -> None:
    from zoneinfo import ZoneInfo

    from trading.risk_engine import _rest_quote_price

    monkeypatch.setattr("trading.market_hours.is_market_open", lambda: True)
    now_utc = datetime.now(ZoneInfo("America/New_York")).astimezone(UTC)
    fresh = _BarsOnlyProvider(_Bar(now_utc.strftime("%Y-%m-%dT%H:%M:%SZ"), 123.45))
    monkeypatch.setattr("brokers.factory.get_market_data_provider", lambda *a, **k: fresh)
    assert await _rest_quote_price("AAPL") == 123.45


@pytest.mark.asyncio
async def test_risk_engine_insufficient_buying_power(monkeypatch, tmp_path: Path) -> None:
    db = tmp_path / "risk.db"
    _patch_passing_env(monkeypatch, db)
    _save_fresh_snapshot(db, buying_power=100.0)
    result = await RiskEngine.validate(_intent(qty=Decimal("10")), db_path=db)
    assert result.passed is False
    assert result.blocked_reason == "insufficient buying power"


@pytest.mark.asyncio
async def test_risk_engine_order_value_cap(monkeypatch, tmp_path: Path) -> None:
    db = tmp_path / "risk.db"
    _patch_passing_env(monkeypatch, db)
    monkeypatch.setattr(
        "trading.risk_engine.trading_config",
        lambda: {
            **settings_mod.DEFAULT_SETTINGS["trading"],
            "max_order_value_usd": 500,
        },
    )
    result = await RiskEngine.validate(_intent(qty=Decimal("10")), db_path=db)
    assert result.passed is False
    assert result.blocked_reason == "order exceeds max value"


@pytest.mark.asyncio
async def test_risk_engine_concentration(monkeypatch, tmp_path: Path) -> None:
    db = tmp_path / "risk.db"
    _patch_passing_env(monkeypatch, db)
    account = Account(
        id="acc1",
        status="ACTIVE",
        cash=10_000.0,
        buying_power=100_000.0,
        portfolio_value=10_000.0,
    )
    positions = [
        Position(
            symbol="AAPL",
            qty=10.0,
            side="long",
            market_value=2_900.0,
            unrealized_pl=0.0,
            avg_entry_price=290.0,
            current_price=290.0,
        )
    ]
    sqlite_mod.save_snapshot(account, positions, [], broker="alpaca", db_path=db)
    monkeypatch.setattr(
        "trading.risk_engine.trading_config",
        lambda: {
            **settings_mod.DEFAULT_SETTINGS["trading"],
            "max_position_pct": 30,
        },
    )
    result = await RiskEngine.validate(_intent(qty=Decimal("10")), db_path=db)
    assert result.passed is False
    assert result.blocked_reason == "concentration limit exceeded"


@pytest.mark.asyncio
async def test_risk_engine_open_orders_cap(monkeypatch, tmp_path: Path) -> None:
    db = tmp_path / "risk.db"
    _patch_passing_env(monkeypatch, db)
    orders = [
        Order(
            id=f"ord{i}",
            symbol="MSFT",
            qty=1.0,
            side="buy",
            order_type="market",
            status="new",
            filled_qty=0.0,
            limit_price=None,
        )
        for i in range(10)
    ]
    account = Account(
        id="acc1",
        status="ACTIVE",
        cash=50_000.0,
        buying_power=100_000.0,
        portfolio_value=100_000.0,
    )
    sqlite_mod.save_snapshot(account, [], orders, broker="alpaca", db_path=db)
    result = await RiskEngine.validate(_intent(), db_path=db)
    assert result.passed is False
    assert result.blocked_reason == "too many open orders"


@pytest.mark.asyncio
async def test_risk_engine_limit_order_value_cap_uses_market_price(
    monkeypatch, tmp_path: Path
) -> None:
    db = tmp_path / "risk.db"
    _patch_passing_env(monkeypatch, db)
    monkeypatch.setattr(
        "trading.risk_engine.trading_config",
        lambda: {
            **settings_mod.DEFAULT_SETTINGS["trading"],
            "max_order_value_usd": 500,
        },
    )
    monkeypatch.setattr(
        "trading.risk_engine.market_hub.quote_snapshot",
        lambda _symbols: {"AAPL": {"price": 150.0}},
    )
    result = await RiskEngine.validate(
        _intent(
            qty=Decimal("1000"),
            order_type="limit",
            limit_price=Decimal("0.01"),
        ),
        db_path=db,
    )
    assert result.passed is False
    assert result.blocked_reason == "order exceeds max value"


def test_count_executions_today_excludes_rejected_dry_run_and_error(
    tmp_path: Path,
) -> None:
    db = tmp_path / "counts.db"
    for idx, status in enumerate(["rejected"] * 5 + ["dry_run"]):
        order_executions_mod.create_execution_record(
            execution_id=f"exec_count{idx:02d}",
            proposal_id=f"paper_{idx}",
            session_id=None,
            symbol="AAPL",
            side="buy",
            qty=Decimal("1"),
            order_type="market",
            limit_price=None,
            execution_mode="dry_run",
            status=status,
            broker="alpaca",
            db_path=db,
        )
    order_executions_mod.create_execution_record(
        execution_id="exec_error01",
        proposal_id="paper_err",
        session_id=None,
        symbol="AAPL",
        side="buy",
        qty=Decimal("1"),
        order_type="market",
        limit_price=None,
        execution_mode="dry_run",
        status="error",
        broker="alpaca",
        db_path=db,
    )
    assert order_executions_mod.count_executions_today(db_path=db) == 0


@pytest.mark.asyncio
async def test_risk_engine_daily_orders_cap(monkeypatch, tmp_path: Path) -> None:
    db = tmp_path / "risk.db"
    _patch_passing_env(monkeypatch, db)
    monkeypatch.setattr(
        "trading.risk_engine.trading_config",
        lambda: {
            **settings_mod.DEFAULT_SETTINGS["trading"],
            "max_daily_orders": 1,
        },
    )
    order_executions_mod.create_execution_record(
        execution_id="exec_seed001",
        proposal_id="paper_seed",
        session_id=None,
        symbol="AAPL",
        side="buy",
        qty=Decimal("1"),
        order_type="market",
        limit_price=None,
        execution_mode="paper",
        status="submitted",
        broker="alpaca",
        db_path=db,
    )
    result = await RiskEngine.validate(_intent(), db_path=db)
    assert result.passed is False
    assert result.blocked_reason == "daily order limit reached"
