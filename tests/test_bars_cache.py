"""Chart bars store (card #326): newest-window fetch, tail refresh, hourly
re-baseline, on-demand older pages, request coalescing, stale-if-error.

Still pins the 07-22 lessons that survive the redesign: concurrent identical
requests share ONE upstream fetch, and futures never hop to Alpaca (which has
no futures data) — they serve the last-good series through a Schwab WAF cooldown.
"""

from __future__ import annotations

import asyncio
import time as _time
from datetime import UTC, datetime

import pytest

import api.bars_routes as bars_routes


class _TBar:
    def __init__(self, time: str, close: float = 100.0) -> None:
        self.time = time
        self.open = self.high = self.low = self.close = close
        self.volume = 1.0


def _day(d: int, close: float = 100.0) -> _TBar:
    return _TBar(f"2026-07-{d:02d}T04:00:00Z", close)


class _Provider:
    """``newest`` is what aget_stock_bars_latest returns (with ``has_more``);
    ``tail`` is what the range refetch returns. Every call is recorded."""

    def __init__(
        self,
        newest=None,
        *,
        has_more: bool = True,
        tail=None,
        fail: bool = False,
        delay: float = 0.0,
    ) -> None:
        self.newest = list(newest or [_day(21)])
        self.has_more = has_more
        self.tail = tail
        self.fail = fail
        self.delay = delay
        self.latest_calls: list[dict] = []
        self.range_calls: list[dict] = []

    async def aget_stock_bars_latest(
        self, sym, *, timeframe, limit, before=None, extended_hours=False
    ):
        self.latest_calls.append(
            {"sym": sym, "timeframe": timeframe, "limit": limit, "before": before, "ext": extended_hours}
        )
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.fail:
            raise RuntimeError("provider down")
        return list(self.newest[-limit:]), self.has_more

    async def aget_stock_bars_range(self, sym, *, timeframe, start, end, extended_hours=False):
        self.range_calls.append({"sym": sym, "timeframe": timeframe, "start": start})
        if self.tail is None:
            raise RuntimeError("no range data")
        return list(self.tail)


@pytest.fixture(autouse=True)
def _clean_cache(monkeypatch):
    bars_routes._BARS_CACHE.clear()
    bars_routes._BARS_INFLIGHT.clear()
    bars_routes._BARS_FULL_AT.clear()
    monkeypatch.setattr(bars_routes, "_overnight_delayed_flag", lambda: False)
    yield
    bars_routes._BARS_CACHE.clear()
    bars_routes._BARS_INFLIGHT.clear()
    bars_routes._BARS_FULL_AT.clear()


def _expire(key: tuple) -> None:
    _, payload = bars_routes._BARS_CACHE[key]
    bars_routes._BARS_CACHE[key] = (0.0, payload)


# --- newest window ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_cold_load_fetches_newest_limit_and_stores(monkeypatch) -> None:
    provider = _Provider([_day(d) for d in range(1, 11)], has_more=True)
    monkeypatch.setattr(bars_routes, "get_market_data_provider", lambda: provider)

    out = await bars_routes._get_bars_cached("SPY", "1Day", limit=3)
    assert provider.latest_calls == [
        {"sym": "SPY", "timeframe": "1Day", "limit": 3, "before": None, "ext": False}
    ]
    assert [b["time"] for b in out["bars"]] == [_day(d).time for d in (8, 9, 10)]
    assert out["has_more"] is True
    key = ("SPY", "1Day", False)
    assert key in bars_routes._BARS_CACHE and key in bars_routes._BARS_FULL_AT

    # Warm + fresh: served from the store, no upstream call.
    again = await bars_routes._get_bars_cached("SPY", "1Day", limit=3)
    assert again is out and len(provider.latest_calls) == 1


@pytest.mark.asyncio
async def test_fresh_store_is_a_hit_regardless_of_size(monkeypatch) -> None:
    """A short window (the vendor's page walk stopped early) is still a hit while
    fresh — the chart backfills through `before`, never by re-asking for more."""
    provider = _Provider([_day(1), _day(2)], has_more=True)
    monkeypatch.setattr(bars_routes, "get_market_data_provider", lambda: provider)
    first = await bars_routes._get_bars_cached("TINY", "1Day", limit=5)
    assert await bars_routes._get_bars_cached("TINY", "1Day", limit=50) is first
    assert len(provider.latest_calls) == 1


def test_window_returns_newest_limit_without_mutating_store() -> None:
    response = {"symbol": "SPY", "bars": [{"time": str(i)} for i in range(5)], "has_more": False}
    out = bars_routes._window(response, 2)
    assert [b["time"] for b in out["bars"]] == ["3", "4"] and out["has_more"] is True
    assert len(response["bars"]) == 5 and response["has_more"] is False
    assert bars_routes._window(response, 5) is response


# --- tail refresh + re-baseline ---------------------------------------------------


@pytest.mark.asyncio
async def test_stale_store_refreshes_tail_and_merges(monkeypatch) -> None:
    """An expired store over a fresh baseline refetches from its LAST bar to now
    (one small page) and merges: the boundary bar is replaced, new bars appended."""
    provider = _Provider(
        [_day(20, 10.0), _day(21, 11.0), _day(22, 12.0)],
        tail=[_day(22, 12.5), _day(23, 13.0)],
    )
    monkeypatch.setattr(bars_routes, "get_market_data_provider", lambda: provider)
    key = ("SPY", "1Day", False)
    first = await bars_routes._get_bars_cached("SPY", "1Day", limit=3)
    baseline = bars_routes._BARS_FULL_AT[key]
    _expire(key)

    second = await bars_routes._get_bars_cached("SPY", "1Day", limit=3)
    assert len(provider.latest_calls) == 1  # no second full walk
    assert provider.range_calls[0]["start"] == datetime(2026, 7, 22, 4, tzinfo=UTC)
    assert [(b["time"], b["close"]) for b in second["bars"]] == [
        (_day(20).time, 10.0),
        (_day(21).time, 11.0),
        (_day(22).time, 12.5),  # replaced, not duplicated
        (_day(23).time, 13.0),  # appended
    ]
    assert second is not first
    assert bars_routes._BARS_FULL_AT[key] == baseline  # a tail refresh is not a baseline
    assert bars_routes._BARS_CACHE[key][0] > _time.monotonic()  # fresh again


@pytest.mark.asyncio
async def test_old_baseline_forces_full_refetch(monkeypatch) -> None:
    provider = _Provider([_day(21)], tail=[_day(21)])
    monkeypatch.setattr(bars_routes, "get_market_data_provider", lambda: provider)
    key = ("SPY", "1Day", False)
    await bars_routes._get_bars_cached("SPY", "1Day", limit=1)
    _expire(key)
    bars_routes._BARS_FULL_AT[key] = _time.monotonic() - (bars_routes._BARS_FULL_REBASELINE + 1)
    await bars_routes._get_bars_cached("SPY", "1Day", limit=1)
    assert len(provider.latest_calls) == 2 and provider.range_calls == []


@pytest.mark.asyncio
async def test_tail_refresh_failure_falls_back_to_full(monkeypatch) -> None:
    provider = _Provider([_day(21)], tail=None)  # range raises
    monkeypatch.setattr(bars_routes, "get_market_data_provider", lambda: provider)
    key = ("SPY", "1Day", False)
    await bars_routes._get_bars_cached("SPY", "1Day", limit=1)
    _expire(key)
    out = await bars_routes._get_bars_cached("SPY", "1Day", limit=1)
    assert out is not None and len(provider.latest_calls) == 2


@pytest.mark.asyncio
async def test_futures_aggregated_timeframes_never_merge_a_tail(monkeypatch) -> None:
    """Schwab builds 1Hour/4Hour/1Week/1Month by aggregating in-process; bucket
    stamps depend on the window, so futures always refetch the whole window."""
    provider = _Provider([_day(21)], tail=[_day(21)])
    monkeypatch.setattr(bars_routes, "get_market_data_provider", lambda: provider)
    for tf, want_full in (("1Week", 2), ("1Min", 1)):
        provider.latest_calls.clear()
        key = ("/ES", tf, bars_routes._bars_extended_default(tf))
        await bars_routes._get_bars_cached("/ES", tf, limit=1)
        _expire(key)
        await bars_routes._get_bars_cached("/ES", tf, limit=1)
        assert len(provider.latest_calls) == want_full, tf


# --- older pages ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_older_page_is_fetched_before_and_not_stored(monkeypatch) -> None:
    provider = _Provider([_day(d) for d in range(1, 6)], has_more=False)
    monkeypatch.setattr(bars_routes, "get_market_data_provider", lambda: provider)
    before_ms = int(datetime(2026, 7, 6, 4, tzinfo=UTC).timestamp() * 1000)
    page = await bars_routes._get_older_bars("SPY", "1Day", False, before_ms, 5)
    assert provider.latest_calls[0]["before"] == datetime(2026, 7, 6, 4, tzinfo=UTC)
    assert len(page["bars"]) == 5 and page["has_more"] is False
    assert bars_routes._BARS_CACHE == {}  # history is the browser's to keep


@pytest.mark.asyncio
async def test_older_page_failure_is_none(monkeypatch) -> None:
    provider = _Provider(fail=True)
    monkeypatch.setattr(bars_routes, "get_market_data_provider", lambda: provider)
    assert await bars_routes._get_older_bars("SPY", "1Day", False, 0, 5) is None


# --- coalescing + resilience --------------------------------------------------------


@pytest.mark.asyncio
async def test_concurrent_identical_requests_share_one_fetch(monkeypatch) -> None:
    provider = _Provider(delay=0.05)
    monkeypatch.setattr(bars_routes, "get_market_data_provider", lambda: provider)
    a, b = await asyncio.gather(
        bars_routes._get_bars_cached("SPY", "4Hour"),
        bars_routes._get_bars_cached("SPY", "4Hour"),
    )
    assert len(provider.latest_calls) == 1
    assert a is b and a is not None and a["symbol"] == "SPY"


@pytest.mark.asyncio
async def test_futures_failure_serves_stale_and_never_hops_to_alpaca(monkeypatch) -> None:
    provider = _Provider(fail=True)
    monkeypatch.setattr(bars_routes, "get_market_data_provider", lambda: provider)
    alpaca_calls: list[str] = []
    monkeypatch.setattr(
        bars_routes, "_require_alpaca", lambda: alpaca_calls.append("hop") or None
    )
    key = ("/ES", "4Hour", bars_routes._bars_extended_default("4Hour"))
    stale = {"symbol": "/ES", "bars": [{"time": "2026-07-21T04:00:00Z", "close": 7500.0}]}
    bars_routes._BARS_CACHE[key] = (0.0, stale)  # expired — stale on purpose

    out = await bars_routes._get_bars_cached("/ES", "4Hour")
    assert out == stale  # last-good series through the WAF cooldown
    assert alpaca_calls == []  # Alpaca has no futures — the hop must not happen


@pytest.mark.asyncio
async def test_equity_failure_still_falls_back_to_alpaca(monkeypatch) -> None:
    provider = _Provider(fail=True)
    fallback = _Provider()
    monkeypatch.setattr(bars_routes, "get_market_data_provider", lambda: provider)
    monkeypatch.setattr(bars_routes, "_require_alpaca", lambda: fallback)
    out = await bars_routes._get_bars_cached("SPY", "1Day")
    assert [c["sym"] for c in fallback.latest_calls] == ["SPY"]
    assert out is not None and out["symbol"] == "SPY"


@pytest.mark.asyncio
async def test_total_failure_with_nothing_stored_is_none(monkeypatch) -> None:
    provider = _Provider(fail=True)
    monkeypatch.setattr(bars_routes, "get_market_data_provider", lambda: provider)
    monkeypatch.setattr(bars_routes, "_require_alpaca", lambda: _Provider(fail=True))
    assert await bars_routes._get_bars_cached("SPY", "1Day") is None
