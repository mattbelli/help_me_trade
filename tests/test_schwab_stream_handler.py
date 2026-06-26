"""Schwab level-one stream handler — labeled-key contract.

Regression lock for the bug where `_on_equity`/`_on_futures` read content by the
numeric field `.value` (e.g. 3) while schwab-py relabels each content dict to the
field enum's MEMBER NAME ("LAST_PRICE") before invoking the handler. Reading
`.value` returned None for every field → every tick was silently dropped → the WS
never streamed and the phone fell back to the ~6-12s REST crawl.

These tests feed the SAME labeled shape schwab-py produces (member-name keys) and
assert the hub's QuoteState is populated — no network.
"""

from __future__ import annotations

import pytest

from brokers.schwab_market_stream import SchwabMarketStreamHub


def _labeled_equity_msg() -> dict:
    # Keys are enum MEMBER NAMES — exactly what schwab-py's relabel_message emits.
    return {
        "service": "LEVELONE_EQUITIES",
        "content": [
            {
                "key": "AAPL",
                "LAST_PRICE": 200.5,
                "BID_PRICE": 200.45,
                "ASK_PRICE": 200.55,
                "TOTAL_VOLUME": 1_234_000,
                "NET_CHANGE_PERCENT": 1.25,
                "CLOSE_PRICE": 198.0,
            }
        ],
    }


@pytest.mark.asyncio
async def test_on_equity_reads_labeled_keys() -> None:
    hub = SchwabMarketStreamHub()
    hub._on_equity(_labeled_equity_msg())

    state = hub._quotes.get("AAPL")
    assert state is not None, "tick was dropped — handler read the wrong keys"
    assert state.price == pytest.approx(200.5)
    assert state.bid == pytest.approx(200.45)
    assert state.ask == pytest.approx(200.55)
    assert state.volume == pytest.approx(1_234_000)
    assert state.change_pct == pytest.approx(1.25)
    assert state.prev_close == pytest.approx(198.0)


@pytest.mark.asyncio
async def test_on_equity_falls_back_to_mark_when_no_last() -> None:
    hub = SchwabMarketStreamHub()
    hub._on_equity(
        {"content": [{"key": "MSFT", "MARK": 410.25, "CLOSE_PRICE": 409.0}]}
    )
    state = hub._quotes.get("MSFT")
    assert state is not None
    assert state.price == pytest.approx(410.25)


@pytest.mark.asyncio
async def test_on_equity_skips_tick_with_no_price() -> None:
    """A partial tick (bid/ask only, no last/mark) is skipped — not a zero quote."""
    hub = SchwabMarketStreamHub()
    hub._on_equity({"content": [{"key": "TSLA", "BID_PRICE": 250.0}]})
    assert "TSLA" not in hub._quotes


@pytest.mark.asyncio
async def test_on_futures_reads_labeled_keys() -> None:
    hub = SchwabMarketStreamHub()
    hub._on_futures(
        {
            "content": [
                {
                    "key": "/ES",
                    "LAST_PRICE": 5500.25,
                    "FUTURE_CHANGE_PERCENT": -0.42,
                    "CLOSE_PRICE": 5523.5,
                    "TOTAL_VOLUME": 88_000,
                }
            ]
        }
    )
    state = hub._quotes.get("/ES")
    assert state is not None
    assert state.price == pytest.approx(5500.25)
    assert state.change_pct == pytest.approx(-0.42)
    assert state.prev_close == pytest.approx(5523.5)
