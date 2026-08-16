"""Open-boundary staleness on the watchlist daily-bars cache.

Pins the 07-22 live bug: a 1Day series cached pre-open (last bar = yesterday)
kept serving through the TTL after 09:30, so every %chg column showed
YESTERDAY's move as current at the open (SPY +0.83 displayed vs −0.14 real).
During regular session a series missing today's developing bar is stale by
SHAPE, ttl notwithstanding — but only rechecked after _WL_STALE_RECHECK_S so a
halted/no-bar symbol can't retry-storm."""

from __future__ import annotations

import time
from datetime import datetime, timedelta

import pytest

import api.watchlist_routes as wl_routes
from api import watchlist_bars_cache
from trading.market_hours import _ET


class _Bar:
    def __init__(self, ts: str) -> None:
        self.time = ts
        self.close = 100.0
        self.open = self.high = self.low = 100.0
        self.volume = 1.0


def _bar_days_ago(days: int) -> _Bar:
    d = datetime.now(_ET).date() - timedelta(days=days)
    return _Bar(f"{d.isoformat()}T04:00:00+00:00")


class _Provider:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    async def aget_stock_bars_batch(self, symbols, *, timeframe, days):
        self.calls.append(list(symbols))
        return {s: [_bar_days_ago(1), _bar_days_ago(0)] for s in symbols}


@pytest.fixture(autouse=True)
def _clean_cache():
    wl_routes._wl_bars_cache.clear()
    yield
    wl_routes._wl_bars_cache.clear()


@pytest.mark.asyncio
async def test_stale_daily_series_refetched_at_the_open(monkeypatch) -> None:
    monkeypatch.setattr(watchlist_bars_cache, "market_session", lambda: "regular")
    provider = _Provider()
    # Cached 2 minutes ago (inside the 300s TTL), but the series ends YESTERDAY.
    wl_routes._wl_bars_cache[("SPY", "1Day")] = (
        time.time() - 120.0,
        [_bar_days_ago(2), _bar_days_ago(1)],
    )
    out = await wl_routes._wl_cached_bars_batch(provider, ["SPY"], timeframe="1Day")
    assert provider.calls == [["SPY"]]  # forced refetch despite valid TTL
    assert str(out["SPY"][-1].time)[:10] == _bar_days_ago(0).time[:10]


@pytest.mark.asyncio
async def test_fresh_entry_not_rechecked_before_min_age(monkeypatch) -> None:
    """The retry-storm bound: a stale-by-shape entry younger than
    _WL_STALE_RECHECK_S serves from cache — at most one forced refetch per
    symbol per minute."""
    monkeypatch.setattr(watchlist_bars_cache, "market_session", lambda: "regular")
    provider = _Provider()
    wl_routes._wl_bars_cache[("SPY", "1Day")] = (
        time.time() - 10.0,
        [_bar_days_ago(2), _bar_days_ago(1)],
    )
    out = await wl_routes._wl_cached_bars_batch(provider, ["SPY"], timeframe="1Day")
    assert provider.calls == []
    assert str(out["SPY"][-1].time)[:10] == _bar_days_ago(1).time[:10]


@pytest.mark.asyncio
async def test_yesterday_tail_is_fine_outside_regular_session(monkeypatch) -> None:
    """Pre-open/closed: yesterday-ending series is the CORRECT current state —
    no forced refetch."""
    monkeypatch.setattr(watchlist_bars_cache, "market_session", lambda: "closed")
    provider = _Provider()
    wl_routes._wl_bars_cache[("SPY", "1Day")] = (
        time.time() - 120.0,
        [_bar_days_ago(2), _bar_days_ago(1)],
    )
    await wl_routes._wl_cached_bars_batch(provider, ["SPY"], timeframe="1Day")
    assert provider.calls == []
