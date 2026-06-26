"""Session-node level compute + API endpoint tests."""

from __future__ import annotations

from datetime import date
from unittest.mock import AsyncMock, MagicMock

import pytest

from brokers.alpaca import StockBar
from brokers.errors import BrokerError
from research.intraday_bars import IntradayBar
from signals.session_node_strategy import (
    compute_session_levels,
    load_session_node_context,
)

DAY1 = date(2024, 6, 3)
DAY2 = date(2024, 6, 4)


def _bar(
    ts: str,
    *,
    o: float | None = None,
    h: float | None = None,
    low: float | None = None,
    c: float | None = None,
    v: float = 1000.0,
) -> IntradayBar:
    close = c if c is not None else 100.0
    return {
        "t": ts,
        "o": o if o is not None else close,
        "h": h if h is not None else close,
        "l": low if low is not None else close,
        "c": close,
        "v": v,
    }


def _rth_bar(
    ny_date: date,
    ny_hour: int,
    ny_minute: int,
    *,
    price: float = 100.0,
    v: float = 1000.0,
) -> IntradayBar:
    utc_hour = ny_hour + 4
    ts = f"{ny_date.isoformat()}T{utc_hour:02d}:{ny_minute:02d}:00+00:00"
    return _bar(ts, o=price, h=price, low=price, c=price, v=v)


def _flat_session(
    ny_date: date,
    price: float,
    count: int,
    *,
    volume: float = 1000.0,
    start_minute: int = 30,
) -> list[IntradayBar]:
    bars: list[IntradayBar] = []
    for i in range(count):
        minute = start_minute + i
        hour = 9 + minute // 60
        minute = minute % 60
        bars.append(_rth_bar(ny_date, hour, minute, price=price, v=volume))
    return bars


def _reference_session(ny_date: date) -> list[IntradayBar]:
    return (
        _flat_session(ny_date, 85.0, 50, volume=10_000.0)
        + _flat_session(ny_date, 100.0, 10, volume=100_000.0, start_minute=80)
        + _flat_session(ny_date, 115.0, 3, volume=500.0, start_minute=90)
    )


def _intraday_to_stockbars(bars: list[IntradayBar]) -> list[StockBar]:
    out: list[StockBar] = []
    for bar in bars:
        ts = bar["t"].replace("+00:00", "Z")
        out.append(
            StockBar(
                time=ts,
                open=bar["o"],
                high=bar["h"],
                low=bar["l"],
                close=bar["c"],
                volume=bar["v"],
            )
        )
    return out


def _patch_provider(monkeypatch, spy_bars: list[StockBar], *, symbol: str = "SPY") -> None:
    provider = MagicMock()
    provider.aget_stock_bars_batch = AsyncMock(return_value={symbol: spy_bars})
    monkeypatch.setattr(
        "signals.session_node_strategy.get_market_data_provider",
        lambda: provider,
    )


@pytest.mark.asyncio
async def test_compute_session_levels_returns_five_named_levels(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prior = _reference_session(DAY1)
    today = [_rth_bar(DAY2, 9, 30, price=100.0)]
    _patch_provider(monkeypatch, _intraday_to_stockbars(prior + today))
    # Market open: today's session is still forming → reference the prior (DAY1).
    monkeypatch.setattr("signals.session_node_strategy.is_market_open", lambda: True)

    payload = await compute_session_levels("SPY")
    assert payload is not None
    assert payload["symbol"] == "SPY"
    assert payload["session_date"] == DAY1.isoformat()
    assert len(payload["levels"]) == 5

    by_name = {level["name"]: level for level in payload["levels"]}
    assert by_name["POC"]["kind"] == "poc"
    assert by_name["VAH"]["kind"] == "resistance"
    assert by_name["VAL"]["kind"] == "support"
    assert by_name["PDH"]["kind"] == "prior_high"
    assert by_name["PDL"]["kind"] == "prior_low"
    for level in payload["levels"]:
        assert isinstance(level["price"], float)
        assert level["price"] > 0


@pytest.mark.asyncio
async def test_compute_session_levels_none_without_prior_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    today_only = [_rth_bar(DAY2, 9, 30, price=100.0)]
    _patch_provider(monkeypatch, _intraday_to_stockbars(today_only))
    # Market open + only a forming session, no prior completed one → None.
    monkeypatch.setattr("signals.session_node_strategy.is_market_open", lambda: True)

    assert await compute_session_levels("SPY") is None


@pytest.mark.asyncio
async def test_compute_session_levels_uses_latest_completed_when_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # After the close / pre-market the latest session is complete → reference IT,
    # so the chart overlay + morning report are never a day stale.
    day1 = _reference_session(DAY1)
    day2 = _reference_session(DAY2)
    _patch_provider(monkeypatch, _intraday_to_stockbars(day1 + day2))
    monkeypatch.setattr("signals.session_node_strategy.is_market_open", lambda: False)

    payload = await compute_session_levels("SPY")
    assert payload is not None
    assert payload["session_date"] == DAY2.isoformat()


@pytest.mark.asyncio
async def test_load_session_node_context_round_trips(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from research.session_node_signal import compute_reference_nodes

    prior = _reference_session(DAY1)
    today = [_rth_bar(DAY2, 9, 30, price=100.0)]
    stock_bars = _intraday_to_stockbars(prior + today)
    _patch_provider(monkeypatch, stock_bars)

    ctx = await load_session_node_context("SPY")
    assert ctx is not None
    assert ctx.session_date == DAY1
    assert ctx.today_bars
    assert ctx.all_bars
    expected_nodes = compute_reference_nodes(prior)
    assert expected_nodes is not None
    assert ctx.nodes == expected_nodes


@pytest.fixture()
def test_client():
    from fastapi.testclient import TestClient

    from api.main import app

    with TestClient(app, raise_server_exceptions=True) as client:
        yield client


def test_api_session_node_levels_shape(
    test_client, monkeypatch: pytest.MonkeyPatch
) -> None:
    prior = _reference_session(DAY1)
    today = [_rth_bar(DAY2, 9, 30, price=100.0)]
    _patch_provider(monkeypatch, _intraday_to_stockbars(prior + today))
    monkeypatch.setattr("signals.session_node_strategy.is_market_open", lambda: True)

    resp = test_client.get("/api/session-node/levels?symbol=SPY")
    assert resp.status_code == 200
    data = resp.json()
    assert data["symbol"] == "SPY"
    assert data["session_date"] == DAY1.isoformat()
    assert len(data["levels"]) == 5


def test_api_session_node_levels_empty_when_no_data(
    test_client, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider = MagicMock()
    provider.aget_stock_bars_batch = AsyncMock(return_value={"SPY": []})
    monkeypatch.setattr(
        "signals.session_node_strategy.get_market_data_provider",
        lambda: provider,
    )

    resp = test_client.get("/api/session-node/levels?symbol=SPY")
    assert resp.status_code == 200
    data = resp.json()
    assert data == {"symbol": "SPY", "session_date": None, "levels": []}


def test_api_session_node_levels_empty_on_broker_error(
    test_client, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider = MagicMock()
    provider.aget_stock_bars_batch = AsyncMock(
        side_effect=BrokerError("market data unavailable")
    )
    monkeypatch.setattr(
        "signals.session_node_strategy.get_market_data_provider",
        lambda: provider,
    )

    resp = test_client.get("/api/session-node/levels?symbol=SPY")
    assert resp.status_code == 200
    data = resp.json()
    assert data == {"symbol": "SPY", "session_date": None, "levels": []}
