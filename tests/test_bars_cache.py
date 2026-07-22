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
    yield
    api_main._BARS_CACHE.clear()
    api_main._BARS_INFLIGHT.clear()


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
