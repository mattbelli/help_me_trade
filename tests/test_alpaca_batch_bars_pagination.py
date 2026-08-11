"""Batch bar pagination must never return silently-truncated data.

Alpaca fills bar pages symbol-ALPHABETICALLY, so a batch that exhausts the page
budget loses the TAIL OF THE ALPHABET and reports it to the caller as "no data
for N symbols" — indistinguishable from those tickers not existing. Measured
2026-08-11: a 503-symbol × 5-year daily request needs ~627k bars against a
50 × 10,000 budget; SHW/SJM/SLB/SMCI/SNA came back empty while AAPL got all
1,253 sessions, and nothing raised.

Two halves, and NEITHER alone is a fix: chunk sizing makes capping rare, and the
raise makes a cap that still happens loud. The raise on its own would be a
REGRESSION — a capped chunk returns nothing where it used to return its
alphabetical head.
"""

from __future__ import annotations

import pytest

from brokers.alpaca import (
    _BARS_PAGE_LIMIT,
    _MAX_BAR_PAGES,
    _SESSIONS_PER_CALENDAR_DAY,
    AlpacaAdapter,
)
from brokers.errors import BrokerError

_NOMINAL_BUDGET = _MAX_BAR_PAGES * _BARS_PAGE_LIMIT

# Bars Alpaca ACTUALLY returned for one calendar day, measured against the live
# SIP feed 2026-08-10. Hardcoded on purpose: deriving the expectation from
# _SESSIONS_PER_CALENDAR_DAY would only re-check the implementation's own
# arithmetic and could never catch the constants being wrong — which they were.
# Alpaca serves 04:00-20:00 ET, so intraday counts are EXTENDED HOURS, roughly
# 2.5x what an RTH session implies.
_MEASURED_BARS_PER_SESSION = {"1Day": 1, "1Hour": 16, "5Min": 191, "1Min": 879}


@pytest.mark.parametrize("timeframe,measured", sorted(_MEASURED_BARS_PER_SESSION.items()))
def test_constants_match_what_alpaca_actually_returns(
    timeframe: str, measured: int
) -> None:
    """Guards the assumption, not the arithmetic. RTH numbers here under-counted
    the budget by up to 2.5x on exactly the liquid names a batch asks about."""
    assumed_per_session = _SESSIONS_PER_CALENDAR_DAY[timeframe] * (365 / 252)
    assert assumed_per_session >= measured, (
        f"{timeframe}: assuming {assumed_per_session:.0f} bars/session but Alpaca "
        f"returned {measured} — the budget is under-counted"
    )


@pytest.mark.parametrize(
    "timeframe,days",
    [("1Day", 12), ("1Day", 480), ("1Day", 1825), ("1Day", 3650),
     ("5Min", 30), ("5Min", 365), ("1Min", 5), ("1Week", 3650),
     ("15Min", 365), ("30Min", 90), ("3Min", 200)],
)
def test_chunk_size_stays_within_budget_whenever_that_is_possible(
    timeframe: str, days: int
) -> None:
    size = AlpacaAdapter._batch_chunk_size(timeframe=timeframe, days=days)
    per_symbol = days * _SESSIONS_PER_CALENDAR_DAY.get(
        timeframe, max(_SESSIONS_PER_CALENDAR_DAY.values())
    )
    assert size >= 1
    if per_symbol <= _NOMINAL_BUDGET:  # a single symbol fits; the batch must too
        assert size * per_symbol <= _NOMINAL_BUDGET


def test_the_only_over_budget_cases_are_ones_chunking_cannot_fix() -> None:
    """When ONE symbol's own history exceeds the budget the floor of 1 binds and
    the request will cap — it then RAISES rather than truncating. Chunking sizes
    requests; it cannot make an impossible one possible. This test states that
    boundary so nobody 'fixes' the floor by removing the raise."""
    over = [
        (tf, d)
        for tf in _SESSIONS_PER_CALENDAR_DAY
        for d in range(1, 4001)
        if AlpacaAdapter._batch_chunk_size(timeframe=tf, days=d)
        * (d * _SESSIONS_PER_CALENDAR_DAY[tf])
        > _NOMINAL_BUDGET
    ]
    assert all(
        AlpacaAdapter._batch_chunk_size(timeframe=tf, days=d) == 1 for tf, d in over
    )


def test_production_default_depth_is_unchanged() -> None:
    """The momentum refresh runs at 480 days and a live strategy trades on the
    result. This fix must not quietly reshape that request — only deep ones."""
    assert AlpacaAdapter._batch_chunk_size(timeframe="1Day", days=480) == 500


def test_unknown_timeframe_clamps_low_not_high() -> None:
    """A dict miss must assume the DENSEST timeframe. The old fallback of "1 bar
    per day" resolved 15Min — a real Alpaca timeframe used elsewhere in this repo
    — to a 500-symbol chunk needing ~8M bars against a 500k budget."""
    unknown = AlpacaAdapter._batch_chunk_size(timeframe="7Min", days=365)
    densest = AlpacaAdapter._batch_chunk_size(timeframe="1Min", days=365)
    assert unknown == densest < 500


@pytest.mark.asyncio
async def test_capped_async_batch_raises_instead_of_truncating(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = AlpacaAdapter(base_url="https://paper-api.example", key_id="k", secret="s")

    async def endless(path: str, *, params: dict) -> dict:
        return {"bars": {"AAPL": [{"t": "2026-01-01", "o": 1, "h": 1, "l": 1,
                                   "c": 1, "v": 1}]}, "next_page_token": "more"}

    monkeypatch.setattr(adapter, "_aget_data", endless)
    with pytest.raises(BrokerError, match="silently truncated"):
        await adapter._aget_all_bar_pages("/v2/stocks/bars", {"symbols": "AAPL"})


def test_capped_sync_batch_raises_too(monkeypatch: pytest.MonkeyPatch) -> None:
    """The sync twin is a separate code path with its own callers
    (ai/research/data_sources.py passes a caller-derived depth). An async-only fix
    leaves exactly the same bug reachable."""
    adapter = AlpacaAdapter(base_url="https://paper-api.example", key_id="k", secret="s")

    def endless(path: str, *, params: dict) -> dict:
        return {"bars": {"AAPL": [{"t": "2026-01-01", "o": 1, "h": 1, "l": 1,
                                   "c": 1, "v": 1}]}, "next_page_token": "more"}

    monkeypatch.setattr(adapter, "_get_data", endless)
    with pytest.raises(BrokerError, match="silently truncated"):
        adapter._get_all_bar_pages("/v2/stocks/bars", {"symbols": "AAPL"})


def test_capped_single_symbol_pagination_still_only_warns(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Single-symbol paths lose the far end of ONE series — noisy, not silently
    wrong about which symbols exist. Contract unchanged, and it must SAY so."""
    adapter = AlpacaAdapter(base_url="https://paper-api.example", key_id="k", secret="s")

    def endless(path: str, *, params: dict) -> dict:
        return {"bars": [{"t": "2026-01-01", "o": 1, "h": 1, "l": 1, "c": 1, "v": 1}],
                "next_page_token": "more"}

    monkeypatch.setattr(adapter, "_get_data", endless)
    with caplog.at_level("WARNING"):
        out = adapter._get_all_bar_pages("/v2/stocks/AAPL/bars", {})
    assert len(out["bars"]) == _MAX_BAR_PAGES
    assert "pagination capped" in caplog.text
