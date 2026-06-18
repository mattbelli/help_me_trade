"""Alpaca overnight-tradable asset parsing tests."""

from __future__ import annotations

import time

import pytest

from brokers.alpaca import AlpacaAdapter


@pytest.fixture(autouse=True)
def _clear_asset_caches() -> None:
    AlpacaAdapter._assets_cache = None
    AlpacaAdapter._overnight_cache = None
    yield
    AlpacaAdapter._assets_cache = None
    AlpacaAdapter._overnight_cache = None


def _adapter() -> AlpacaAdapter:
    return AlpacaAdapter(
        base_url="https://paper-api.alpaca.markets",
        key_id="test-key",
        secret="test-secret",
    )


def _fake_assets_payload() -> list[dict]:
    return [
        {
            "symbol": "AAPL",
            "tradable": True,
            "attributes": ["overnight_tradable"],
        },
        {
            "symbol": "MSFT",
            "tradable": True,
            "attributes": ["overnight_tradable", "overnight_halted"],
        },
        {
            "symbol": "GOOG",
            "tradable": True,
            "attributes": [],
        },
        {
            "symbol": "TSLA",
            "tradable": False,
            "attributes": ["overnight_tradable"],
        },
        {
            "symbol": "NVDA",
            "tradable": True,
            "attributes": ["overnight_tradable", "has_options"],
        },
    ]


def test_overnight_cache_from_assets_parse() -> None:
    adapter = _adapter()
    now = time.time()

    tradable = adapter._cache_tradable_symbols(now, _fake_assets_payload())

    assert tradable == ["AAPL", "GOOG", "MSFT", "NVDA"]
    assert adapter.overnight_tradable_symbols() == {"AAPL", "NVDA", "TSLA"}


@pytest.mark.asyncio
async def test_aovernight_tradable_symbols_uses_cache() -> None:
    adapter = _adapter()
    now = time.time()
    adapter._cache_tradable_symbols(now, _fake_assets_payload())

    result = await adapter.aovernight_tradable_symbols()

    assert result == {"AAPL", "NVDA", "TSLA"}
    assert "MSFT" not in result
