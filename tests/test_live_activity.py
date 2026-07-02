"""Live Activity push — token storage, content-state, START/END, fail-soft hooks."""

from __future__ import annotations

import json
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
from db.live_activities import (
    clear_for_proposal,
    init_live_activity_tables,
    list_update_tokens,
    record_update_token,
)
from signals.push import (
    _la_content_state,
    _la_wire_content_state,
    end_proposal_live_activity,
    start_proposal_live_activity,
)
from trading.proposals import create_phone_proposal


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

    stub = object.__new__(AlpacaAdapter)
    stub.symbol_exists = lambda _symbol: True  # type: ignore[method-assign]

    monkeypatch.setattr("trading.risk_engine.get_broker", lambda _name: stub)
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
        "trading.proposals.notify",
        AsyncMock(return_value={}),
    )


def _sample_proposal_row(**overrides) -> dict:
    base = {
        "id": "paper_abc123",
        "symbol": "AAPL",
        "side": "buy",
        "qty": 10,
        "order_type": "market",
        "expires_at": "2026-06-23T12:00:00+00:00",
        "proposal": {
            "symbol": "AAPL",
            "side": "buy",
            "qty": 10,
            "order_type": "market",
        },
        "score": {"win_prob": 0.62},
    }
    base.update(overrides)
    return base


def _apns_cfg(key_path: Path) -> dict:
    return {
        "enabled": True,
        "provider": "apns",
        "apns_key_id": "KEY1234567",
        "apns_team_id": "TEAM123456",
        "apns_bundle_id": "com.helpmetrade.app",
        "apns_key_path": str(key_path),
        "apns_use_sandbox": True,
    }


def _make_test_p8(tmp_path: Path) -> Path:
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


def test_record_and_list_update_tokens_round_trip(tmp_path: Path) -> None:
    db = tmp_path / "la.db"
    init_live_activity_tables(db)
    record_update_token("p1", "dev1", "upd1", db_path=db)
    record_update_token("p1", "dev2", "upd2", db_path=db)

    rows = list_update_tokens("p1", db_path=db)
    assert len(rows) == 2
    by_dev = {r["device_token"]: r["update_token"] for r in rows}
    assert by_dev == {"dev1": "upd1", "dev2": "upd2"}


def test_record_update_token_upserts_latest(tmp_path: Path) -> None:
    db = tmp_path / "la.db"
    init_live_activity_tables(db)
    record_update_token("p1", "dev1", "old", db_path=db)
    record_update_token("p1", "dev1", "new", db_path=db)

    rows = list_update_tokens("p1", db_path=db)
    assert len(rows) == 1
    assert rows[0]["update_token"] == "new"


def test_clear_for_proposal(tmp_path: Path) -> None:
    db = tmp_path / "la.db"
    init_live_activity_tables(db)
    record_update_token("p1", "dev1", "upd1", db_path=db)
    clear_for_proposal("p1", db_path=db)
    assert list_update_tokens("p1", db_path=db) == []


def test_la_content_state_includes_win_prob_when_scored() -> None:
    state = _la_content_state(_sample_proposal_row(), status="pending")
    assert state["proposalId"] == "paper_abc123"
    assert state["symbol"] == "AAPL"
    assert state["side"] == "buy"
    assert state["qty"] == 10
    assert state["orderType"] == "market"
    assert state["winProb"] == 0.62
    assert state["status"] == "pending"
    assert "statusReason" not in state


def test_la_content_state_omits_win_prob_when_unscored() -> None:
    row = _sample_proposal_row(score=None)
    state = _la_content_state(row, status="pending")
    assert "winProb" not in state

    row2 = _sample_proposal_row(score={"other": 1})
    state2 = _la_content_state(row2, status="pending")
    assert "winProb" not in state2


def test_la_content_state_status_reason_only_on_failed() -> None:
    ok = _la_content_state(_sample_proposal_row(), status="executed", status_reason="x")
    assert "statusReason" not in ok

    failed = _la_content_state(
        _sample_proposal_row(), status="failed", status_reason="Risk block"
    )
    assert failed["statusReason"] == "Risk block"
    assert failed["status"] == "failed"


def test_la_wire_content_state_wraps_props_as_json_string() -> None:
    # iOS Codable decodes ContentState{name: String, props: String}; props must be a
    # JSON-encoded STRING, not a nested object, or the activity silently drops.
    wire = _la_wire_content_state(_sample_proposal_row(), status="pending")
    assert wire["name"] == "TradeProposalActivity"
    assert isinstance(wire["props"], str)
    decoded = json.loads(wire["props"])
    assert decoded == _la_content_state(_sample_proposal_row(), status="pending")
    assert decoded["status"] == "pending"
    assert decoded["symbol"] == "AAPL"


@pytest.mark.asyncio
async def test_start_live_activity_no_tokens_returns_zero(monkeypatch, tmp_path: Path) -> None:
    db = tmp_path / "la.db"
    init_device_token_tables(db)
    key_path = _make_test_p8(tmp_path)

    sent_calls: list[tuple] = []

    async def _capture(token, payload_aps, *, db_path):
        sent_calls.append((token, payload_aps))
        return True

    monkeypatch.setattr("signals.push.push_config", lambda: _apns_cfg(key_path))
    monkeypatch.setattr("signals.push._send_live_activity", _capture)

    count = await start_proposal_live_activity(_sample_proposal_row(), db_path=db)
    assert count == 0
    assert sent_calls == []


@pytest.mark.asyncio
async def test_start_live_activity_sends_start_event(monkeypatch, tmp_path: Path) -> None:
    db = tmp_path / "la.db"
    init_device_token_tables(db)
    register_device_token(token="la_start_tok", token_type="la_pushtostart", db_path=db)
    key_path = _make_test_p8(tmp_path)

    captured: list[dict] = []

    async def _capture(token, payload_aps, *, db_path):
        captured.append({"token": token, "aps": payload_aps})
        return True

    monkeypatch.setattr("signals.push.push_config", lambda: _apns_cfg(key_path))
    monkeypatch.setattr("signals.push._send_live_activity", _capture)

    count = await start_proposal_live_activity(_sample_proposal_row(), db_path=db)
    assert count == 1
    assert len(captured) == 1
    aps = captured[0]["aps"]
    assert captured[0]["token"] == "la_start_tok"
    assert aps["event"] == "start"
    assert aps["attributes-type"] == "LiveActivityAttributes"
    # expo-widgets envelope: content-state is {name, props-as-JSON-string}.
    cs = aps["content-state"]
    assert cs["name"] == "TradeProposalActivity"
    assert isinstance(cs["props"], str)
    props = json.loads(cs["props"])
    assert props["status"] == "pending"
    assert props["proposalId"] == "paper_abc123"
    assert aps["alert"]["title"] == "Trade proposal"
    assert "AAPL" in aps["alert"]["body"]


@pytest.mark.asyncio
async def test_end_live_activity_no_update_tokens_is_noop(monkeypatch, tmp_path: Path) -> None:
    db = tmp_path / "la.db"
    init_live_activity_tables(db)
    key_path = _make_test_p8(tmp_path)

    async def _fail(*_a, **_k):
        raise AssertionError("_send_live_activity must not be called")

    monkeypatch.setattr("signals.push.push_config", lambda: _apns_cfg(key_path))
    monkeypatch.setattr("signals.push._send_live_activity", _fail)

    count = await end_proposal_live_activity(
        "paper_missing", status="executed", proposal=_sample_proposal_row(), db_path=db
    )
    assert count == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "expected_event"),
    [
        ("approved", "update"),
        ("executed", "end"),
        ("failed", "end"),
        ("rejected", "end"),
        ("expired", "end"),
    ],
)
async def test_end_live_activity_event_by_status(
    monkeypatch, tmp_path: Path, status: str, expected_event: str
) -> None:
    db = tmp_path / "la.db"
    init_live_activity_tables(db)
    record_update_token("paper_abc123", "dev1", "upd_tok", db_path=db)
    key_path = _make_test_p8(tmp_path)

    captured: list[dict] = []

    async def _capture(token, payload_aps, *, db_path):
        captured.append(payload_aps)
        return True

    monkeypatch.setattr("signals.push.push_config", lambda: _apns_cfg(key_path))
    monkeypatch.setattr("signals.push._send_live_activity", _capture)

    count = await end_proposal_live_activity(
        "paper_abc123",
        status=status,
        status_reason="blocked" if status == "failed" else None,
        proposal=_sample_proposal_row(),
        db_path=db,
    )
    assert count == 1
    assert captured[0]["event"] == expected_event
    cs = captured[0]["content-state"]
    assert cs["name"] == "TradeProposalActivity"
    assert json.loads(cs["props"])["status"] == status
    if expected_event == "end":
        assert "dismissal-date" in captured[0]
        assert list_update_tokens("paper_abc123", db_path=db) == []
    else:
        assert list_update_tokens("paper_abc123", db_path=db) != []


@pytest.mark.asyncio
async def test_create_phone_proposal_survives_live_activity_failure(
    monkeypatch, tmp_path: Path
) -> None:
    db = tmp_path / "la.db"
    _patch_proposal_env(monkeypatch, db)

    async def _raise(*_args, **_kwargs):
        raise RuntimeError("live activity blew up")

    monkeypatch.setattr("signals.push.send_proposal_push", AsyncMock())
    monkeypatch.setattr("signals.push.start_proposal_live_activity", _raise)

    row = await create_phone_proposal(
        {"symbol": "AAPL", "side": "buy", "qty": 2, "order_type": "market"},
        ttl_seconds=300,
        db_path=db,
    )

    assert row["status"] == "pending_phone"
    assert row["id"]
    assert row["payload_hash"]
    assert row["expires_at"]


def test_api_register_live_activity(tmp_path: Path, monkeypatch) -> None:
    from fastapi.testclient import TestClient

    db = tmp_path / "la.db"
    monkeypatch.setattr(db_base, "DEFAULT_DB_PATH", db)
    monkeypatch.setattr("db.live_activities.DEFAULT_DB_PATH", db)
    init_live_activity_tables(db)

    from api.main import app

    with TestClient(app, base_url="http://127.0.0.1") as client:
        bad = client.post("/api/live-activities", json={"proposal_id": "p1"})
        assert bad.status_code == 400

        ok = client.post(
            "/api/live-activities",
            json={
                "proposal_id": "p1",
                "activity_update_token": "upd1",
                "device_token": "dev1",
            },
        )
        assert ok.status_code == 200
        assert ok.json() == {"ok": True}

    assert list_update_tokens("p1", db_path=db) == [
        {"device_token": "dev1", "update_token": "upd1"}
    ]
