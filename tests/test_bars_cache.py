"""Chart bars cache: prewarm key parity, request coalescing, stale-if-error.

Pins the 07-22 founder-reported chart outage. Root causes: (1) the prewarm
loop's skip-check used a 3-tuple key against a 4-tuple cache — never matched,
so it refetched every symbol × spec every 15s (each a 10–30-page Alpaca
pagination walk) and starved interactive loads; (2) concurrent identical
requests each ran their own full walk; (3) futures fell back to Alpaca (which
has no futures data) instead of serving the last-good series through a Schwab
WAF cooldown.
"""

from __future__ import annotations

import asyncio

import pytest

import api.main as api_main


class _Bar:
    def __init__(self, close: float = 100.0) -> None:
        self.time = "2026-07-21T04:00:00+00:00"
        self.open = self.high = self.low = self.close = close
        self.volume = 1.0


class _Provider:
    def __init__(self, fail: bool = False, delay: float = 0.0) -> None:
        self.fail = fail
        self.delay = delay
        self.calls: list[str] = []

    async def aget_stock_bars(self, sym, *, timeframe, days, extended_hours=None):
        self.calls.append(sym)
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.fail:
            raise RuntimeError("provider down")
        return [_Bar()]


@pytest.fixture(autouse=True)
def _clean_cache():
    api_main._BARS_CACHE.clear()
    api_main._BARS_INFLIGHT.clear()
    api_main._BARS_FULL_AT.clear()
    yield
    api_main._BARS_CACHE.clear()
    api_main._BARS_INFLIGHT.clear()
    api_main._BARS_FULL_AT.clear()


def _warm(key: tuple, payload: dict, ttl: float = 600.0) -> None:
    import time

    api_main._BARS_CACHE[key] = (time.monotonic() + ttl, payload)


@pytest.mark.asyncio
async def test_prewarm_skip_check_matches_the_real_cache_key(monkeypatch) -> None:
    """The storm bug: a warm entry must make the prewarm skip the fetch."""
    provider = _Provider()
    monkeypatch.setattr(api_main, "get_market_data_provider", lambda: provider)
    monkeypatch.setattr(api_main, "_all_watchlist_symbols", lambda: ["SPY"])
    monkeypatch.setattr(api_main, "_BARS_PREWARM_DEFAULT_SPECS", {("1Day", 1825)})
    monkeypatch.setattr(api_main, "_BARS_SEEN_SPECS", {})
    monkeypatch.setattr(api_main, "_BARS_SEEN_SYMBOLS", {})
    # Warm entry under the REAL key shape (incl. the ext default for 1Day).
    _warm(("SPY", "1Day", 1825, api_main._bars_extended_default("1Day")), {"bars": []})

    marks: list[str] = []
    monkeypatch.setattr(api_main, "mark_alive", lambda name: marks.append(name))
    await api_main._bars_prewarm_once()
    assert provider.calls == []  # warm → no refetch; 3-tuple lookup would refetch
    # Heartbeat is per-symbol progress, not per completed cycle — a multi-minute
    # cold warm must not false-stale the watchdog.
    assert marks == ["bars_prewarm"]


@pytest.mark.asyncio
async def test_prewarm_warms_viewed_combos_not_the_cross_product(monkeypatch) -> None:
    """Round 2 (watchdog-caught): seen specs are per-(symbol, timeframe, days)
    combo. One symbol viewed on 4Hour must NOT drag every watchlist symbol
    through its own 10–30-page 4Hour walk — that cycle runs longer than the
    TTLs it refreshes, i.e. a permanent slow-motion refetch storm."""
    import time as _time

    provider = _Provider()
    monkeypatch.setattr(api_main, "get_market_data_provider", lambda: provider)
    monkeypatch.setattr(api_main, "_all_watchlist_symbols", lambda: ["SPY", "QQQ"])
    monkeypatch.setattr(api_main, "_BARS_PREWARM_DEFAULT_SPECS", set())
    monkeypatch.setattr(api_main, "_BARS_PREWARM_PACE", 0.0)
    monkeypatch.setattr(
        api_main, "_BARS_SEEN_SPECS", {("SPY", "4Hour", 365): _time.monotonic()}
    )
    monkeypatch.setattr(api_main, "_BARS_SEEN_SYMBOLS", {})
    monkeypatch.setattr(api_main, "mark_alive", lambda name: None)

    await api_main._bars_prewarm_once()
    assert provider.calls == ["SPY"]  # QQQ never dragged through 4Hour


@pytest.mark.asyncio
async def test_concurrent_identical_requests_share_one_fetch(monkeypatch) -> None:
    provider = _Provider(delay=0.05)
    monkeypatch.setattr(api_main, "get_market_data_provider", lambda: provider)

    a, b = await asyncio.gather(
        api_main._get_bars_cached("SPY", "4Hour", 365),
        api_main._get_bars_cached("SPY", "4Hour", 365),
    )
    assert len(provider.calls) == 1
    assert a is b and a is not None and a["symbol"] == "SPY"


@pytest.mark.asyncio
async def test_futures_failure_serves_stale_and_never_hops_to_alpaca(monkeypatch) -> None:
    provider = _Provider(fail=True)
    monkeypatch.setattr(api_main, "get_market_data_provider", lambda: provider)
    alpaca_calls: list[str] = []
    monkeypatch.setattr(
        api_main, "_require_alpaca", lambda: alpaca_calls.append("hop") or None
    )
    ext = api_main._bars_extended_default("4Hour")
    stale = {"symbol": "/ES", "bars": [{"close": 7500.0}]}
    _warm(("/ES", "4Hour", 90, ext), stale, ttl=-5.0)  # expired — stale on purpose

    out = await api_main._get_bars_cached("/ES", "4Hour", 90)
    assert out == stale  # last-good series through the WAF cooldown
    assert alpaca_calls == []  # Alpaca has no futures — the hop must not happen


@pytest.mark.asyncio
async def test_equity_failure_still_falls_back_to_alpaca(monkeypatch) -> None:
    provider = _Provider(fail=True)
    fallback = _Provider()
    monkeypatch.setattr(api_main, "get_market_data_provider", lambda: provider)
    monkeypatch.setattr(api_main, "_require_alpaca", lambda: fallback)

    out = await api_main._get_bars_cached("SPY", "1Day", 365)
    assert fallback.calls == ["SPY"]
    assert out is not None and out["symbol"] == "SPY"


# --- incremental refresh (fetch-since-last-bar merge) ---------------------------


class _TBar:
    def __init__(self, time: str, close: float) -> None:
        self.time = time
        self.open = self.high = self.low = self.close = close
        self.volume = 1.0


class _SeriesProvider:
    """Full series on a big-days walk; a small trailing window on a small-days call.
    ``calls`` records the requested ``days`` so a test can prove which path ran."""

    def __init__(self, full, tail, *, empty_tail: bool = False) -> None:
        self.full = full
        self.tail = tail
        self.empty_tail = empty_tail
        self.calls: list[int] = []

    async def aget_stock_bars(self, sym, *, timeframe, days, extended_hours=None):
        self.calls.append(days)
        if days <= 10:  # the incremental window (1Day inc = 7d)
            return [] if self.empty_tail else list(self.tail)
        return list(self.full)


def _expire(key: tuple) -> None:
    """Force the cache entry expired (monotonic 0) without dropping it, so the next
    read refetches while the retained payload still seeds the incremental merge."""
    _, payload = api_main._BARS_CACHE[key]
    api_main._BARS_CACHE[key] = (0.0, payload)


@pytest.mark.asyncio
async def test_warm_refresh_is_incremental_and_merges_over_cached(monkeypatch) -> None:
    """A refresh of a fresh-baseline series fetches only the small trailing window
    and merges it over the cached bars — the still-forming last bar is replaced (not
    duplicated) and new bars appended. This is the whole point of fix #1."""
    full = [
        _TBar("2026-07-20T04:00:00Z", 10.0),
        _TBar("2026-07-21T04:00:00Z", 11.0),
        _TBar("2026-07-22T04:00:00Z", 12.0),  # last bar, still forming
    ]
    tail = [
        _TBar("2026-07-22T04:00:00Z", 12.5),  # same day, updated close
        _TBar("2026-07-23T04:00:00Z", 13.0),  # a new bar
    ]
    provider = _SeriesProvider(full, tail)
    monkeypatch.setattr(api_main, "get_market_data_provider", lambda: provider)
    ext = api_main._bars_extended_default("1Day")
    key = ("SPY", "1Day", 1825, ext)

    first = await api_main._get_bars_cached("SPY", "1Day", 1825)
    assert provider.calls == [1825]  # cold → one full walk
    assert key in api_main._BARS_FULL_AT
    baseline_ts = api_main._BARS_FULL_AT[key]
    assert [b["close"] for b in first["bars"]] == [10.0, 11.0, 12.0]

    _expire(key)
    second = await api_main._get_bars_cached("SPY", "1Day", 1825)
    assert provider.calls == [1825, 7]  # incremental window, NOT a second full walk
    assert [b["time"] for b in second["bars"]] == [
        "2026-07-20T04:00:00Z",
        "2026-07-21T04:00:00Z",
        "2026-07-22T04:00:00Z",
        "2026-07-23T04:00:00Z",
    ]
    # boundary bar overwritten by its fresh copy (12.0 → 12.5), new bar appended
    assert [b["close"] for b in second["bars"]] == [10.0, 11.0, 12.5, 13.0]
    # incremental did not re-baseline — the full-fetch timestamp is unchanged
    assert api_main._BARS_FULL_AT[key] == baseline_ts


@pytest.mark.asyncio
async def test_rebaseline_forces_full_refetch_after_interval(monkeypatch) -> None:
    """After _BARS_FULL_REBASELINE, a refresh re-walks the full range so a split
    adjustment / history revision propagates instead of living on stale bars."""
    import time as _time

    full = [_TBar("2026-07-22T04:00:00Z", 12.0)]
    tail = [_TBar("2026-07-23T04:00:00Z", 13.0)]
    provider = _SeriesProvider(full, tail)
    monkeypatch.setattr(api_main, "get_market_data_provider", lambda: provider)
    ext = api_main._bars_extended_default("1Day")
    key = ("SPY", "1Day", 1825, ext)

    await api_main._get_bars_cached("SPY", "1Day", 1825)
    assert provider.calls == [1825]
    # age the baseline past the rebaseline window
    api_main._BARS_FULL_AT[key] = _time.monotonic() - (api_main._BARS_FULL_REBASELINE + 100)
    _expire(key)

    await api_main._get_bars_cached("SPY", "1Day", 1825)
    assert provider.calls == [1825, 1825]  # full again, not the 7d incremental window


@pytest.mark.asyncio
async def test_incremental_empty_window_falls_back_to_full(monkeypatch) -> None:
    """An empty trailing window (provider hiccup / no recent bars) must not clobber
    the series — it falls through to a full re-fetch."""
    full = [_TBar("2026-07-22T04:00:00Z", 12.0)]
    provider = _SeriesProvider(full, [], empty_tail=True)
    monkeypatch.setattr(api_main, "get_market_data_provider", lambda: provider)
    ext = api_main._bars_extended_default("1Day")
    key = ("SPY", "1Day", 1825, ext)

    await api_main._get_bars_cached("SPY", "1Day", 1825)
    _expire(key)
    out = await api_main._get_bars_cached("SPY", "1Day", 1825)
    assert provider.calls == [1825, 7, 1825]  # tried window (7), empty → full walk
    assert out is not None and [b["close"] for b in out["bars"]] == [12.0]
