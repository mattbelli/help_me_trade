"""API authentication and Host-header validation."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def auth_token(monkeypatch: pytest.MonkeyPatch) -> str:
    token = "test-secret-token"
    monkeypatch.setattr("api.auth.API_TOKEN", token)
    return token


@pytest.fixture
def authed_client(auth_token: str) -> TestClient:
    from api.main import app

    with TestClient(app, base_url="http://127.0.0.1") as client:
        yield client


@pytest.fixture(autouse=True)
def _disable_auth_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep existing tests open unless a test opts into auth fixtures."""
    monkeypatch.setattr("api.auth.API_TOKEN", "")


def test_health_exempt_without_token(authed_client: TestClient) -> None:
    response = authed_client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_api_rejects_missing_token(authed_client: TestClient) -> None:
    response = authed_client.get("/api/settings")
    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid or missing API token"


def test_api_rejects_wrong_token(authed_client: TestClient, auth_token: str) -> None:
    response = authed_client.get(
        "/api/settings",
        headers={"X-API-Token": "wrong-token"},
    )
    assert response.status_code == 401


def test_api_accepts_valid_token(authed_client: TestClient, auth_token: str) -> None:
    response = authed_client.get(
        "/api/settings",
        headers={"X-API-Token": auth_token},
    )
    assert response.status_code == 200


def test_api_accepts_bearer_token(authed_client: TestClient, auth_token: str) -> None:
    response = authed_client.get(
        "/api/settings",
        headers={"Authorization": f"Bearer {auth_token}"},
    )
    assert response.status_code == 200


def test_api_rejects_invalid_host(
    authed_client: TestClient, auth_token: str
) -> None:
    response = authed_client.get(
        "/api/settings",
        headers={"X-API-Token": auth_token, "Host": "evil.example"},
    )
    assert response.status_code == 403
    assert response.json()["detail"] == "Invalid Host header"
