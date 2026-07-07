"""HybridMarketData — per-symbol REST routing, batch partition+merge, deny-by-default.

Hermetic: children are hand-rolled fakes; no Alpaca/Schwab/network.
"""

from __future__ import annotations

import pytest

from brokers.errors import BrokerError
from brokers.factory import _create_market_data_provider
from brokers.hybrid_market_data import HybridMarketData


class _FakeChild:
    def __init__(self, name: str) -> None:
        self.name = name
        self.batch_calls: list[list[str]] = []
        self.single_calls: list[str] = []
        self.quote_calls: list[list[str]] = []

    async def aget_stock_bars(self, symbol, *, timeframe="1Day", days=180, extended_hours=False):
        self.single_calls.append(symbol)
        return [f"{self.name}:{symbol}"]

    async def aget_stock_bars_batch(self, symbols, *, timeframe="1Day", days=12):
        self.batch_calls.append(list(symbols))
        return {s: [f"{self.name}:{s}"] for s in symbols}

    async def aget_quotes(self, symbols):
        self.quote_calls.append(list(symbols))
        return {s: {"src": self.name} for s in symbols}


def _hybrid() -> tuple[HybridMarketData, _FakeChild, _FakeChild]:
    alpaca = _FakeChild("alpaca")
    schwab = _FakeChild("schwab")
    return HybridMarketData(alpaca=alpaca, schwab=schwab), alpaca, schwab


@pytest.mark.asyncio
async def test_batch_partitions_and_merges() -> None:
    hybrid, alpaca, schwab = _hybrid()
    out = await hybrid.aget_stock_bars_batch(["AAPL", "SPY", "/ESU25", "/NQZ25"])

    # Each child saw ONLY its own slice.
    assert alpaca.batch_calls == [["AAPL", "SPY"]]
    assert schwab.batch_calls == [["/ESU25", "/NQZ25"]]
    # Merged result covers all four with the right provenance.
    assert set(out) == {"AAPL", "SPY", "/ESU25", "/NQZ25"}
    assert out["AAPL"] == ["alpaca:AAPL"]
    assert out["/ESU25"] == ["schwab:/ESU25"]


@pytest.mark.asyncio
async def test_batch_all_equities_never_touches_schwab() -> None:
    hybrid, alpaca, schwab = _hybrid()
    await hybrid.aget_stock_bars_batch(["AAPL", "SPY", "QQQ"])
    assert alpaca.batch_calls == [["AAPL", "SPY", "QQQ"]]
    assert schwab.batch_calls == []


@pytest.mark.asyncio
async def test_single_bar_routes_per_symbol() -> None:
    hybrid, alpaca, schwab = _hybrid()
    assert await hybrid.aget_stock_bars("SPY") == ["alpaca:SPY"]
    assert await hybrid.aget_stock_bars("/ESU25") == ["schwab:/ESU25"]
    assert alpaca.single_calls == ["SPY"]
    assert schwab.single_calls == ["/ESU25"]


@pytest.mark.asyncio
async def test_quotes_route_equities_to_schwab_drop_futures() -> None:
    hybrid, alpaca, schwab = _hybrid()
    out = await hybrid.aget_quotes(["AAPL", "/ESU25", "SPY"])
    # ext/fundamentals live only on Schwab; futures carry none and are dropped.
    assert schwab.quote_calls == [["AAPL", "SPY"]]
    assert alpaca.quote_calls == []
    assert set(out) == {"AAPL", "SPY"}


@pytest.mark.asyncio
async def test_quotes_only_futures_short_circuits() -> None:
    hybrid, alpaca, schwab = _hybrid()
    assert await hybrid.aget_quotes(["/ESU25", "/NQZ25"]) == {}
    assert schwab.quote_calls == []


class _RaisingChild(_FakeChild):
    async def aget_stock_bars_batch(self, symbols, *, timeframe="1Day", days=12):
        raise BrokerError("Schwab WAF cooldown active")


@pytest.mark.asyncio
async def test_batch_faults_isolate_partial_serve() -> None:
    # A Schwab (futures) failure must NOT discard the already-fetched equity bars
    # or propagate — the whole watchlist would 502 + retry-storm otherwise.
    alpaca = _FakeChild("alpaca")
    schwab = _RaisingChild("schwab")
    hybrid = HybridMarketData(alpaca=alpaca, schwab=schwab)
    out = await hybrid.aget_stock_bars_batch(["AAPL", "SPY", "/ESU25"])
    assert set(out) == {"AAPL", "SPY"}  # equities served
    assert "/ESU25" not in out  # futures leg failed → skipped, not fatal
    assert out["AAPL"] == ["alpaca:AAPL"]


@pytest.mark.asyncio
async def test_batch_skips_crypto_option_never_hits_stock_endpoint() -> None:
    # crypto/option have no REST bar endpoint yet — they must be surfaced as a gap,
    # NOT silently forwarded to the Alpaca stock endpoint (which returns empty).
    hybrid, alpaca, schwab = _hybrid()
    out = await hybrid.aget_stock_bars_batch(
        ["AAPL", "BTC/USD", "AAPL240119C00190000", "/ESU25"]
    )
    assert alpaca.batch_calls == [["AAPL"]]  # only the equity, never the crypto/option
    assert schwab.batch_calls == [["/ESU25"]]
    assert set(out) == {"AAPL", "/ESU25"}


@pytest.mark.asyncio
async def test_single_crypto_returns_empty_without_touching_children() -> None:
    hybrid, alpaca, schwab = _hybrid()
    assert await hybrid.aget_stock_bars("BTC/USD") == []
    assert alpaca.single_calls == []
    assert schwab.single_calls == []


def test_deny_by_default_when_alpaca_unconfigured(monkeypatch) -> None:
    monkeypatch.setattr(
        "brokers.factory.broker_is_configured", lambda name: False
    )
    with pytest.raises(BrokerError, match="Hybrid market data requires Alpaca"):
        _create_market_data_provider("hybrid")


def test_factory_builds_hybrid_when_alpaca_configured(monkeypatch) -> None:
    monkeypatch.setattr(
        "brokers.factory.broker_is_configured", lambda name: name == "alpaca"
    )
    provider = _create_market_data_provider("hybrid")
    assert isinstance(provider, HybridMarketData)
