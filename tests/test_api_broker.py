"""Tests for GET /api/broker Alpaca data_feed exposure."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from brokers.alpaca_tier import clear_alpaca_tier_cache


@pytest.fixture(autouse=True)
def _reset_tier_cache() -> None:
    clear_alpaca_tier_cache()
    yield
    clear_alpaca_tier_cache()


def _mock_alpaca_block(
    monkeypatch: pytest.MonkeyPatch,
    *,
    configured: str,
    tier: str,
    feed: str,
) -> MagicMock:
    adapter = MagicMock()
    adapter._configured_data_feed = configured
    adapter._get_data = MagicMock()

    monkeypatch.setattr("api.main.broker_is_configured", lambda name: name == "alpaca")
    monkeypatch.setattr(
        "api.main.alpaca_config",
        lambda: {"data_feed": configured},
    )
    monkeypatch.setattr("api.main.get_market_data_provider", lambda _name: adapter)
    monkeypatch.setattr("api.main.data_tier", lambda _adapter: tier)
    monkeypatch.setattr(
        "api.main.effective_alpaca_feed",
        lambda *, configured_feed, get_data: feed,
    )
    return adapter


def test_broker_includes_paid_sip_feed(monkeypatch: pytest.MonkeyPatch) -> None:
    from api.main import app

    _mock_alpaca_block(monkeypatch, configured="sip", tier="paid", feed="sip")

    with TestClient(app, base_url="http://127.0.0.1") as client:
        resp = client.get("/api/broker")

    assert resp.status_code == 200
    assert resp.json()["brokers"]["alpaca"]["data_feed"] == {
        "configured": "sip",
        "tier": "paid",
        "feed": "sip",
        "overnight_feed": "boats",
    }


def test_broker_includes_free_iex_feed(monkeypatch: pytest.MonkeyPatch) -> None:
    from api.main import app

    _mock_alpaca_block(monkeypatch, configured="iex", tier="free", feed="iex")

    with TestClient(app, base_url="http://127.0.0.1") as client:
        resp = client.get("/api/broker")

    assert resp.status_code == 200
    assert resp.json()["brokers"]["alpaca"]["data_feed"] == {
        "configured": "iex",
        "tier": "free",
        "feed": "iex",
        "overnight_feed": "overnight",
    }


def test_broker_omits_data_feed_when_alpaca_unconfigured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from api.main import app

    monkeypatch.setattr("api.main.broker_is_configured", lambda _name: False)

    with TestClient(app, base_url="http://127.0.0.1") as client:
        resp = client.get("/api/broker")

    assert resp.status_code == 200
    assert "data_feed" not in resp.json()["brokers"]["alpaca"]


def test_recheck_feed_clears_tier_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    from api.main import app

    adapter = _mock_alpaca_block(
        monkeypatch, configured="auto", tier="paid", feed="sip"
    )
    cleared = MagicMock()
    monkeypatch.setattr("api.main.clear_alpaca_tier_cache", cleared)

    with TestClient(app, base_url="http://127.0.0.1") as client:
        resp = client.get("/api/broker", params={"recheck_feed": "1"})

    assert resp.status_code == 200
    cleared.assert_called_once()
    assert adapter._get_data is not None


def test_broker_survives_adapter_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    from api.main import app

    monkeypatch.setattr("api.main.broker_is_configured", lambda name: name == "alpaca")

    def boom(_name: str) -> object:
        raise RuntimeError("adapter unavailable")

    monkeypatch.setattr("api.main.get_market_data_provider", boom)
    monkeypatch.setattr("api.main.get_broker", boom)

    with TestClient(app, base_url="http://127.0.0.1") as client:
        resp = client.get("/api/broker")

    assert resp.status_code == 200
    assert "data_feed" not in resp.json()["brokers"]["alpaca"]
