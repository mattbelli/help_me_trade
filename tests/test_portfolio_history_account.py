"""Account-scoped GET /api/portfolio/history (Strategy Lab equity curves).

Covers the optional `account_id` extension: it routes to that account's Alpaca
adapter, leaves the default (no-param) behavior untouched, and rejects
non-Alpaca / unknown accounts.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from brokers.alpaca import AlpacaAdapter
from brokers.errors import BrokerError


@pytest.fixture
def client():
    from api.main import app

    with TestClient(
        app, base_url="http://127.0.0.1", raise_server_exceptions=True
    ) as test_client:
        yield test_client


def _point(ts: int, equity: float, pl: float, pl_pct: float) -> SimpleNamespace:
    return SimpleNamespace(
        timestamp=ts, equity=equity, profit_loss=pl, profit_loss_pct=pl_pct
    )


def _fail_default() -> None:
    raise AssertionError("default (_require_alpaca) path used unexpectedly")


def test_history_account_scoped_routes_to_that_account(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    adapter = MagicMock(spec=AlpacaAdapter)
    adapter.aget_portfolio_history = AsyncMock(
        return_value=[
            _point(1000, 100_000.0, 0.0, 0.0),
            _point(2000, 100_500.0, 500.0, 0.5),
        ]
    )
    captured: dict[str, str] = {}

    def _resolve(account_id: str) -> MagicMock:
        captured["account_id"] = account_id
        return adapter

    monkeypatch.setattr("api.main.get_broker_for_account", _resolve)
    monkeypatch.setattr("api.main._require_alpaca", _fail_default)

    resp = client.get(
        "/api/portfolio/history?range=1M&account_id=acct_alpaca_fable"
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["range"] == "1M"
    assert captured["account_id"] == "acct_alpaca_fable"
    adapter.aget_portfolio_history.assert_awaited_once_with("1M")
    assert body["points"][1] == {
        "timestamp": 2000,
        "equity": 100_500.0,
        "profit_loss": 500.0,
        "profit_loss_pct": 0.5,
    }


def test_history_default_path_unchanged(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    adapter = MagicMock(spec=AlpacaAdapter)
    adapter.aget_portfolio_history = AsyncMock(
        return_value=[_point(1000, 100_000.0, 0.0, 0.0)]
    )
    monkeypatch.setattr("api.main._require_alpaca", lambda: adapter)

    def _boom(account_id: str) -> MagicMock:
        raise AssertionError("account path used for a default request")

    monkeypatch.setattr("api.main.get_broker_for_account", _boom)

    resp = client.get("/api/portfolio/history?range=1W")
    assert resp.status_code == 200
    body = resp.json()
    assert body["range"] == "1W"
    adapter.aget_portfolio_history.assert_awaited_once_with("1W")


def test_history_non_alpaca_account_rejected(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    non_alpaca = MagicMock()  # no AlpacaAdapter spec → isinstance() is False
    monkeypatch.setattr(
        "api.main.get_broker_for_account", lambda account_id: non_alpaca
    )
    resp = client.get(
        "/api/portfolio/history?range=1M&account_id=acct_schwab"
    )
    assert resp.status_code == 400


def test_history_unknown_account_502(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _raise(account_id: str) -> MagicMock:
        raise BrokerError("unknown account 'nope'")

    monkeypatch.setattr("api.main.get_broker_for_account", _raise)
    resp = client.get("/api/portfolio/history?range=1M&account_id=nope")
    assert resp.status_code == 502
