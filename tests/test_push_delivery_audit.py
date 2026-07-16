"""R1 push delivery ledger, retry, and zero-delivery alarm."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from db.device_tokens import (
    init_device_token_tables,
    list_active_device_tokens,
    register_device_token,
)
from db.push_deliveries import init_push_delivery_tables, list_deliveries
from signals.push import _send_apns, _send_expo, send_proposal_push
from trading.proposals import retry_undelivered_pushes


def _sample_proposal(**overrides) -> dict:
    base = {
        "id": "paper_abc123",
        "summary": "BUY 2 AAPL @ market",
        "expires_at": (datetime.now(UTC) + timedelta(minutes=5)).isoformat(),
        "payload_hash": "hash123",
        "proposal": {
            "symbol": "AAPL",
            "side": "buy",
            "qty": 2,
            "order_type": "market",
        },
    }
    base.update(overrides)
    return base


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


def _apns_cfg(key_path: Path, **over) -> dict:
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
    def __init__(self, status_code: int = 200, *, text: str = "", json_body: dict | None = None):
        self.status_code = status_code
        self.text = text
        self._json_body = json_body

    def json(self):
        if self._json_body is not None:
            return self._json_body
        raise json.JSONDecodeError("no json", self.text, 0)

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class _FakeClient:
    def __init__(self, responses: list[_FakeResp] | None = None):
        self.responses = list(responses or [_FakeResp(200)])
        self.posts: list[dict] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_a):
        return False

    async def post(self, url, headers=None, json=None):
        self.posts.append({"url": url, "headers": headers, "json": json})
        idx = len(self.posts) - 1
        return self.responses[min(idx, len(self.responses) - 1)]


@pytest.mark.asyncio
async def test_apns_200_records_sent_row(monkeypatch, tmp_path: Path) -> None:
    db = tmp_path / "push.db"
    init_device_token_tables(db)
    init_push_delivery_tables(db)
    register_device_token(token="apnsdevtoken1", token_type="apns", db_path=db)

    key_path = _make_test_p8(tmp_path)
    cfg = _apns_cfg(key_path)
    monkeypatch.setattr("signals.push.httpx.AsyncClient", lambda **_k: _FakeClient())

    sent = await _send_apns(_sample_proposal(), cfg, db)

    assert sent == 1
    rows = list_deliveries("paper_abc123", db_path=db)
    assert len(rows) == 1
    assert rows[0]["status"] == "sent"
    assert rows[0]["channel"] == "apns"
    assert rows[0]["attempts"] == 1


@pytest.mark.asyncio
async def test_apns_non_200_records_failed_with_detail(monkeypatch, tmp_path: Path) -> None:
    db = tmp_path / "push.db"
    init_device_token_tables(db)
    init_push_delivery_tables(db)
    register_device_token(token="apnsdevtoken1", token_type="apns", db_path=db)

    key_path = _make_test_p8(tmp_path)
    cfg = _apns_cfg(key_path)
    monkeypatch.setattr(
        "signals.push.httpx.AsyncClient",
        lambda **_k: _FakeClient([_FakeResp(400, text='{"reason":"BadDeviceToken"}')]),
    )

    sent = await _send_apns(_sample_proposal(), cfg, db)

    assert sent == 0
    rows = list_deliveries("paper_abc123", db_path=db)
    assert rows[0]["status"] == "failed"
    assert "400" in rows[0]["detail"]
    assert "BadDeviceToken" in rows[0]["detail"]


@pytest.mark.asyncio
async def test_apns_410_deactivates_token(monkeypatch, tmp_path: Path) -> None:
    db = tmp_path / "push.db"
    init_device_token_tables(db)
    init_push_delivery_tables(db)
    register_device_token(token="apnsdevtoken1", token_type="apns", db_path=db)

    key_path = _make_test_p8(tmp_path)
    cfg = _apns_cfg(key_path)
    monkeypatch.setattr(
        "signals.push.httpx.AsyncClient",
        lambda **_k: _FakeClient([_FakeResp(410, json_body={"reason": "Unregistered"})]),
    )

    await _send_apns(_sample_proposal(), cfg, db)

    rows = list_deliveries("paper_abc123", db_path=db)
    assert rows[0]["status"] == "failed"
    assert rows[0]["detail"] == "unregistered — token deactivated"
    active = list_active_device_tokens(token_type="apns", db_path=db)
    assert active == []


@pytest.mark.asyncio
async def test_expo_tickets_parsed_per_token_in_order(monkeypatch, tmp_path: Path) -> None:
    db = tmp_path / "push.db"
    init_push_delivery_tables(db)

    class _ExpoClient:
        def __init__(self, *_a, **_k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_a):
            return False

        async def post(self, url, json=None, headers=None):
            return _FakeResp(
                200,
                json_body={
                    "data": [
                        {"status": "ok", "id": "ticket-1"},
                        {
                            "status": "error",
                            "message": "bad token",
                            "details": {"error": "InvalidCredentials"},
                        },
                    ]
                },
            )

    monkeypatch.setattr("signals.push.httpx.AsyncClient", _ExpoClient)

    messages = [
        {"to": "ExponentPushToken[t1]"},
        {"to": "ExponentPushToken[t2]"},
    ]
    sent = await _send_expo(
        messages,
        {"expo_access_token": ""},
        proposal_id="paper_abc123",
        db_path=db,
    )

    assert sent == 1
    rows = list_deliveries("paper_abc123", db_path=db)
    by_token = {r["device_token"]: r for r in rows}
    assert by_token["ExponentPushToken[t1]"]["status"] == "sent"
    assert by_token["ExponentPushToken[t2]"]["status"] == "failed"
    assert "bad token" in by_token["ExponentPushToken[t2]"]["detail"]


@pytest.mark.asyncio
async def test_expo_device_not_registered_deactivates(monkeypatch, tmp_path: Path) -> None:
    db = tmp_path / "push.db"
    init_device_token_tables(db)
    init_push_delivery_tables(db)
    register_device_token(token="ExponentPushToken[dead]", db_path=db)

    class _ExpoClient:
        def __init__(self, *_a, **_k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_a):
            return False

        async def post(self, url, json=None, headers=None):
            return _FakeResp(
                200,
                json_body={
                    "data": [
                        {
                            "status": "error",
                            "message": "not registered",
                            "details": {"error": "DeviceNotRegistered"},
                        }
                    ]
                },
            )

    monkeypatch.setattr("signals.push.httpx.AsyncClient", _ExpoClient)

    await _send_expo(
        [{"to": "ExponentPushToken[dead]"}],
        {"expo_access_token": ""},
        proposal_id="paper_abc123",
        db_path=db,
    )

    rows = list_deliveries("paper_abc123", db_path=db)
    assert rows[0]["detail"] == "unregistered — token deactivated"
    assert list_active_device_tokens(db_path=db) == []


@pytest.mark.asyncio
async def test_delivery_alarm_on_zero_tokens(monkeypatch, tmp_path: Path) -> None:
    db = tmp_path / "push.db"
    init_device_token_tables(db)
    notify_mock = AsyncMock(return_value={})
    monkeypatch.setattr("signals.push._fire_delivery_alarm", notify_mock)
    monkeypatch.setattr(
        "signals.push.push_config",
        lambda: {"enabled": True, "provider": "expo", "expo_access_token": ""},
    )

    await send_proposal_push(_sample_proposal(), db_path=db)

    notify_mock.assert_awaited_once()
    kwargs = notify_mock.await_args.kwargs
    assert kwargs["reason"] == "no registered device tokens"


@pytest.mark.asyncio
async def test_delivery_alarm_on_all_failed(monkeypatch, tmp_path: Path) -> None:
    db = tmp_path / "push.db"
    init_device_token_tables(db)
    register_device_token(token="ExponentPushToken[t1]", db_path=db)

    class _ExpoClient:
        def __init__(self, *_a, **_k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_a):
            return False

        async def post(self, url, json=None, headers=None):
            return _FakeResp(
                200,
                json_body={
                    "data": [
                        {
                            "status": "error",
                            "message": "failed",
                            "details": {"error": "MessageTooBig"},
                        }
                    ]
                },
            )

    notify_mock = AsyncMock(return_value={})
    monkeypatch.setattr("signals.push._fire_delivery_alarm", notify_mock)
    monkeypatch.setattr("signals.push.httpx.AsyncClient", _ExpoClient)
    monkeypatch.setattr(
        "signals.push.push_config",
        lambda: {"enabled": True, "provider": "expo", "expo_access_token": ""},
    )

    sent = await send_proposal_push(_sample_proposal(), db_path=db)

    assert sent == 0
    notify_mock.assert_awaited_once()
    assert notify_mock.await_args.kwargs["reason"] == "all push attempts failed"


@pytest.mark.asyncio
async def test_ledger_write_failure_does_not_break_send(monkeypatch, tmp_path: Path) -> None:
    db = tmp_path / "push.db"
    init_device_token_tables(db)
    register_device_token(token="ExponentPushToken[t1]", db_path=db)

    class _ExpoClient:
        def __init__(self, *_a, **_k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_a):
            return False

        async def post(self, url, json=None, headers=None):
            return _FakeResp(200, json_body={"data": [{"status": "ok", "id": "x"}]})

    def _boom(*_a, **_k):
        raise RuntimeError("db down")

    monkeypatch.setattr("signals.push.record_delivery", _boom)
    monkeypatch.setattr("signals.push.httpx.AsyncClient", _ExpoClient)
    monkeypatch.setattr(
        "signals.push.push_config",
        lambda: {"enabled": True, "provider": "expo", "expo_access_token": ""},
    )
    alarm_mock = AsyncMock()
    monkeypatch.setattr("signals.push._fire_delivery_alarm", alarm_mock)

    sent = await send_proposal_push(_sample_proposal(), db_path=db)

    assert sent == 1
    alarm_mock.assert_not_awaited()


def _insert_pending_proposal(db: Path, proposal_id: str, *, expires_at: str) -> None:
    from db.paper_orders import init_paper_order_tables

    init_paper_order_tables(db)
    conn = sqlite3.connect(db)
    conn.execute(
        """
        INSERT INTO paper_order_proposals (
            id, session_id, symbol, side, qty, order_type, limit_price,
            status, proposal_json, created_at, expires_at, payload_hash, delivery
        ) VALUES (?, NULL, 'AAPL', 'buy', 1, 'market', NULL,
                  'pending_phone', '{}', ?, ?, 'hash', 'phone')
        """,
        (proposal_id, datetime.now(UTC).isoformat(), expires_at),
    )
    conn.commit()
    conn.close()


@pytest.mark.asyncio
async def test_retry_resends_only_failed_under_max_attempts(
    monkeypatch, tmp_path: Path
) -> None:
    db = tmp_path / "retry.db"
    init_push_delivery_tables(db)
    proposal_id = "paper_retry1"
    future = (datetime.now(UTC) + timedelta(minutes=5)).isoformat()
    _insert_pending_proposal(db, proposal_id, expires_at=future)

    from db.push_deliveries import record_delivery

    record_delivery(proposal_id, "tok_a", "expo", "failed", detail="err", db_path=db)
    record_delivery(proposal_id, "tok_b", "expo", "sent", db_path=db)
    record_delivery(
        proposal_id,
        "tok_c",
        "expo",
        "failed",
        detail="err",
        db_path=db,
    )
    conn = sqlite3.connect(db)
    conn.execute(
        "UPDATE push_deliveries SET attempts = 3 WHERE proposal_id = ? AND device_token = ?",
        (proposal_id, "tok_c"),
    )
    conn.commit()
    conn.close()

    sent_tokens: list[str] = []
    sent_hashes: list[object] = []

    async def _capture(proposal, token, *, channel=None, db_path=db):
        sent_tokens.append(token)
        # payload_hash consistency: the phone approves against this hash — a
        # RESENT push must carry the exact hash of the original proposal row.
        sent_hashes.append(proposal.get("payload_hash"))
        return True

    monkeypatch.setattr("signals.push.send_proposal_push_to_token", _capture)
    monkeypatch.setattr("trading.proposals.DEFAULT_DB_PATH", db)
    from db import paper_orders as paper_orders_mod

    monkeypatch.setattr(paper_orders_mod, "DEFAULT_DB_PATH", db)

    count = await retry_undelivered_pushes(db_path=db)

    assert count == 1
    assert sent_tokens == ["tok_a"]
    assert sent_hashes == ["hash"]  # same payload_hash as the seeded proposal row


@pytest.mark.asyncio
async def test_retry_skips_expired_and_non_pending(monkeypatch, tmp_path: Path) -> None:
    db = tmp_path / "retry.db"
    init_push_delivery_tables(db)
    from db.push_deliveries import record_delivery

    expired_id = "paper_expired"
    past = (datetime.now(UTC) - timedelta(seconds=30)).isoformat()
    _insert_pending_proposal(db, expired_id, expires_at=past)

    decided_id = "paper_decided"
    future = (datetime.now(UTC) + timedelta(minutes=5)).isoformat()
    _insert_pending_proposal(db, decided_id, expires_at=future)
    conn = sqlite3.connect(db)
    conn.execute(
        "UPDATE paper_order_proposals SET status = 'executed' WHERE id = ?",
        (decided_id,),
    )
    conn.commit()
    conn.close()

    record_delivery(expired_id, "tok_e", "expo", "failed", db_path=db)
    record_delivery(decided_id, "tok_d", "expo", "failed", db_path=db)

    sent_tokens: list[str] = []

    async def _capture(proposal, token, *, channel=None, db_path=db):
        sent_tokens.append(token)
        return False

    monkeypatch.setattr("signals.push.send_proposal_push_to_token", _capture)
    from db import paper_orders as paper_orders_mod

    monkeypatch.setattr(paper_orders_mod, "DEFAULT_DB_PATH", db)

    count = await retry_undelivered_pushes(db_path=db)

    assert count == 0
    assert sent_tokens == []


@pytest.mark.asyncio
async def test_retry_increments_attempts(monkeypatch, tmp_path: Path) -> None:
    db = tmp_path / "retry.db"
    init_push_delivery_tables(db)
    proposal_id = "paper_retry2"
    future = (datetime.now(UTC) + timedelta(minutes=5)).isoformat()
    _insert_pending_proposal(db, proposal_id, expires_at=future)

    from db.push_deliveries import record_delivery

    record_delivery(proposal_id, "tok_a", "expo", "failed", detail="err", db_path=db)

    class _ExpoClient:
        def __init__(self, *_a, **_k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_a):
            return False

        async def post(self, url, json=None, headers=None):
            return _FakeResp(
                200,
                json_body={"data": [{"status": "error", "message": "still bad"}]},
            )

    monkeypatch.setattr("signals.push.httpx.AsyncClient", _ExpoClient)
    monkeypatch.setattr(
        "signals.push.push_config",
        lambda: {"enabled": True, "provider": "expo", "expo_access_token": ""},
    )
    from db import paper_orders as paper_orders_mod

    monkeypatch.setattr(paper_orders_mod, "DEFAULT_DB_PATH", db)

    await retry_undelivered_pushes(db_path=db)

    rows = list_deliveries(proposal_id, db_path=db)
    assert rows[0]["attempts"] == 2
    assert rows[0]["status"] == "failed"


@pytest.mark.asyncio
async def test_retry_exhaustion_fires_delivery_alarm_once(monkeypatch, tmp_path: Path) -> None:
    db = tmp_path / "retry.db"
    init_push_delivery_tables(db)
    proposal_id = "paper_alarm"
    future = (datetime.now(UTC) + timedelta(minutes=5)).isoformat()
    _insert_pending_proposal(db, proposal_id, expires_at=future)

    from db.push_deliveries import record_delivery

    record_delivery(proposal_id, "tok_a", "expo", "failed", detail="err", db_path=db)
    conn = sqlite3.connect(db)
    conn.execute(
        "UPDATE push_deliveries SET attempts = 3 WHERE proposal_id = ?",
        (proposal_id,),
    )
    conn.commit()
    conn.close()

    alarm_mock = AsyncMock()
    monkeypatch.setattr("signals.push._fire_delivery_alarm", alarm_mock)
    from db import paper_orders as paper_orders_mod

    monkeypatch.setattr(paper_orders_mod, "DEFAULT_DB_PATH", db)

    await retry_undelivered_pushes(db_path=db)

    alarm_mock.assert_awaited_once()
    assert "retries exhausted" in alarm_mock.await_args.kwargs["reason"]
