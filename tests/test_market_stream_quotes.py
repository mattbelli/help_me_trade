"""Quote-watch lane (`quotes` WS action → upstream `q` frames → NBBO payload).

Hermetic: no network. Pins the DOM ladder's data contract: sizes are SHARES
(never ×100), the quotes channel is subscribed per-watch (never for the whole
pinned set), and disconnects release the upstream subscription.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from brokers.hybrid_market_stream import HybridMarketStreamHub
from brokers.market_stream import (
    AlpacaMarketStreamHub,
    QuoteState,
    _quote_payload,
)


async def _noop_async(*args, **kwargs):
    return None


@pytest.fixture
def hub(monkeypatch) -> AlpacaMarketStreamHub:
    h = AlpacaMarketStreamHub()
    monkeypatch.setattr(h, "_broadcast_quote", AsyncMock())
    return h


def _quote_item(symbol: str, **over) -> dict:
    item = {
        "T": "q",
        "S": symbol,
        "bp": 412.51,
        "bs": 300,
        "ap": 412.53,
        "as": 200,
        "t": "2026-08-25T14:30:00.123456789Z",
    }
    item.update(over)
    return item


# ---------------------------------------------------------------------------
# `q` frame handling
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_on_quote_updates_nbbo_and_sizes(hub: AlpacaMarketStreamHub) -> None:
    hub._quotes["TSLA"] = QuoteState(price=412.5, prev_close=410.0)
    await hub._on_quote(_quote_item("TSLA"))
    state = hub._quotes["TSLA"]
    assert state.bid == 412.51
    assert state.ask == 412.53
    # Sizes are shares as-published — no round-lot multiplication.
    assert state.bid_size == 300
    assert state.ask_size == 200
    assert state.quote_ts is not None
    hub._broadcast_quote.assert_awaited_once_with("TSLA")
    # Trade-derived state is untouched by a quote frame.
    assert state.price == 412.5
    assert state.prev_close == 410.0


@pytest.mark.asyncio
async def test_on_quote_one_sided_carries_zero(hub: AlpacaMarketStreamHub) -> None:
    hub._quotes["FXE"] = QuoteState(price=100.0)
    await hub._on_quote(_quote_item("FXE", bp=None, bs=None))
    state = hub._quotes["FXE"]
    assert state.bid == 0.0
    assert state.bid_size == 0.0
    assert state.ask == 412.53


@pytest.mark.asyncio
async def test_on_quote_never_seeds_unknown_symbol(hub: AlpacaMarketStreamHub) -> None:
    # A bare QuoteState (price=0) would be skipped by the baseline seeder
    # forever and its zeros broadcast to every client — the `quotes` action
    # seeds via _ensure_baselines instead.
    await hub._on_quote(_quote_item("AMD"))
    assert "AMD" not in hub._quotes
    hub._broadcast_quote.assert_not_awaited()


@pytest.mark.asyncio
async def test_handle_alpaca_message_dispatches_q(hub: AlpacaMarketStreamHub) -> None:
    hub._quotes["TSLA"] = QuoteState(price=1.05)
    await hub._handle_alpaca_message(
        '[{"T":"q","S":"TSLA","bp":1.0,"bs":5,"ap":1.1,"as":7,'
        '"t":"2026-08-25T14:30:00Z"}]'
    )
    assert hub._quotes["TSLA"].bid == 1.0


def test_quote_payload_carries_sizes() -> None:
    state = QuoteState(price=10.0, bid=9.99, ask=10.01, bid_size=100, ask_size=50, quote_ts=123)
    payload = _quote_payload(state)
    assert payload["bid_size"] == 100
    assert payload["ask_size"] == 50
    assert payload["quote_ts"] == 123


# ---------------------------------------------------------------------------
# Quote-watch subscription lifecycle
# ---------------------------------------------------------------------------


class _FakeWS:
    """Stands in for a WebSocket dict key."""


@pytest.mark.asyncio
async def test_sync_quote_upstream_diffs_pending_sets(hub: AlpacaMarketStreamHub) -> None:
    ws = _FakeWS()
    hub._quote_watch[ws] = {"TSLA"}
    await hub._sync_quote_upstream()
    assert hub._quotes_subscribed == {"TSLA"}
    assert hub._pending_quote_add == {"TSLA"}
    assert hub._pending_quote_remove == set()

    hub._pending_quote_add.clear()  # upstream loop drained it
    hub._quote_watch[ws] = {"AMD"}
    await hub._sync_quote_upstream()
    assert hub._quotes_subscribed == {"AMD"}
    assert hub._pending_quote_add == {"AMD"}
    assert hub._pending_quote_remove == {"TSLA"}


@pytest.mark.asyncio
async def test_client_departure_releases_quote_watch(hub: AlpacaMarketStreamHub) -> None:
    ws_a, ws_b = _FakeWS(), _FakeWS()
    hub._quote_watch[ws_a] = {"TSLA"}
    hub._quote_watch[ws_b] = {"TSLA", "AMD"}
    await hub._sync_quote_upstream()
    hub._pending_quote_add.clear()

    # Second watcher of TSLA leaves: TSLA survives, AMD is released.
    hub._quote_watch.pop(ws_b)
    await hub._sync_quote_upstream()
    assert hub._quotes_subscribed == {"TSLA"}
    assert hub._pending_quote_remove == {"AMD"}


def test_quote_desired_union_cap_truncates() -> None:
    from brokers.market_stream import MAX_QUOTE_SYMBOLS

    hub = AlpacaMarketStreamHub()
    # Many clients can together exceed the union cap even with the per-client
    # cap enforced at handle_client; the union cap is the proxy-queue guard.
    for i in range(MAX_QUOTE_SYMBOLS + 4):
        hub._quote_watch[_FakeWS()] = {f"SY{i:02d}"}
    assert len(hub._quote_desired()) == MAX_QUOTE_SYMBOLS


@pytest.mark.asyncio
async def test_rapid_watch_flicker_cannot_leak_an_upstream_sub(
    hub: AlpacaMarketStreamHub,
) -> None:
    """TSLA→AMD→TSLA switches faster than one drain window must not leave a
    stale pending add for a symbol nobody watches (measured leak in review:
    the drain sends unsubscribe first, so a stale add re-subscribes forever)."""
    ws = _FakeWS()
    hub._quote_watch[ws] = {"TSLA"}
    await hub._sync_quote_upstream()
    hub._quote_watch[ws] = {"AMD"}
    await hub._sync_quote_upstream()
    # No drain has run: pending sets must describe exactly the desired state.
    assert hub._quotes_subscribed == {"AMD"}
    assert hub._pending_quote_add == {"AMD"}
    assert hub._pending_quote_remove == {"TSLA"}
    hub._quote_watch[ws] = {"TSLA"}
    await hub._sync_quote_upstream()
    assert hub._pending_quote_add == {"TSLA"}
    assert hub._pending_quote_remove == {"AMD"}
    # The original hazard: TSLA must not sit in BOTH sets.
    assert not (hub._pending_quote_add & hub._pending_quote_remove)


# ---------------------------------------------------------------------------
# Hybrid partition: only the Alpaca child gets a quotes upstream
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_hybrid_routes_equity_quote_watch_to_alpaca_child() -> None:
    hub = HybridMarketStreamHub()
    ws = _FakeWS()
    hub._quote_watch[ws] = {"TSLA", "/ES", "BTC/USD"}
    await hub._sync_quote_upstream()
    assert hub._alpaca._quotes_subscribed == {"TSLA"}
    assert hub._alpaca._pending_quote_add == {"TSLA"}
    # Futures/crypto quote-watch never reaches the equity child.
    assert "/ES" not in hub._alpaca._quotes_subscribed
    # The composite's own sets stay untouched (children own the upstream).
    assert hub._quotes_subscribed == set()


@pytest.mark.asyncio
async def test_hybrid_release_on_watch_change() -> None:
    hub = HybridMarketStreamHub()
    ws = _FakeWS()
    hub._quote_watch[ws] = {"TSLA"}
    await hub._sync_quote_upstream()
    hub._alpaca._pending_quote_add.clear()
    hub._quote_watch[ws] = set()
    await hub._sync_quote_upstream()
    assert hub._alpaca._quotes_subscribed == set()
    assert hub._alpaca._pending_quote_remove == {"TSLA"}
