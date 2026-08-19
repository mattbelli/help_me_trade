"""Who may approve a proposal: device signature > admin token > legacy (ratcheted)."""

from __future__ import annotations

import base64
import time
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi import HTTPException
from fastapi.testclient import TestClient
from starlette.requests import Request

from api import approval_auth
from db.approval_devices import enroll_device, revoke_device


def _keypair() -> tuple[Ed25519PrivateKey, str]:
    priv = Ed25519PrivateKey.generate()
    pub_raw = priv.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    return priv, base64.b64encode(pub_raw).decode()


def _sign(priv: Ed25519PrivateKey, proposal_id: str, payload_hash: str, ts: str) -> str:
    return base64.b64encode(
        priv.sign(approval_auth.approval_message(proposal_id, payload_hash, ts))
    ).decode()


def _request(headers: dict[str, str], client: str = "127.0.0.1") -> Request:
    raw = [(k.lower().encode(), v.encode()) for k, v in headers.items()]
    return Request(
        {"type": "http", "method": "POST", "path": "/x", "headers": raw, "query_string": b"", "client": (client, 1234)}
    )


@pytest.fixture
def db(tmp_path: Path) -> Path:
    approval_auth._reset_failures()
    return tmp_path / "t.db"


@pytest.fixture
def admin(monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setattr("api.auth.ADMIN_TOKEN", "adm")
    return "adm"


def _device_headers(priv, device_id, pid, ph, ts=None):
    ts = ts or str(int(time.time()))
    return {
        "X-Approval-Device": device_id,
        "X-Approval-Signature": _sign(priv, pid, ph, ts),
        "X-Approval-Timestamp": ts,
    }


# --- legacy ratchet ---


def test_legacy_shared_token_allowed_until_first_enrollment(db: Path) -> None:
    who = approval_auth.authorize_approval(_request({"x-api-token": "t"}), "p1", "h1", db_path=db)
    assert who == "shared-token"


def test_env_pin_closes_legacy_on_a_fresh_db(db: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APPROVAL_REQUIRE_DEVICE", "1")
    with pytest.raises(HTTPException) as exc:
        approval_auth.authorize_approval(_request({"x-api-token": "t"}), "p1", "h1", db_path=db)
    assert exc.value.status_code == 403


def test_legacy_closed_once_any_device_enrolled(db: Path) -> None:
    _, pub = _keypair()
    enroll_device(device_id="dev1", public_key=pub, label="Matt iPhone", db_path=db)
    with pytest.raises(HTTPException) as exc:
        approval_auth.authorize_approval(_request({"x-api-token": "t"}), "p1", "h1", db_path=db)
    assert exc.value.status_code == 403


def test_legacy_stays_closed_after_revoking_the_only_device(db: Path) -> None:
    _, pub = _keypair()
    enroll_device(device_id="dev1", public_key=pub, db_path=db)
    revoke_device("dev1", db_path=db)
    with pytest.raises(HTTPException):
        approval_auth.authorize_approval(_request({"x-api-token": "t"}), "p1", "h1", db_path=db)


# --- admin ---


def test_admin_token_always_approves(db: Path, admin: str) -> None:
    _, pub = _keypair()
    enroll_device(device_id="dev1", public_key=pub, db_path=db)
    who = approval_auth.authorize_approval(_request({"x-api-token": admin}), "p1", "h1", db_path=db)
    assert who == "admin"


# --- device signature ---


def test_valid_device_signature(db: Path) -> None:
    priv, pub = _keypair()
    enroll_device(device_id="dev1", public_key=pub, label="Matt iPhone", db_path=db)
    req = _request(_device_headers(priv, "dev1", "p1", "h1"))
    assert approval_auth.authorize_approval(req, "p1", "h1", db_path=db) == "device:Matt iPhone"


def test_signature_bound_to_proposal_and_hash(db: Path) -> None:
    priv, pub = _keypair()
    enroll_device(device_id="dev1", public_key=pub, db_path=db)
    h = _device_headers(priv, "dev1", "p1", "h1")
    # Same signature replayed against a different proposal / different terms → no.
    for pid, ph in (("p2", "h1"), ("p1", "h2")):
        with pytest.raises(HTTPException) as exc:
            approval_auth.authorize_approval(_request(h), pid, ph, db_path=db)
        assert exc.value.status_code == 403


def test_wrong_key_rejected_and_does_not_fall_through_to_token(db: Path, admin: str) -> None:
    _, pub = _keypair()
    other, _ = _keypair()
    enroll_device(device_id="dev1", public_key=pub, db_path=db)
    h = _device_headers(other, "dev1", "p1", "h1")
    h["x-api-token"] = admin  # even the admin token can't rescue a bad signature
    with pytest.raises(HTTPException) as exc:
        approval_auth.authorize_approval(_request(h), "p1", "h1", db_path=db)
    assert exc.value.status_code == 403


def test_revoked_device_rejected(db: Path) -> None:
    priv, pub = _keypair()
    enroll_device(device_id="dev1", public_key=pub, db_path=db)
    revoke_device("dev1", db_path=db)
    with pytest.raises(HTTPException):
        approval_auth.authorize_approval(
            _request(_device_headers(priv, "dev1", "p1", "h1")), "p1", "h1", db_path=db
        )


def test_stale_timestamp_rejected(db: Path) -> None:
    priv, pub = _keypair()
    enroll_device(device_id="dev1", public_key=pub, db_path=db)
    old = str(int(time.time()) - approval_auth.MAX_CLOCK_SKEW_SEC - 5)
    with pytest.raises(HTTPException):
        approval_auth.authorize_approval(
            _request(_device_headers(priv, "dev1", "p1", "h1", ts=old)), "p1", "h1", db_path=db
        )


def test_failed_signatures_throttle_the_offending_client_not_the_phone(db: Path) -> None:
    priv, pub = _keypair()
    other, _ = _keypair()
    enroll_device(device_id="dev1", public_key=pub, db_path=db)
    # Attacker at 10.0.0.9 spams bad signatures under the REAL device id.
    for _ in range(approval_auth._FAIL_LIMIT):
        with pytest.raises(HTTPException) as exc:
            approval_auth.authorize_approval(
                _request(_device_headers(other, "dev1", "p1", "h1"), client="10.0.0.9"), "p1", "h1", db_path=db
            )
        assert exc.value.status_code == 403
    with pytest.raises(HTTPException) as exc:
        approval_auth.authorize_approval(
            _request(_device_headers(other, "dev1", "p1", "h1"), client="10.0.0.9"), "p1", "h1", db_path=db
        )
    assert exc.value.status_code == 429
    # The real phone, from its own address, still approves.
    who = approval_auth.authorize_approval(
        _request(_device_headers(priv, "dev1", "p1", "h1"), client="100.64.0.2"), "p1", "h1", db_path=db
    )
    assert who == "device:dev1"


# --- cross-side vector ---


def test_cross_side_vector() -> None:
    """Pins the wire format. The SAME seed/message run through the phone's
    mobile/src/auth/approval-sign.ts (compiled under Node, 2026-08-19) produced
    exactly these bytes — if this drifts, the phone's signatures stop verifying."""
    priv = Ed25519PrivateKey.from_private_bytes(bytes(range(32)))
    pub_b64 = base64.b64encode(
        priv.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    ).decode()
    assert pub_b64 == "A6EHv/POEL4dcN0Y50vAmWfk1jCbpQ1fHdyGZBJVMbg="
    assert _sign(priv, "prop-123", "deadbeef", "1755600000") == (
        "9608h1TQFXfcOm/QpX4q20AYggk1h2W0kSEqz6po4LuNvN8TFFBIEHKm+TIfs4CaChDRQDR+klXyHiF2RjxCBQ=="
    )


# --- route wiring ---


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch, db: Path) -> TestClient:
    monkeypatch.setattr("api.auth.API_TOKEN", "tok")
    monkeypatch.setattr("api.auth.ADMIN_TOKEN", "adm")
    monkeypatch.setattr("db.base.DEFAULT_DB_PATH", db)
    from api.main import app

    with TestClient(app, base_url="http://127.0.0.1") as c:
        yield c


def test_enroll_requires_admin_then_approve_route_demands_signature(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, db: Path
) -> None:
    priv, pub = _keypair()
    body = {"device_id": "dev1", "public_key": pub, "label": "Matt iPhone"}
    assert client.post("/api/approval-devices", json=body, headers={"X-API-Token": "tok"}).status_code == 403
    assert client.post("/api/approval-devices", json=body, headers={"X-API-Token": "adm"}).status_code == 200
    assert client.post(
        "/api/approval-devices", json={**body, "public_key": "nope"}, headers={"X-API-Token": "adm"}
    ).status_code == 400
    assert [d["device_id"] for d in client.get("/api/approval-devices", headers={"X-API-Token": "tok"}).json()["devices"]] == ["dev1"]

    approve = AsyncMock(return_value={"status": "executed"})
    monkeypatch.setattr("trading.proposals.approve_proposal", approve)

    # Shared token alone: refused now that a device exists. Nothing reached the chokepoint.
    r = client.post("/api/proposals/p1/approve", json={"payload_hash": "h1"}, headers={"X-API-Token": "tok"})
    assert r.status_code == 403
    approve.assert_not_awaited()

    # Signed by the enrolled phone: approver identity lands in the journal call.
    headers = {"X-API-Token": "tok", **_device_headers(priv, "dev1", "p1", "h1")}
    r = client.post("/api/proposals/p1/approve", json={"payload_hash": "h1"}, headers=headers)
    assert r.status_code == 200, r.text
    assert approve.await_args.kwargs["decided_by"] == "device:Matt iPhone"

    # Desk path: admin token.
    r = client.post("/api/proposals/p1/approve", json={"payload_hash": "h1"}, headers={"X-API-Token": "adm"})
    assert r.status_code == 200
    assert approve.await_args.kwargs["decided_by"] == "admin"

    # Revoke (admin) → that phone is out.
    assert client.post("/api/approval-devices/dev1/revoke", headers={"X-API-Token": "tok"}).status_code == 403
    assert client.post("/api/approval-devices/dev1/revoke", headers={"X-API-Token": "adm"}).status_code == 200
    r = client.post("/api/proposals/p1/approve", json={"payload_hash": "h1"}, headers=headers)
    assert r.status_code == 403
