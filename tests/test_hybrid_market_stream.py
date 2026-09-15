"""HybridMarketStreamHub — shared _quotes/_clients + per-class subscription routing.

Hermetic: children's baseline seeding is stubbed to no-ops so no Alpaca/Schwab
network is touched; we assert only the composition + routing wiring.
"""

from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta, timezone

import pytest

from brokers.hybrid_market_stream import (
    MAX_WS_SYMBOLS_ALPACA,
    HybridMarketStreamHub,
)
from brokers.market_stream import QuoteState
from trading.market_hours import _ET


async def _noop_baselines(self, symbols, force=False):
    return True


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
    monkeypatch.setattr(
        "brokers.market_stream.AlpacaCryptoStreamHub._ensure_baselines",
        _noop_baselines,
    )
    return HybridMarketStreamHub()


def test_children_share_quotes_and_clients(hub: HybridMarketStreamHub) -> None:
    assert hub._alpaca._quotes is hub._quotes
    assert hub._alpaca._clients is hub._clients
    assert hub._schwab._quotes is hub._quotes
    assert hub._schwab._clients is hub._clients
    assert hub._crypto._quotes is hub._quotes
    assert hub._crypto._clients is hub._clients
    assert hub._crypto.bars is hub.bars
    assert hub._crypto._last_print_ms is hub._last_print_ms
    assert hub._crypto.tape_detector is hub.tape_detector

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
    assert hub._crypto._subscribed == set()


@pytest.mark.asyncio
async def test_crypto_streams_on_its_own_child_options_still_dropped(
    hub: HybridMarketStreamHub,
) -> None:
    await hub.ensure_symbols({"BTC/USD", "ETH/USD", "AAPL240119C00190000", "AAPL"})
    # Crypto now has a child of its own; options remain a documented stream skip.
    assert hub._alpaca._subscribed == {"AAPL"}
    assert hub._crypto._subscribed == {"BTC/USD", "ETH/USD"}
    assert hub._crypto._pending_add == {"BTC/USD", "ETH/USD"}
    assert hub._schwab._subscribed == set()


@pytest.mark.asyncio
async def test_crypto_unsubscribe_flows_to_the_crypto_child(
    hub: HybridMarketStreamHub,
) -> None:
    await hub.ensure_symbols({"BTC/USD", "AAPL"})
    await hub.ensure_symbols({"AAPL"})
    assert "BTC/USD" in hub._crypto._pending_remove
    assert hub._crypto._subscribed == set()


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


async def _sleep_forever(self):
    await asyncio.sleep(3600)


@pytest.mark.asyncio
async def test_composite_runs_the_midnight_roll_loop(
    hub: HybridMarketStreamHub, monkeypatch
) -> None:
    # Children never start(), so the composite must own the roll loop — or
    # prev_close is never re-seeded and Tuesday's % reads against Friday.
    for cls in (
        "brokers.market_stream.AlpacaMarketStreamHub",
        "brokers.schwab_market_stream.SchwabMarketStreamHub",
        "brokers.market_stream.AlpacaCryptoStreamHub",
    ):
        monkeypatch.setattr(f"{cls}._upstream_loop", _sleep_forever)
    await hub.start()
    try:
        assert hub._baseline_roll_task is not None
        assert not hub._baseline_roll_task.done()
    finally:
        await hub.stop()
    assert hub._baseline_roll_task is None


@pytest.mark.asyncio
async def test_roll_reseeds_each_child_slice_on_its_own_clock(
    hub: HybridMarketStreamHub, monkeypatch
) -> None:
    calls: list[tuple[str, set[str], bool]] = []

    def _rec(name: str, ok: bool = True):
        async def _f(self, symbols, force=False):
            calls.append((name, set(symbols), force))
            return ok

        return _f

    monkeypatch.setattr(type(hub._alpaca), "_ensure_baselines", _rec("alpaca"))
    monkeypatch.setattr(type(hub._schwab), "_ensure_baselines", _rec("schwab"))
    monkeypatch.setattr(type(hub._crypto), "_ensure_baselines", _rec("crypto"))
    for sym in ("AAPL", "/ESU25", "BTC/USD"):
        hub._quotes[sym] = QuoteState(price=1.0)

    yesterday = date.today() - timedelta(days=1)
    hub._baseline_date = yesterday
    hub._crypto._baseline_date = yesterday
    await hub._roll_baselines_if_new_day()

    assert ("alpaca", {"AAPL"}, True) in calls
    assert ("schwab", {"/ESU25"}, True) in calls
    assert ("crypto", {"BTC/USD"}, True) in calls
    assert hub._baseline_date == datetime.now(_ET).date()
    assert hub._crypto._baseline_date == datetime.now(timezone.utc).date()

    # A second tick the same day is a no-op.
    calls.clear()
    await hub._roll_baselines_if_new_day()
    assert calls == []


@pytest.mark.asyncio
async def test_roll_does_not_stamp_the_day_when_a_fetch_fails(
    hub: HybridMarketStreamHub, monkeypatch
) -> None:
    async def _fail(self, symbols, force=False):
        return False

    monkeypatch.setattr(type(hub._alpaca), "_ensure_baselines", _fail)
    hub._quotes["AAPL"] = QuoteState(price=1.0)
    yesterday = date.today() - timedelta(days=1)
    hub._baseline_date = yesterday
    await hub._roll_baselines_if_new_day()
    assert hub._baseline_date == yesterday  # retries next tick
