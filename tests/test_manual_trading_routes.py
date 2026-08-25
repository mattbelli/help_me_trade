"""Manual trading routes — admin-token gate + response mapping."""

from __future__ import annotations

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from api import manual_trading_routes as routes


def _request(headers: dict[str, str]) -> Request:
    raw = [(k.lower().encode(), v.encode()) for k, v in headers.items()]
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/manual/orders",
            "headers": raw,
            "query_string": b"",
            "client": ("127.0.0.1", 1),
        }
    )


@pytest.fixture
def admin(monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setattr("api.auth.ADMIN_TOKEN", "adm")
    monkeypatch.setattr("api.auth.API_TOKEN", "shared")
    return "adm"


def test_shared_token_gets_403_on_money_verbs(admin: str) -> None:
    with pytest.raises(HTTPException) as exc:
        routes._require_admin(_request({"X-API-Token": "shared"}))
    assert exc.value.status_code == 403


def test_admin_token_passes(admin: str) -> None:
    routes._require_admin(_request({"X-API-Token": "adm"}))


def test_unset_admin_token_disables_verbs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("api.auth.ADMIN_TOKEN", "")
    with pytest.raises(HTTPException) as exc:
        routes._require_admin(_request({"X-API-Token": "anything"}))
    assert exc.value.status_code == 403


def test_has_admin_echoes_own_token_only(admin: str) -> None:
    assert routes._has_admin(_request({"X-API-Token": "adm"})) is True
    assert routes._has_admin(_request({"X-API-Token": "shared"})) is False


def test_respond_maps_only_input_errors() -> None:
    with pytest.raises(HTTPException) as exc:
        routes._respond({"status": "invalid", "reason": "qty must be a number"})
    assert exc.value.status_code == 400
    with pytest.raises(HTTPException) as exc:
        routes._respond({"status": "not_found", "reason": "unknown execution id"})
    assert exc.value.status_code == 404
    # A RiskEngine block is a 200 outcome, not a transport failure.
    out = routes._respond({"status": "blocked", "reason": "daily order cap"})
    assert out["status"] == "blocked"
    out = routes._respond({"status": "failed", "reason": "x", "cancelled_orders": ["a"]})
    assert out["cancelled_orders"] == ["a"]
