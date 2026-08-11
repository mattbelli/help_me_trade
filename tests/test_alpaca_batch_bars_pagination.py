"""Batch bar pagination must never return silently-truncated data.

Alpaca fills bar pages symbol-alphabetically, so a batch that exhausts the page
budget loses the TAIL OF THE ALPHABET and reports it to the caller as "no data
for N symbols" — indistinguishable from those tickers not existing. Measured
2026-08-11: a 503-symbol × 5-year daily request needs ~627k bars against a
50 × 10,000 budget; SHW/SJM/SLB/SMCI/SNA came back empty while AAPL got all
1,253 sessions, and nothing raised.
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

_BUDGET = _MAX_BAR_PAGES * _BARS_PAGE_LIMIT


@pytest.mark.parametrize(
    "timeframe,days",
    [("1Day", 12), ("1Day", 480), ("1Day", 1825), ("1Day", 3650),
     ("5Min", 30), ("1Min", 5), ("1Week", 3650)],
)
def test_chunk_size_never_exceeds_the_page_budget(timeframe: str, days: int) -> None:
    size = AlpacaAdapter._batch_chunk_size(timeframe=timeframe, days=days)
    per_symbol = days * _SESSIONS_PER_CALENDAR_DAY[timeframe]
    assert size >= 1
    assert size * per_symbol <= _BUDGET


def test_production_default_depth_is_unchanged() -> None:
    """The momentum refresh runs at 480 days and trades on the result. This fix
    must not quietly reshape that request — only deep ones."""
    assert AlpacaAdapter._batch_chunk_size(timeframe="1Day", days=480) == 500


def test_five_year_daily_request_is_chunked_below_the_old_500() -> None:
    """The exact request that lost 97 symbols."""
    assert AlpacaAdapter._batch_chunk_size(timeframe="1Day", days=1825) < 500


def test_unknown_timeframe_falls_back_to_the_url_cap() -> None:
    assert AlpacaAdapter._batch_chunk_size(timeframe="3Min", days=999) == 500


@pytest.mark.asyncio
async def test_capped_batch_pagination_raises_instead_of_truncating(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A batch that never stops paginating must ERROR, not hand back a partial
    dict the caller will read as 'those symbols have no data'."""
    adapter = AlpacaAdapter(
        base_url="https://paper-api.example", key_id="k", secret="s"
    )

    async def endless(path: str, *, params: dict) -> dict:
        return {"bars": {"AAPL": [{"t": "2026-01-01", "o": 1, "h": 1, "l": 1,
                                   "c": 1, "v": 1}]},
                "next_page_token": "more"}

    monkeypatch.setattr(adapter, "_aget_data", endless)
    with pytest.raises(BrokerError, match="silently truncated"):
        await adapter._aget_all_bar_pages("/v2/stocks/bars", {"symbols": "AAPL"})


@pytest.mark.asyncio
async def test_capped_single_symbol_pagination_still_only_warns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Single-symbol paths return a list and lose only the far end of ONE series —
    noisy, not silently wrong about which symbols exist. Contract unchanged."""
    adapter = AlpacaAdapter(
        base_url="https://paper-api.example", key_id="k", secret="s"
    )

    async def endless(path: str, *, params: dict) -> dict:
        return {"bars": [{"t": "2026-01-01", "o": 1, "h": 1, "l": 1, "c": 1, "v": 1}],
                "next_page_token": "more"}

    monkeypatch.setattr(adapter, "_aget_data", endless)
    out = await adapter._aget_all_bar_pages("/v2/stocks/AAPL/bars", {})
    assert len(out["bars"]) == _MAX_BAR_PAGES
