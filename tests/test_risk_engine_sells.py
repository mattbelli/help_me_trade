"""Risk engine sell-to-close — equity and option long exits only."""

from __future__ import annotations

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
