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


# --- Direct APNs sender tests ---


def _make_test_p8(tmp_path):
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    key = ec.generate_private_key(ec.SECP256R1())
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    p = tmp_path / "apns.p8"
    p.write_bytes(pem)
    return p


def _apns_cfg(key_path, **over):
    cfg = {
        "enabled": True,
        "provider": "apns",
        "expo_access_token": "",
        "apns_key_id": "KEY1234567",
        "apns_team_id": "TEAM123456",
        "apns_bundle_id": "com.helpmetrade.app",
        "apns_key_path": str(key_path),
        "apns_use_sandbox": True,
    }
    cfg.update(over)
    return cfg


class _FakeResp:
    def __init__(self, status_code=200):
        self.status_code = status_code
        self.headers = {"apns-id": "test-id"}
        self.text = ""


class _FakeClient:
    last = None

    def __init__(self, *_a, **_k):
        self.posts = []

    async def __aenter__(self):
        _FakeClient.last = self
        return self

    async def __aexit__(self, *_a):
        return False

    async def post(self, url, headers=None, json=None):
        self.posts.append({"url": url, "headers": headers, "json": json})
        return _FakeResp(200)


def test_make_apns_jwt_shape(tmp_path):
    import base64

    from signals.push import _make_apns_jwt

    pem = _make_test_p8(tmp_path).read_bytes()
    jwt = _make_apns_jwt(pem, "KEY1234567", "TEAM123456")
    parts = jwt.split(".")
    assert len(parts) == 3
    sig = parts[2]
    raw = base64.urlsafe_b64decode(sig + "=" * (-len(sig) % 4))
    assert len(raw) == 64  # JOSE raw R||S, not DER


def test_build_apns_payload_has_category_and_data():
    from signals.push import _build_apns_payload

    payload = _build_apns_payload(_sample_proposal())
    assert payload["aps"]["category"] == "TRADE_PROPOSAL"
    assert payload["aps"]["alert"]["body"]
    assert payload["type"] == "trade_proposal"
    assert payload["proposal_id"] == "paper_abc123"
    assert payload["payload_hash"] == "hash123"


@pytest.mark.asyncio
async def test_apns_sends_to_active_apns_tokens(monkeypatch, tmp_path):
    db = tmp_path / "push.db"
    init_device_token_tables(db)
    register_device_token(token="apnsdevtoken1", token_type="apns", db_path=db)
    register_device_token(token="ExponentPushToken[x]", token_type="expo", db_path=db)

    key_path = _make_test_p8(tmp_path)
    monkeypatch.setattr("signals.push.push_config", lambda: _apns_cfg(key_path))
    monkeypatch.setattr("signals.push.httpx.AsyncClient", _FakeClient)

    sent = await send_proposal_push(_sample_proposal(), db_path=db)

    assert sent == 1  # the expo token is ignored by the apns path
    post = _FakeClient.last.posts[0]
    assert post["url"].endswith("/3/device/apnsdevtoken1")
    assert "api.sandbox.push.apple.com" in post["url"]
    assert post["headers"]["apns-topic"] == "com.helpmetrade.app"
    assert post["headers"]["authorization"].startswith("bearer ")
    assert post["headers"]["apns-push-type"] == "alert"
    assert post["json"]["aps"]["category"] == "TRADE_PROPOSAL"
    assert post["json"]["proposal_id"] == "paper_abc123"


@pytest.mark.asyncio
async def test_apns_incomplete_config_is_noop(monkeypatch, tmp_path):
    db = tmp_path / "push.db"
    init_device_token_tables(db)
    register_device_token(token="apnsdevtoken1", token_type="apns", db_path=db)

    # Missing key_path → must no-op (never raise, never POST).
    monkeypatch.setattr("signals.push.push_config", lambda: _apns_cfg("", apns_key_path=""))
    monkeypatch.setattr("signals.push.httpx.AsyncClient", _FakeClient)

    sent = await send_proposal_push(_sample_proposal(), db_path=db)
    assert sent == 0
