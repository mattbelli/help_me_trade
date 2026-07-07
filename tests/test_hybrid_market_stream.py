"""HybridMarketStreamHub — shared _quotes/_clients + per-class subscription routing.

Hermetic: children's baseline seeding is stubbed to no-ops so no Alpaca/Schwab
network is touched; we assert only the composition + routing wiring.
"""

from __future__ import annotations

import pytest

from brokers.hybrid_market_stream import (
    MAX_WS_SYMBOLS_ALPACA,
    HybridMarketStreamHub,
)
from brokers.market_stream import QuoteState


async def _noop_baselines(self, symbols):
    return None


@pytest.fixture
def hub(monkeypatch) -> HybridMarketStreamHub:
    # Stub baseline seeding on both child classes (they'd otherwise attempt a
    # broker bar fetch on first subscribe).
    monkeypatch.setattr(
        "brokers.market_stream.AlpacaMarketStreamHub._ensure_baselines",
        _noop_baselines,
    )
    monkeypatch.setattr(
        "brokers.schwab_market_stream.SchwabMarketStreamHub._ensure_baselines",
        _noop_baselines,
    )
    return HybridMarketStreamHub()


def test_children_share_quotes_and_clients(hub: HybridMarketStreamHub) -> None:
    assert hub._alpaca._quotes is hub._quotes
    assert hub._alpaca._clients is hub._clients
    assert hub._schwab._quotes is hub._quotes
    assert hub._schwab._clients is hub._clients

    # A child tick lands in the composite's single quote table.
    hub._alpaca._quotes["AAPL"] = QuoteState(price=200.0)
    assert hub._quotes["AAPL"].price == 200.0


@pytest.mark.asyncio
async def test_subscribe_partitions_per_class(hub: HybridMarketStreamHub) -> None:
    await hub.ensure_symbols({"AAPL", "SPY", "/ESU25"})

    # Equities → Alpaca child only; futures → Schwab child only.
    assert hub._alpaca._subscribed == {"AAPL", "SPY"}
    assert hub._alpaca._pending_add == {"AAPL", "SPY"}
    assert hub._schwab._subscribed == {"/ESU25"}
    assert hub._schwab._pending_add == {"/ESU25"}


@pytest.mark.asyncio
async def test_crypto_and_options_not_streamed(hub: HybridMarketStreamHub) -> None:
    await hub.ensure_symbols({"BTC/USD", "AAPL240119C00190000", "AAPL"})
    # Only the equity is subscribed; crypto/option are documented stream skips.
    assert hub._alpaca._subscribed == {"AAPL"}
    assert hub._schwab._subscribed == set()


@pytest.mark.asyncio
async def test_unsubscribe_flows_to_the_right_child(hub: HybridMarketStreamHub) -> None:
    await hub.ensure_symbols({"AAPL", "/ESU25"})
    await hub.ensure_symbols({"/ESU25"})  # drop the equity
    assert "AAPL" in hub._alpaca._pending_remove
    assert hub._alpaca._subscribed == set()
    assert hub._schwab._subscribed == {"/ESU25"}


def test_alpaca_child_cap_is_raised() -> None:
    # SIP allows unlimited ws symbols; the equity child cap is generous but finite.
    from brokers.market_stream import MAX_WS_SYMBOLS

    assert MAX_WS_SYMBOLS_ALPACA > MAX_WS_SYMBOLS
