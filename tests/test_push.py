"""Proposal push delivery — gated, fail-safe."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from brokers.alpaca import AlpacaAdapter
from brokers.base import Account, Position
from db import base as db_base
from db import order_executions as order_executions_mod
from db import paper_orders as paper_orders_mod
from db import sqlite as sqlite_mod
from db.device_tokens import init_device_token_tables, register_device_token
from signals.push import send_proposal_push
from trading.proposals import create_phone_proposal


def _stub_alpaca(*, tradable: bool = True) -> AlpacaAdapter:
    stub = object.__new__(AlpacaAdapter)
    stub.symbol_exists = lambda _symbol: tradable  # type: ignore[method-assign]
    return stub


def _patch_proposal_env(monkeypatch, db_path: Path) -> None:
    monkeypatch.setattr(db_base, "DEFAULT_DB_PATH", db_path)
    monkeypatch.setattr(sqlite_mod, "DEFAULT_DB_PATH", db_path)
    monkeypatch.setattr(order_executions_mod, "DEFAULT_DB_PATH", db_path)
    monkeypatch.setattr(paper_orders_mod, "DEFAULT_DB_PATH", db_path)

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
    monkeypatch.setattr(
        "trading.proposals.market_hub.broadcast_alert",
        AsyncMock(),
    )


def _sample_proposal(**overrides) -> dict:
    base = {
        "id": "paper_abc123",
        "summary": "BUY 2 AAPL @ market",
        "expires_at": "2026-06-23T12:00:00+00:00",
        "payload_hash": "hash123",
        "proposal": {
            "symbol": "AAPL",
            "side": "buy",
            "qty": 2,
            "limit_price": None,
        },
    }
    base.update(overrides)
    return base


@pytest.mark.asyncio
async def test_push_disabled_by_default_no_http(monkeypatch, tmp_path: Path) -> None:
    db = tmp_path / "push.db"
    init_device_token_tables(db)
    register_device_token(token="ExponentPushToken[t1]", db_path=db)

    async def _fail_if_called(*_args, **_kwargs):
        raise AssertionError("_send_expo must not be called when push is disabled")

    monkeypatch.setattr("signals.push._send_expo", _fail_if_called)

    sent = await send_proposal_push(_sample_proposal(), db_path=db)
    assert sent == 0


@pytest.mark.asyncio
async def test_push_enabled_sends_to_active_tokens(monkeypatch, tmp_path: Path) -> None:
    db = tmp_path / "push.db"
    init_device_token_tables(db)
    register_device_token(token="ExponentPushToken[t1]", db_path=db)
    register_device_token(token="ExponentPushToken[t2]", db_path=db)

    captured: list[list[dict]] = []

    async def _capture(messages, _cfg):
        captured.append(messages)
        return len(messages)

    monkeypatch.setattr(
        "signals.push.push_config",
        lambda: {"enabled": True, "provider": "expo", "expo_access_token": ""},
    )
    monkeypatch.setattr("signals.push._send_expo", _capture)

    proposal = _sample_proposal()
    sent = await send_proposal_push(proposal, db_path=db)

    assert sent == 2
    assert len(captured) == 1
    messages = captured[0]
    assert len(messages) == 2
    for msg in messages:
        assert msg["to"] in ("ExponentPushToken[t1]", "ExponentPushToken[t2]")
        assert msg["title"] == "Trade proposal"
        assert msg["body"] == proposal["summary"]
        assert msg["categoryId"] == "TRADE_PROPOSAL"
        assert msg["data"]["type"] == "trade_proposal"
        assert msg["data"]["proposal_id"] == proposal["id"]
        assert msg["data"]["payload_hash"] == proposal["payload_hash"]
        assert msg["data"]["expires_at"] == proposal["expires_at"]


@pytest.mark.asyncio
async def test_push_no_tokens_returns_zero(monkeypatch, tmp_path: Path) -> None:
    db = tmp_path / "push.db"
    init_device_token_tables(db)

    monkeypatch.setattr(
        "signals.push.push_config",
        lambda: {"enabled": True, "provider": "expo", "expo_access_token": ""},
    )

    sent = await send_proposal_push(_sample_proposal(), db_path=db)
    assert sent == 0


@pytest.mark.asyncio
async def test_push_failsafe_on_send_error(monkeypatch, tmp_path: Path) -> None:
    db = tmp_path / "push.db"
    init_device_token_tables(db)
    register_device_token(token="ExponentPushToken[t1]", db_path=db)

    async def _raise(*_args, **_kwargs):
        raise RuntimeError("network down")

    monkeypatch.setattr(
        "signals.push.push_config",
        lambda: {"enabled": True, "provider": "expo", "expo_access_token": ""},
    )
    monkeypatch.setattr("signals.push._send_expo", _raise)

    sent = await send_proposal_push(_sample_proposal(), db_path=db)
    assert sent == 0


@pytest.mark.asyncio
async def test_create_phone_proposal_survives_push_failure(
    monkeypatch, tmp_path: Path
) -> None:
    db = tmp_path / "push.db"
    _patch_proposal_env(monkeypatch, db)

    async def _raise(*_args, **_kwargs):
        raise RuntimeError("push blew up")

    monkeypatch.setattr("signals.push.send_proposal_push", _raise)

    row = await create_phone_proposal(
        {"symbol": "AAPL", "side": "buy", "qty": 2, "order_type": "market"},
        ttl_seconds=300,
        db_path=db,
    )

    assert row["status"] == "pending_phone"
    assert row["id"]
    assert row["payload_hash"]
    assert row["expires_at"]
