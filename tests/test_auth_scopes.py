"""Auth scope split: fail-closed mutations, admin trading scope, kill switch."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def auth_token(monkeypatch: pytest.MonkeyPatch) -> str:
    token = "test-secret-token"
    monkeypatch.setattr("api.auth.API_TOKEN", token)
    return token


@pytest.fixture
def admin_token(monkeypatch: pytest.MonkeyPatch) -> str:
    token = "test-admin-token"
    monkeypatch.setattr("api.auth.ADMIN_TOKEN", token)
    return token


@pytest.fixture
def authed_client(auth_token: str) -> TestClient:
    from api.main import app

    with TestClient(app, base_url="http://127.0.0.1") as client:
        yield client


@pytest.fixture
def scoped_client(auth_token: str, admin_token: str) -> TestClient:
    from api.main import app

    with TestClient(app, base_url="http://127.0.0.1") as client:
        yield client


@pytest.fixture
def isolated_settings(tmp_path, monkeypatch: pytest.MonkeyPatch):
    from config import settings as settings_mod

    settings_path = tmp_path / "settings.json"
    monkeypatch.setattr(settings_mod, "SETTINGS_PATH", settings_path)
    settings_mod.clear_settings_cache()
    return settings_mod


def _headers(token: str) -> dict[str, str]:
    return {"X-API-Token": token}


# --- 1. Fail-closed mutations when API_TOKEN unset ---


def test_get_open_when_api_token_unset() -> None:
    from api.main import app

    with TestClient(app, base_url="http://127.0.0.1") as client:
        response = client.get("/api/settings")
    assert response.status_code == 200


def test_mutations_rejected_when_api_token_unset() -> None:
    from api.main import app

    with TestClient(app, base_url="http://127.0.0.1") as client:
        for method, path, kwargs in (
            ("put", "/api/settings", {"json": {"ai": {"provider": "mock"}}}),
            ("post", "/api/kill-switch", {"json": {"active": True}}),
            ("delete", "/api/research/1", {}),
        ):
            response = getattr(client, method)(path, **kwargs)
            assert response.status_code == 401, method
            assert response.json()["detail"] == "mutations disabled: API_TOKEN not configured"


# --- 2. Normal vs admin on trading settings ---


def test_non_trading_settings_put_with_normal_token(
    scoped_client: TestClient, auth_token: str
) -> None:
    response = scoped_client.put(
        "/api/settings",
        headers=_headers(auth_token),
        json={"ai": {"provider": "mock"}},
    )
    assert response.status_code == 200


def test_trading_settings_put_requires_admin(
    scoped_client: TestClient, auth_token: str
) -> None:
    response = scoped_client.put(
        "/api/settings",
        headers=_headers(auth_token),
        json={"trading": {"max_daily_orders": 5}},
    )
    assert response.status_code == 403
    assert "admin token required" in response.json()["detail"]


def test_trading_settings_put_with_admin_token(
    scoped_client: TestClient,
    admin_token: str,
    isolated_settings,
) -> None:
    response = scoped_client.put(
        "/api/settings",
        headers=_headers(admin_token),
        json={"trading": {"max_daily_orders": 7}},
    )
    assert response.status_code == 200
    assert response.json()["trading"]["max_daily_orders"] == 7


# --- 3. Admin unset ---


def test_trading_put_admin_unset(
    authed_client: TestClient, auth_token: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("api.auth.ADMIN_TOKEN", "")
    response = authed_client.put(
        "/api/settings",
        headers=_headers(auth_token),
        json={"trading": {"max_order_value_usd": 1000}},
    )
    assert response.status_code == 403
    assert response.json()["detail"] == (
        "admin token not configured — risk-control changes disabled"
    )


def test_kill_switch_disarm_admin_unset(
    authed_client: TestClient, auth_token: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("api.auth.ADMIN_TOKEN", "")
    response = authed_client.post(
        "/api/kill-switch",
        headers=_headers(auth_token),
        json={"active": False},
    )
    assert response.status_code == 403
    assert "admin token not configured" in response.json()["detail"]


def test_kill_switch_arm_without_admin_configured(
    authed_client: TestClient,
    auth_token: str,
    admin_token: str,
    monkeypatch: pytest.MonkeyPatch,
    isolated_settings,
) -> None:
    monkeypatch.setattr("api.auth.ADMIN_TOKEN", "")
    response = authed_client.post(
        "/api/kill-switch",
        headers=_headers(auth_token),
        json={"active": True},
    )
    assert response.status_code == 200
    assert response.json()["kill_switch_active"] is True


# --- 4. Kill switch endpoint ---


@pytest.fixture
def mock_notify(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    mock = AsyncMock(return_value={"id": "n1"})
    monkeypatch.setattr("signals.notify.notify", mock)
    return mock


def test_kill_switch_arm_normal_token(
    scoped_client: TestClient,
    auth_token: str,
    isolated_settings,
    mock_notify: AsyncMock,
) -> None:
    response = scoped_client.post(
        "/api/kill-switch",
        headers=_headers(auth_token),
        json={"active": True},
    )
    assert response.status_code == 200
    assert response.json()["kill_switch_active"] is True
    mock_notify.assert_awaited_once()
    assert mock_notify.await_args.kwargs["severity"] == "critical"
    assert mock_notify.await_args.kwargs["push"] is True


def test_kill_switch_disarm_normal_token_rejected(
    scoped_client: TestClient,
    auth_token: str,
    isolated_settings,
) -> None:
    scoped_client.post(
        "/api/kill-switch",
        headers=_headers(auth_token),
        json={"active": True},
    )
    response = scoped_client.post(
        "/api/kill-switch",
        headers=_headers(auth_token),
        json={"active": False},
    )
    assert response.status_code == 403


def test_kill_switch_disarm_admin_token(
    scoped_client: TestClient,
    auth_token: str,
    admin_token: str,
    isolated_settings,
    mock_notify: AsyncMock,
) -> None:
    scoped_client.post(
        "/api/kill-switch",
        headers=_headers(auth_token),
        json={"active": True},
    )
    mock_notify.reset_mock()
    response = scoped_client.post(
        "/api/kill-switch",
        headers=_headers(admin_token),
        json={"active": False},
    )
    assert response.status_code == 200
    assert response.json()["kill_switch_active"] is False
    mock_notify.assert_awaited_once()


def test_settings_put_rejects_kill_switch_key(
    scoped_client: TestClient, admin_token: str
) -> None:
    response = scoped_client.put(
        "/api/settings",
        headers=_headers(admin_token),
        json={"trading": {"kill_switch_active": False}},
    )
    assert response.status_code == 400
    assert response.json()["error"] == "use /api/kill-switch"


# --- 4b. Entries-halt (HALT) endpoint — symmetric normal-token auth ---


def test_entries_halt_arm_disarm_normal_token(
    scoped_client: TestClient,
    auth_token: str,
    isolated_settings,
    mock_notify: AsyncMock,
) -> None:
    # HALT is less dangerous than the kill switch — a normal token both arms AND
    # disarms it (no admin gate on disarm), the human resume path.
    arm = scoped_client.post(
        "/api/entries-halt",
        headers=_headers(auth_token),
        json={"active": True},
    )
    assert arm.status_code == 200
    assert arm.json()["entries_halted"] is True

    disarm = scoped_client.post(
        "/api/entries-halt",
        headers=_headers(auth_token),
        json={"active": False},
    )
    assert disarm.status_code == 200
    assert disarm.json()["entries_halted"] is False


def test_entries_halt_rejects_non_bool(
    scoped_client: TestClient, auth_token: str, isolated_settings
) -> None:
    response = scoped_client.post(
        "/api/entries-halt",
        headers=_headers(auth_token),
        json={"active": "yes"},
    )
    assert response.status_code == 400


def test_settings_put_rejects_entries_halted_key(
    scoped_client: TestClient, admin_token: str
) -> None:
    response = scoped_client.put(
        "/api/settings",
        headers=_headers(admin_token),
        json={"trading": {"entries_halted": False}},
    )
    assert response.status_code == 400
    assert response.json()["error"] == "use /api/entries-halt"


def test_health_reports_both_safety_states(
    authed_client: TestClient, isolated_settings
) -> None:
    resp = authed_client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    # Both states surfaced independently.
    assert body["kill_switch_active"] is False
    assert body["entries_halted"] is False


# --- 5. Admin ⊃ normal ---


def test_admin_token_passes_normal_auth(
    scoped_client: TestClient, admin_token: str
) -> None:
    response = scoped_client.get("/api/settings", headers=_headers(admin_token))
    assert response.status_code == 200


def test_normal_token_never_satisfies_admin(
    scoped_client: TestClient, auth_token: str
) -> None:
    response = scoped_client.put(
        "/api/settings",
        headers=_headers(auth_token),
        json={"trading": {"execution_mode": "dry_run"}},
    )
    assert response.status_code == 403


# --- 6. Empty-token edge cases ---


def test_empty_presented_token_does_not_authenticate(
    authed_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("api.auth.ADMIN_TOKEN", "")
    response = authed_client.get("/api/settings", headers={"X-API-Token": ""})
    assert response.status_code == 401


def test_empty_admin_env_with_empty_header_on_trading_put(
    authed_client: TestClient, auth_token: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("api.auth.ADMIN_TOKEN", "")
    response = authed_client.put(
        "/api/settings",
        headers={"X-API-Token": ""},
        json={"trading": {"max_daily_orders": 1}},
    )
    assert response.status_code == 401


# --- 7. Proposal approve/reject still use normal token ---


def test_proposal_approve_reject_normal_token(
    scoped_client: TestClient, auth_token: str
) -> None:
    approve = scoped_client.post(
        "/api/proposals/missing-id/approve",
        headers=_headers(auth_token),
        json={"payload_hash": "abc"},
    )
    assert approve.status_code != 401

    reject = scoped_client.post(
        "/api/proposals/missing-id/reject",
        headers=_headers(auth_token),
    )
    assert reject.status_code != 401


# --- 8. WebSocket auth unchanged (API_TOKEN only) ---


@pytest.mark.asyncio
async def test_websocket_auth_unchanged(
    auth_token: str, admin_token: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from unittest.mock import AsyncMock, MagicMock

    from api.auth import verify_websocket_auth

    monkeypatch.setattr("api.auth.API_TOKEN", auth_token)
    monkeypatch.setattr("api.auth.ADMIN_TOKEN", admin_token)

    ws_admin = MagicMock()
    ws_admin.headers = {"host": "127.0.0.1", "x-api-token": admin_token}
    ws_admin.query_params = {}
    ws_admin.close = AsyncMock()
    assert await verify_websocket_auth(ws_admin) is False
    ws_admin.close.assert_awaited_once()

    ws_normal = MagicMock()
    ws_normal.headers = {"host": "127.0.0.1", "x-api-token": auth_token}
    ws_normal.query_params = {}
    ws_normal.close = AsyncMock()
    assert await verify_websocket_auth(ws_normal) is True
    ws_normal.close.assert_not_awaited()
