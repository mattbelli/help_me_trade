"""Live Activity registration bind (Option A) — start-row + 2-field register."""

from __future__ import annotations

import logging
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from db import base as db_base
from db.base import connect
from db.device_tokens import init_device_token_tables, register_device_token
from db.live_activities import (
    bind_update_token_latest,
    init_live_activity_tables,
    list_update_tokens,
    record_activity_start,
    record_update_token,
)
from signals.push import end_proposal_live_activity, start_proposal_live_activity


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


def _register_via_api(client, *, activity_update_token: str, device_token: str, proposal_id: str = ""):
    body: dict[str, str] = {
        "activity_update_token": activity_update_token,
        "device_token": device_token,
    }
    if proposal_id:
        body["proposal_id"] = proposal_id
    return client.post("/api/live-activities", json=body)


@pytest.mark.asyncio
async def test_start_then_two_field_register_binds_and_lists(monkeypatch, tmp_path: Path, mutation_client) -> None:
    db = tmp_path / "la.db"
    monkeypatch.setattr(db_base, "DEFAULT_DB_PATH", db)
    monkeypatch.setattr("db.live_activities.DEFAULT_DB_PATH", db)
    init_device_token_tables(db)
    register_device_token(token="la_dev", token_type="la_pushtostart", db_path=db)
    key_path = _make_test_p8(tmp_path)

    monkeypatch.setattr("signals.push.push_config", lambda: _apns_cfg(key_path))
    monkeypatch.setattr("signals.push._send_live_activity", AsyncMock(return_value=True))

    proposal = _sample_proposal_row(id="paper_bind1")
    await start_proposal_live_activity(proposal, db_path=db)

    resp = _register_via_api(
        mutation_client,
        activity_update_token="upd_bind1",
        device_token="la_dev",
    )
    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "bound": "latest", "proposal_id": "paper_bind1"}

    rows = list_update_tokens("paper_bind1", db_path=db)
    assert rows == [{"device_token": "la_dev", "update_token": "upd_bind1"}]


@pytest.mark.asyncio
async def test_two_starts_same_device_binds_newest_and_warns(
    monkeypatch, tmp_path: Path, mutation_client, caplog
) -> None:
    db = tmp_path / "la.db"
    monkeypatch.setattr(db_base, "DEFAULT_DB_PATH", db)
    monkeypatch.setattr("db.live_activities.DEFAULT_DB_PATH", db)
    init_device_token_tables(db)
    register_device_token(token="la_dev", token_type="la_pushtostart", db_path=db)
    key_path = _make_test_p8(tmp_path)

    monkeypatch.setattr("signals.push.push_config", lambda: _apns_cfg(key_path))
    monkeypatch.setattr("signals.push._send_live_activity", AsyncMock(return_value=True))

    await start_proposal_live_activity(_sample_proposal_row(id="paper_old"), db_path=db)
    with caplog.at_level(logging.WARNING, logger="signals.push"):
        await start_proposal_live_activity(_sample_proposal_row(id="paper_new"), db_path=db)
    assert any(
        "concurrent LA starts for device" in rec.message for rec in caplog.records
    )

    resp = _register_via_api(
        mutation_client,
        activity_update_token="upd_new",
        device_token="la_dev",
    )
    assert resp.status_code == 200
    assert resp.json()["proposal_id"] == "paper_new"

    assert list_update_tokens("paper_new", db_path=db) == [
        {"device_token": "la_dev", "update_token": "upd_new"}
    ]
    assert list_update_tokens("paper_old", db_path=db) == []

    with connect(db) as conn:
        row = conn.execute(
            """
            SELECT update_token FROM live_activities
            WHERE proposal_id = ? AND device_token = ?
            """,
            ("paper_old", "la_dev"),
        ).fetchone()
    assert row is not None
    assert row["update_token"] is None


def test_two_field_register_without_pending_start_returns_ok_false(
    tmp_path: Path, monkeypatch, mutation_client
) -> None:
    db = tmp_path / "la.db"
    monkeypatch.setattr(db_base, "DEFAULT_DB_PATH", db)
    monkeypatch.setattr("db.live_activities.DEFAULT_DB_PATH", db)
    init_live_activity_tables(db)

    resp = _register_via_api(
        mutation_client,
        activity_update_token="upd_orphan",
        device_token="no_start_dev",
    )
    assert resp.status_code == 200
    assert resp.json() == {"ok": False, "reason": "no pending start for device"}


def test_three_field_register_exact_bind(tmp_path: Path, monkeypatch, mutation_client) -> None:
    db = tmp_path / "la.db"
    monkeypatch.setattr(db_base, "DEFAULT_DB_PATH", db)
    monkeypatch.setattr("db.live_activities.DEFAULT_DB_PATH", db)
    init_live_activity_tables(db)
    record_activity_start("paper_exact", "dev1", db_path=db)

    resp = _register_via_api(
        mutation_client,
        proposal_id="paper_other",
        activity_update_token="upd_exact",
        device_token="dev1",
    )
    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "bound": "exact"}

    assert list_update_tokens("paper_other", db_path=db) == [
        {"device_token": "dev1", "update_token": "upd_exact"}
    ]
    assert list_update_tokens("paper_exact", db_path=db) == []


def test_register_missing_required_fields_returns_400(mutation_client) -> None:
    missing_token = mutation_client.post(
        "/api/live-activities",
        json={"device_token": "dev1"},
    )
    assert missing_token.status_code == 400

    missing_device = mutation_client.post(
        "/api/live-activities",
        json={"activity_update_token": "upd1"},
    )
    assert missing_device.status_code == 400


def test_list_update_tokens_excludes_null_token_rows(tmp_path: Path) -> None:
    db = tmp_path / "la.db"
    init_live_activity_tables(db)
    record_activity_start("p_pending", "dev1", db_path=db)
    record_update_token("p_bound", "dev2", "upd2", db_path=db)

    assert list_update_tokens("p_pending", db_path=db) == []
    assert list_update_tokens("p_bound", db_path=db) == [
        {"device_token": "dev2", "update_token": "upd2"}
    ]


@pytest.mark.asyncio
async def test_end_live_activity_pushes_when_bound_via_two_field_register(
    monkeypatch, tmp_path: Path, mutation_client
) -> None:
    db = tmp_path / "la.db"
    monkeypatch.setattr(db_base, "DEFAULT_DB_PATH", db)
    monkeypatch.setattr("db.live_activities.DEFAULT_DB_PATH", db)
    init_device_token_tables(db)
    register_device_token(token="la_dev", token_type="la_pushtostart", db_path=db)
    key_path = _make_test_p8(tmp_path)

    monkeypatch.setattr("signals.push.push_config", lambda: _apns_cfg(key_path))
    monkeypatch.setattr("signals.push._send_live_activity", AsyncMock(return_value=True))

    proposal_id = "paper_end_bind"
    await start_proposal_live_activity(_sample_proposal_row(id=proposal_id), db_path=db)

    resp = _register_via_api(
        mutation_client,
        activity_update_token="upd_end",
        device_token="la_dev",
    )
    assert resp.json()["ok"] is True

    captured: list[tuple[str, dict]] = []

    async def _capture(token, payload_aps, *, db_path):
        captured.append((token, payload_aps))
        return True

    monkeypatch.setattr("signals.push._send_live_activity", _capture)

    count = await end_proposal_live_activity(
        proposal_id,
        status="executed",
        proposal=_sample_proposal_row(id=proposal_id),
        db_path=db,
    )
    assert count == 1
    assert captured[0][0] == "upd_end"
    assert captured[0][1]["event"] == "end"


def test_bind_update_token_latest_picks_most_recent_start(tmp_path: Path) -> None:
    db = tmp_path / "la.db"
    init_live_activity_tables(db)
    record_activity_start("p1", "dev1", db_path=db)
    record_activity_start("p2", "dev1", db_path=db)

    bound = bind_update_token_latest("dev1", "upd_latest", db_path=db)
    assert bound == "p2"
    assert list_update_tokens("p2", db_path=db) == [
        {"device_token": "dev1", "update_token": "upd_latest"}
    ]
    assert list_update_tokens("p1", db_path=db) == []
