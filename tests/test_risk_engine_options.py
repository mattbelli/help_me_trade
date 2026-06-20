"""Risk engine validation for long single-leg option orders."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from brokers.alpaca import AlpacaAdapter
from brokers.base import Account
from db import order_executions as order_executions_mod
from db import sqlite as sqlite_mod
from imports.option_symbol import OptionSymbol, to_occ_symbol
from trading.models import OrderIntent
from trading.risk_engine import RiskEngine

_TSLA_CALL = to_occ_symbol(
    OptionSymbol(
        underlying="TSLA",
        expiration=date.today() + timedelta(days=30),
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


def _option_intent(**overrides) -> OrderIntent:
    base = dict(
        proposal_id="paper_opt123",
        session_id=None,
        symbol="TSLA",
        side="buy",
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


def _save_fresh_snapshot(db_path: Path, *, buying_power: float = 100_000.0) -> None:
    account = Account(
        id="acc1",
        status="ACTIVE",
        cash=50_000.0,
        buying_power=buying_power,
        portfolio_value=100_000.0,
    )
    sqlite_mod.save_snapshot(
        account,
        [],
        [],
        broker="alpaca",
        db_path=db_path,
    )


def _stub_alpaca(*, tradable: bool = True) -> AlpacaAdapter:
    stub = object.__new__(AlpacaAdapter)
    stub.symbol_exists = lambda _symbol: tradable  # type: ignore[method-assign]
    stub.option_contract_exists = lambda _occ: tradable  # type: ignore[method-assign]
    return stub


def _patch_passing_env(monkeypatch, db_path: Path) -> None:
    from db import base as db_base

    monkeypatch.setattr(db_base, "DEFAULT_DB_PATH", db_path)
    monkeypatch.setattr(sqlite_mod, "DEFAULT_DB_PATH", db_path)
    monkeypatch.setattr(order_executions_mod, "DEFAULT_DB_PATH", db_path)
    _save_fresh_snapshot(db_path)

    monkeypatch.setattr(
        "trading.risk_engine.get_broker",
        lambda _name: _stub_alpaca(),
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
async def test_option_limit_call_passes_notional_700(
    monkeypatch, tmp_path: Path
) -> None:
    db = tmp_path / "risk_opt.db"
    _patch_passing_env(monkeypatch, db)
    # $3.50 × 2 contracts × 100 = $700
    result = await RiskEngine.validate(_option_intent(), db_path=db)
    assert result.passed is True
    assert result.checks["order_value_cap"] == "pass"
    assert result.checks["live_quote"] == "pass: skipped for options"


@pytest.mark.asyncio
async def test_option_premium_300_rejected_by_value_cap(
    monkeypatch, tmp_path: Path
) -> None:
    db = tmp_path / "risk_opt.db"
    _patch_passing_env(monkeypatch, db)
    # $300 × 2 × 100 = $60,000 > $50k cap
    result = await RiskEngine.validate(
        _option_intent(limit_price=Decimal("300")),
        db_path=db,
    )
    assert result.passed is False
    assert result.blocked_reason == "order exceeds max value"
    assert result.checks["order_value_cap"] == "fail: order exceeds max value"


@pytest.mark.asyncio
async def test_option_expired_rejected(monkeypatch, tmp_path: Path) -> None:
    db = tmp_path / "risk_opt.db"
    _patch_passing_env(monkeypatch, db)
    result = await RiskEngine.validate(
        _option_intent(occ_symbol=_EXPIRED_CALL),
        db_path=db,
    )
    assert result.passed is False
    assert "expired" in (result.blocked_reason or "")


@pytest.mark.asyncio
async def test_option_sell_requires_held_position(
    monkeypatch, tmp_path: Path
) -> None:
    db = tmp_path / "risk_opt.db"
    _patch_passing_env(monkeypatch, db)
    result = await RiskEngine.validate(
        _option_intent(side="sell"),
        db_path=db,
    )
    assert result.passed is False
    assert result.checks["sell_position"] == "fail: no held long position"


@pytest.mark.asyncio
async def test_option_market_rejected(monkeypatch, tmp_path: Path) -> None:
    db = tmp_path / "risk_opt.db"
    _patch_passing_env(monkeypatch, db)
    result = await RiskEngine.validate(
        _option_intent(order_type="market"),
        db_path=db,
    )
    assert result.passed is False
    assert result.blocked_reason == "options require a limit order with an explicit premium"


@pytest.mark.asyncio
async def test_option_malformed_occ_rejected(monkeypatch, tmp_path: Path) -> None:
    db = tmp_path / "risk_opt.db"
    _patch_passing_env(monkeypatch, db)
    result = await RiskEngine.validate(
        _option_intent(occ_symbol="BADSYMBOL"),
        db_path=db,
    )
    assert result.passed is False
    assert result.blocked_reason == "invalid symbol"


@pytest.mark.asyncio
async def test_option_not_tradable_rejected(monkeypatch, tmp_path: Path) -> None:
    db = tmp_path / "risk_opt.db"
    _patch_passing_env(monkeypatch, db)
    monkeypatch.setattr(
        "trading.risk_engine.get_broker",
        lambda _name: _stub_alpaca(tradable=False),
    )
    result = await RiskEngine.validate(_option_intent(), db_path=db)
    assert result.passed is False
    assert "not tradable" in (result.blocked_reason or "")
