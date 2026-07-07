"""Regression: WS quote snapshots must never carry zero/priceless rows.

A backend restart during RTH with the tick stream down leaves poll-created
rows at the QuoteState default price=0.0 (the ext poll creates the row, so
the baseline seeder skips it as "present"). Sending those rows let the
client's bulk merge clobber good REST quotes with zeros — the founder's
watchlist showed 0.00 for 22 of 25 symbols (2026-07-07 open).
"""

from __future__ import annotations

import pytest

from brokers.market_stream import BaseMarketHub, QuoteState


class _CaptureWS:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send_json(self, payload: dict) -> None:
        self.sent.append(payload)


@pytest.mark.asyncio
async def test_send_quotes_drops_priceless_rows() -> None:
    hub = BaseMarketHub()
    ws = _CaptureWS()
    await hub._send_quotes(
        ws,
        {
            "AAPL": QuoteState(price=314.7, change_pct=0.5),
            "CRWD": QuoteState(),  # poll-created, never admitted a price
            "SHAZ": QuoteState(price=0.0, ext_price=74.3),  # ext-only is fine
        },
    )
    assert len(ws.sent) == 1
    sent = ws.sent[0]["quotes"]
    assert set(sent) == {"AAPL", "SHAZ"}
    assert sent["AAPL"]["price"] == 314.7


@pytest.mark.asyncio
async def test_send_quotes_all_priceless_sends_nothing() -> None:
    hub = BaseMarketHub()
    ws = _CaptureWS()
    await hub._send_quotes(ws, {"CRWD": QuoteState(), "GLW": QuoteState()})
    assert ws.sent == []
