"""Alpaca data-tier auto-detection tests."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from brokers.alpaca import AlpacaAdapter, AlpacaError
from brokers.alpaca_tier import (
    PROBE_TTL_SEC,
    clear_alpaca_tier_cache,
    data_tier,
    effective_alpaca_feed,
)


@pytest.fixture(autouse=True)
def _reset_tier_cache() -> None:
    clear_alpaca_tier_cache()
    yield
    clear_alpaca_tier_cache()


def _adapter(*, data_feed: str = "auto") -> AlpacaAdapter:
    return AlpacaAdapter(
        base_url="https://paper-api.alpaca.markets",
        key_id="test-key",
        secret="test-secret",
        data_feed=data_feed,
    )


def test_explicit_sip_no_probe(monkeypatch: pytest.MonkeyPatch) -> None:
    probe = MagicMock()
    monkeypatch.setattr("brokers.alpaca_tier._probe_sip_entitlement", probe)

    adapter = _adapter(data_feed="sip")

    assert adapter.configured_data_feed == "sip"
    assert data_tier(adapter) == "paid"
    assert adapter._data_feed == "sip"
    probe.assert_not_called()


def test_explicit_iex_no_probe(monkeypatch: pytest.MonkeyPatch) -> None:
    probe = MagicMock()
    monkeypatch.setattr("brokers.alpaca_tier._probe_sip_entitlement", probe)

    adapter = _adapter(data_feed="iex")

    assert adapter.configured_data_feed == "iex"
    assert data_tier(adapter) == "free"
    assert adapter._data_feed == "iex"
    probe.assert_not_called()


def test_auto_entitled_probe_returns_paid(monkeypatch: pytest.MonkeyPatch) -> None:
    probe = MagicMock(return_value="paid")
    monkeypatch.setattr("brokers.alpaca_tier._probe_sip_entitlement", probe)

    adapter = _adapter(data_feed="auto")

    assert data_tier(adapter) == "paid"
    assert adapter._data_feed == "sip"
    probe.assert_called_once()


def test_auto_not_entitled_probe_returns_free(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "brokers.alpaca_tier._probe_sip_entitlement",
        lambda _get_data: "free",
    )

    adapter = _adapter(data_feed="auto")

    assert data_tier(adapter) == "free"
    assert adapter._data_feed == "iex"


def test_auto_subscription_denied_via_probe(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[object] = []

    def fake_get_data(path: str, *, params: dict[str, str] | None = None) -> dict:
        calls.append((path, params))
        raise AlpacaError("GET /v2/stocks/SPY/snapshot failed (403): subscription required")

    adapter = _adapter(data_feed="auto")
    monkeypatch.setattr(adapter, "_get_data", fake_get_data)

    assert data_tier(adapter) == "free"
    assert adapter._data_feed == "iex"
    assert len(calls) == 1


def test_auto_entitled_via_probe(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[object] = []

    def fake_get_data(path: str, *, params: dict[str, str] | None = None) -> dict:
        calls.append((path, params))
        return {"symbol": "SPY"}

    adapter = _adapter(data_feed="auto")
    monkeypatch.setattr(adapter, "_get_data", fake_get_data)

    assert data_tier(adapter) == "paid"
    assert adapter._data_feed == "sip"
    assert len(calls) == 1


def test_auto_ambiguous_error_falls_back_safely(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_get_data(_path: str, *, params: dict[str, str] | None = None) -> dict:
        raise AlpacaError("Network error: timeout")

    adapter = _adapter(data_feed="auto")
    monkeypatch.setattr(adapter, "_get_data", fake_get_data)

    assert data_tier(adapter) == "free"
    assert adapter._data_feed == "iex"


def test_auto_ambiguous_error_keeps_last_known_tier(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    states = iter(["paid", None])

    def fake_probe(_get_data: object) -> str | None:
        return next(states)

    monkeypatch.setattr("brokers.alpaca_tier._probe_sip_entitlement", fake_probe)
    monkeypatch.setattr("brokers.alpaca_tier.time.monotonic", lambda: 0.0)

    adapter = _adapter(data_feed="auto")

    assert data_tier(adapter) == "paid"
    assert adapter._data_feed == "sip"

    monkeypatch.setattr(
        "brokers.alpaca_tier.time.monotonic",
        lambda: float(PROBE_TTL_SEC + 1),
    )

    assert data_tier(adapter) == "paid"
    assert adapter._data_feed == "sip"


def test_cache_ttl_avoids_repeated_probes(monkeypatch: pytest.MonkeyPatch) -> None:
    probe = MagicMock(return_value="paid")
    monkeypatch.setattr("brokers.alpaca_tier._probe_sip_entitlement", probe)
    monotonic = iter([0.0, 1.0, float(PROBE_TTL_SEC + 1), float(PROBE_TTL_SEC + 2)])
    monkeypatch.setattr("brokers.alpaca_tier.time.monotonic", lambda: next(monotonic))

    adapter = _adapter(data_feed="auto")

    assert data_tier(adapter) == "paid"
    assert data_tier(adapter) == "paid"
    probe.assert_called_once()

    assert data_tier(adapter) == "paid"
    assert probe.call_count == 2


def test_effective_alpaca_feed_explicit_paths() -> None:
    assert effective_alpaca_feed(configured_feed="sip") == "sip"
    assert effective_alpaca_feed(configured_feed="iex") == "iex"
