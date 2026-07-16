"""Alpaca overnight (boats) bar merge and ext-quote feed tests."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from brokers.alpaca import AlpacaAdapter, StockBar


def _adapter(*, data_feed: str = "sip") -> AlpacaAdapter:
    return AlpacaAdapter(
        base_url="https://paper-api.alpaca.markets",
        key_id="test-key",
        secret="test-secret",
        data_feed=data_feed,
    )


def test_merge_bars_by_time_unions_without_duplicates() -> None:
    sip = [
        StockBar(time="2026-07-08T13:00:00Z", open=1, high=2, low=0.5, close=1.5, volume=10),
        StockBar(time="2026-07-08T20:00:00Z", open=2, high=3, low=1.5, close=2.5, volume=20),
    ]
    boats = [
        StockBar(time="2026-07-09T00:00:00Z", open=2.5, high=3, low=2, close=2.8, volume=5),
        StockBar(time="2026-07-09T07:50:00Z", open=2.8, high=3.1, low=2.7, close=3.0, volume=6),
    ]
    merged = AlpacaAdapter._merge_bars_by_time(sip, boats)
    assert [b.time for b in merged] == [
        "2026-07-08T13:00:00Z",
        "2026-07-08T20:00:00Z",
        "2026-07-09T00:00:00Z",
        "2026-07-09T07:50:00Z",
    ]


def test_merge_bars_prefers_first_series_on_duplicate_timestamp() -> None:
    sip = [
        StockBar(time="2026-07-09T08:00:00Z", open=1, high=2, low=0.5, close=1.5, volume=10),
    ]
    boats = [
        StockBar(time="2026-07-09T08:00:00Z", open=9, high=9, low=9, close=9, volume=99),
    ]
    merged = AlpacaAdapter._merge_bars_by_time(sip, boats)
    assert len(merged) == 1
    assert merged[0].close == 1.5


@pytest.mark.asyncio
async def test_aget_ext_quotes_uses_feed_override() -> None:
    adapter = _adapter(data_feed="sip")
    adapter._aget_data = AsyncMock(  # type: ignore[method-assign]
        return_value={
            "AAPL": {
                "latestTrade": {"p": 200.0},
                "prevDailyBar": {"c": 195.0, "t": "2026-07-07T04:00:00Z"},
            }
        }
    )

    out = await adapter.aget_ext_quotes(["AAPL"], feed="boats")

    assert out["AAPL"]["ext_last"] == 200.0
    adapter._aget_data.assert_awaited_once()
    assert adapter._aget_data.await_args.kwargs["params"]["feed"] == "boats"


@pytest.mark.asyncio
async def test_intraday_ext_bars_merge_sip_and_boats() -> None:
    adapter = _adapter(data_feed="sip")
    start = datetime(2026, 7, 7, tzinfo=UTC)
    end = datetime(2026, 7, 9, tzinfo=UTC)

    async def _fake_fetch(symbol, *, timeframe, start, end, feed):
        assert symbol == "SPY"
        assert timeframe == "5Min"
        if feed == "sip":
            return [
                StockBar(
                    time="2026-07-08T14:00:00Z",
                    open=500,
                    high=501,
                    low=499,
                    close=500.5,
                    volume=100,
                ),
                StockBar(
                    time="2026-07-08T20:00:00Z",
                    open=500.5,
                    high=501,
                    low=500,
                    close=500.8,
                    volume=50,
                ),
            ]
        if feed == "boats":
            return [
                StockBar(
                    time="2026-07-09T00:00:00Z",
                    open=500.8,
                    high=501.2,
                    low=500.5,
                    close=501.0,
                    volume=30,
                ),
            ]
        raise AssertionError(f"unexpected feed {feed}")

    adapter._aget_bars_with_feed = _fake_fetch  # type: ignore[method-assign]

    bars = await adapter.aget_stock_bars_range(
        "SPY",
        timeframe="5Min",
        start=start,
        end=end,
        extended_hours=True,
    )

    assert [b.time for b in bars] == [
        "2026-07-08T14:00:00Z",
        "2026-07-08T20:00:00Z",
        "2026-07-09T00:00:00Z",
    ]


@pytest.mark.asyncio
async def test_intraday_ext_bars_serve_sip_only_when_boats_fails() -> None:
    adapter = _adapter(data_feed="sip")
    start = datetime(2026, 7, 7, tzinfo=UTC)
    end = datetime(2026, 7, 9, tzinfo=UTC)
    sip_bar = StockBar(
        time="2026-07-08T14:00:00Z",
        open=1,
        high=2,
        low=0.5,
        close=1.5,
        volume=10,
    )

    async def _fake_fetch(symbol, *, timeframe, start, end, feed):
        if feed == "sip":
            return [sip_bar]
        return []

    adapter._aget_bars_with_feed = _fake_fetch  # type: ignore[method-assign]

    bars = await adapter.aget_stock_bars_range(
        "AAPL",
        timeframe="5Min",
        start=start,
        end=end,
        extended_hours=True,
    )

    assert bars == [sip_bar]


@pytest.mark.asyncio
async def test_daily_bars_skip_boats_leg_even_with_extended_hours() -> None:
    adapter = _adapter(data_feed="sip")
    calls: list[str] = []

    async def _fake_fetch(symbol, *, timeframe, start, end, feed):
        calls.append(feed)
        return [
            StockBar(
                time="2026-07-08T04:00:00Z",
                open=1,
                high=2,
                low=0.5,
                close=1.5,
                volume=10,
            )
        ]

    adapter._aget_bars_with_feed = _fake_fetch  # type: ignore[method-assign]

    await adapter.aget_stock_bars(
        "AAPL",
        timeframe="1Day",
        days=30,
        extended_hours=True,
    )

    assert calls == ["sip"]
