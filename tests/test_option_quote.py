"""Options quote adapter + chat tool."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest

from ai.chat.tools import execute_tool
from brokers.alpaca import AlpacaAdapter
from imports.option_symbol import OptionSymbol, to_occ_symbol


def _adapter() -> AlpacaAdapter:
    return AlpacaAdapter(
        base_url="https://paper-api.alpaca.markets",
        key_id="test-key",
        secret="test-secret",
    )


def test_parse_option_snapshot_bid_ask_mid() -> None:
    adapter = _adapter()
    occ = "TSLA260220C00410000"
    data = {
        "snapshots": {
            occ: {
                "latestQuote": {
                    "bp": 3.45,
                    "ap": 3.55,
                    "t": "2026-02-20T15:30:00Z",
                },
                "latestTrade": {"p": 3.50, "t": "2026-02-20T15:29:00Z"},
            }
        }
    }
    parsed = adapter._parse_option_snapshot(data, occ)
    assert parsed is not None
    assert parsed["symbol"] == occ
    assert parsed["bid"] == 3.45
    assert parsed["ask"] == 3.55
    assert parsed["mid"] == pytest.approx(3.50)
    assert parsed["last"] == 3.50
    assert parsed["as_of"] == "2026-02-20T15:30:00Z"


def test_get_option_quote_returns_none_on_empty(monkeypatch) -> None:
    adapter = _adapter()

    def fake_get_data(path: str, *, params=None):
        return {"snapshots": {}}

    monkeypatch.setattr(adapter, "_get_data", fake_get_data)
    assert adapter.get_option_quote("TSLA260220C00410000") is None


def test_get_option_quote_returns_none_on_error(monkeypatch) -> None:
    adapter = _adapter()

    def fake_get_data(path: str, *, params=None):
        raise RuntimeError("network down")

    monkeypatch.setattr(adapter, "_get_data", fake_get_data)
    assert adapter.get_option_quote("TSLA260220C00410000") is None


@pytest.mark.asyncio
async def test_aget_option_quote_uses_snapshot(monkeypatch) -> None:
    adapter = _adapter()
    occ = "TSLA260220C00410000"
    payload = {
        "snapshots": {
            occ: {
                "latestQuote": {"bp": 1.0, "ap": 1.2, "t": "2026-02-20T15:30:00Z"},
            }
        }
    }

    async def fake_aget_data(path: str, *, params=None):
        if path.endswith(occ):
            return payload
        return {"snapshots": {}}

    monkeypatch.setattr(adapter, "_aget_data", fake_aget_data)
    quote = await adapter.aget_option_quote(occ)
    assert quote is not None
    assert quote["bid"] == 1.0
    assert quote["ask"] == 1.2
    assert quote["mid"] == pytest.approx(1.1)


@pytest.mark.asyncio
async def test_get_option_quote_tool_builds_occ_from_args(monkeypatch) -> None:
    opt = OptionSymbol(
        underlying="TSLA",
        expiration=date(2026, 2, 20),
        strike=Decimal("410"),
        right="C",
    )
    occ = to_occ_symbol(opt)

    class StubBroker:
        async def aget_option_quote(self, symbol: str):
            assert symbol == occ
            return {
                "symbol": symbol,
                "bid": 2.0,
                "ask": 2.2,
                "mid": 2.1,
                "last": 2.1,
                "as_of": "2026-02-20T15:30:00Z",
            }

    monkeypatch.setattr("brokers.factory.get_broker", lambda: StubBroker())

    result = await execute_tool(
        "get_option_quote",
        {
            "underlying": "TSLA",
            "expiration": "2026-02-20",
            "strike": 410,
            "right": "C",
        },
    )
    assert result["occ_symbol"] == occ
    assert result["bid"] == 2.0
    assert result["ask"] == 2.2
    assert result["mid"] == pytest.approx(2.1)


@pytest.mark.asyncio
async def test_get_option_quote_tool_no_quote(monkeypatch) -> None:
    class StubBroker:
        aget_option_quote = AsyncMock(return_value=None)

    monkeypatch.setattr("brokers.factory.get_broker", lambda: StubBroker())

    result = await execute_tool(
        "get_option_quote",
        {"occ_symbol": "TSLA260220C00410000"},
    )
    assert result["status"] == "no_quote"
    assert "No live options data" in result["message"]
    assert result["occ_symbol"] == "TSLA260220C00410000"


@pytest.mark.asyncio
async def test_get_option_quote_tool_with_occ_symbol(monkeypatch) -> None:
    class StubBroker:
        async def aget_option_quote(self, symbol: str):
            return {
                "symbol": symbol,
                "bid": 0.5,
                "ask": 0.6,
                "mid": 0.55,
                "last": 0.55,
                "as_of": "2026-02-20T15:30:00Z",
            }

    monkeypatch.setattr("brokers.factory.get_broker", lambda: StubBroker())

    result = await execute_tool(
        "get_option_quote",
        {"occ_symbol": "TSLA260220C00410000"},
    )
    assert result["occ_symbol"] == "TSLA260220C00410000"
    assert result["bid"] == 0.5
