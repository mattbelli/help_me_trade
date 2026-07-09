"""Hermetic tests for GET /api/options-chain."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from brokers.alpaca import AlpacaAdapter


@pytest.fixture(autouse=True)
def _clear_chain_cache() -> None:
    from api.main import _OPTIONS_CHAIN_CACHE

    _OPTIONS_CHAIN_CACHE.clear()
    yield
    _OPTIONS_CHAIN_CACHE.clear()


def _chain_snapshots() -> dict:
    return {
        "NVDA260717C00200000": {
            "latestQuote": {"bp": 2.1, "ap": 2.2},
            "latestTrade": {"p": 2.15},
            "impliedVolatility": 0.45,
            "greeks": {"delta": 0.55, "gamma": 0.02, "theta": -0.1, "vega": 0.08},
            "dailyBar": {"v": 500},
        },
        "NVDA260717P00200000": {
            "latestQuote": {"bp": 1.8, "ap": 1.9},
            "impliedVolatility": 0.42,
            "greeks": {"delta": -0.45},
        },
        "NVDA260821C00190000": {
            "latestQuote": {"bp": 4.2, "ap": 4.35},
            "latestTrade": {"p": 4.28},
            "impliedVolatility": 0.499,
            "greeks": {
                "delta": 0.44,
                "gamma": 0.031,
                "theta": -0.352,
                "vega": 0.118,
            },
            "dailyBar": {"v": 1234},
        },
        "NVDA260821P00190000": {
            "latestQuote": {"bp": 3.1, "ap": 3.2},
            "impliedVolatility": 0.48,
            "greeks": {"delta": -0.4},
        },
    }


def _mock_alpaca(
    monkeypatch: pytest.MonkeyPatch,
    *,
    chain: dict | None = None,
    contracts: list[dict] | None = None,
    underlying_price: float | None = 201.84,
    chain_raises: bool = False,
) -> MagicMock:
    broker = MagicMock(spec=AlpacaAdapter)

    if chain_raises:

        def _boom_chain(_sym: str, feed: str = "opra") -> dict:
            raise RuntimeError("chain boom")

        broker.get_option_chain_snapshot = _boom_chain
    else:
        broker.get_option_chain_snapshot = MagicMock(
            return_value={} if chain is None else chain
        )

    broker.get_stock_snapshot_fields = MagicMock(
        return_value={"last_price": underlying_price}
        if underlying_price is not None
        else None
    )
    broker.get_option_contracts = MagicMock(
        return_value=[] if contracts is None else contracts
    )

    monkeypatch.setattr("api.main.broker_is_configured", lambda name: name == "alpaca")
    monkeypatch.setattr("api.main.get_broker", lambda _name: broker)
    return broker


def test_options_chain_happy_path_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    from api.main import app

    contracts = [
        {
            "symbol": "NVDA260821C00190000",
            "expiration_date": "2026-08-21",
            "open_interest": "5678",
        },
        {
            "symbol": "NVDA260821P00190000",
            "expiration_date": "2026-08-21",
            "open_interest": 2100,
        },
    ]
    _mock_alpaca(monkeypatch, chain=_chain_snapshots(), contracts=contracts)

    with TestClient(app, base_url="http://127.0.0.1") as client:
        resp = client.get("/api/options-chain?symbol=NVDA&expiry=2026-08-21")

    assert resp.status_code == 200
    data = resp.json()
    assert data["symbol"] == "NVDA"
    assert data["underlying_price"] == 201.84
    assert data["expiry"] == "2026-08-21"
    assert "2026-07-17" in data["expirations"]
    assert "2026-08-21" in data["expirations"]
    assert data["expirations"] == sorted(data["expirations"])

    assert len(data["rows"]) == 1
    row = data["rows"][0]
    assert row["strike"] == 190.0
    assert row["call"]["bid"] == 4.2
    assert row["call"]["ask"] == 4.35
    assert row["call"]["iv"] == 0.499
    assert row["call"]["delta"] == 0.44
    assert row["call"]["gamma"] == 0.031
    assert row["call"]["theta"] == -0.352
    assert row["call"]["vega"] == 0.118
    assert row["call"]["volume"] == 1234
    assert row["call"]["oi"] == 5678
    assert row["call"]["last"] == 4.28
    assert row["put"]["bid"] == 3.1
    assert row["put"]["ask"] == 3.2
    assert row["put"]["oi"] == 2100


def test_options_chain_default_nearest_expiry(monkeypatch: pytest.MonkeyPatch) -> None:
    from datetime import datetime as real_datetime

    from api.main import app
    from trading.market_hours import _ET

    class FrozenDatetime(real_datetime):
        @classmethod
        def now(cls, tz=None):
            return real_datetime(2026, 7, 9, 15, 30, 0, tzinfo=tz or _ET)

    monkeypatch.setattr("api.main.datetime", FrozenDatetime)
    _mock_alpaca(monkeypatch, chain=_chain_snapshots())

    with TestClient(app, base_url="http://127.0.0.1") as client:
        resp = client.get("/api/options-chain?symbol=NVDA")

    assert resp.status_code == 200
    data = resp.json()
    assert data["expiry"] == "2026-07-17"
    strikes = [r["strike"] for r in data["rows"]]
    assert strikes == [200.0]


def test_options_chain_missing_fields_are_null(monkeypatch: pytest.MonkeyPatch) -> None:
    from api.main import app

    sparse_chain = {
        "NVDA260717C00200000": {
            "latestQuote": {"bp": 1.5},
        },
    }
    _mock_alpaca(monkeypatch, chain=sparse_chain)

    with TestClient(app, base_url="http://127.0.0.1") as client:
        resp = client.get("/api/options-chain?symbol=NVDA&expiry=2026-07-17")

    assert resp.status_code == 200
    row = resp.json()["rows"][0]
    assert row["call"]["bid"] == 1.5
    assert row["call"]["ask"] is None
    assert row["call"]["iv"] is None
    assert row["call"]["delta"] is None
    assert row["call"]["volume"] is None
    assert row["call"]["oi"] is None
    assert row["call"]["last"] is None
    assert row["put"]["bid"] is None


def test_options_chain_empty_chain_fail_soft(monkeypatch: pytest.MonkeyPatch) -> None:
    from api.main import app

    _mock_alpaca(monkeypatch, chain={})

    with TestClient(app, base_url="http://127.0.0.1") as client:
        resp = client.get("/api/options-chain?symbol=NVDA")

    assert resp.status_code == 200
    data = resp.json()
    assert data["symbol"] == "NVDA"
    assert data["expirations"] == []
    assert data["expiry"] is None
    assert data["rows"] == []


def test_options_chain_broker_error_returns_500(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from api.main import app

    _mock_alpaca(monkeypatch, chain_raises=True)

    with TestClient(app, base_url="http://127.0.0.1") as client:
        resp = client.get("/api/options-chain?symbol=NVDA")

    assert resp.status_code == 500
    assert "error" in resp.json()


def test_options_chain_requires_symbol(monkeypatch: pytest.MonkeyPatch) -> None:
    from api.main import app

    _mock_alpaca(monkeypatch, chain=_chain_snapshots())

    with TestClient(app, base_url="http://127.0.0.1") as client:
        resp = client.get("/api/options-chain")

    assert resp.status_code == 400


def test_options_chain_alpaca_unconfigured(monkeypatch: pytest.MonkeyPatch) -> None:
    from api.main import app

    monkeypatch.setattr("api.main.broker_is_configured", lambda _name: False)

    with TestClient(app, base_url="http://127.0.0.1") as client:
        resp = client.get("/api/options-chain?symbol=NVDA")

    assert resp.status_code == 501
