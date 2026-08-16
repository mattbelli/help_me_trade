"""Post-market / extended-hours chart support via Schwab."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from schwab.client import Client

from brokers.alpaca import AlpacaAdapter, StockBar
from brokers.errors import BrokerError
from brokers.schwab_market_data import SchwabMarketData

# ---------------------------------------------------------------------------
# _aggregate_to_hourly
# ---------------------------------------------------------------------------


def _bar(
    time: str,
    *,
    open: float = 100.0,
    high: float = 101.0,
    low: float = 99.0,
    close: float = 100.5,
    volume: float = 1000.0,
) -> StockBar:
    return StockBar(
        time=time,
        open=open,
        high=high,
        low=low,
        close=close,
        volume=volume,
    )


def test_aggregate_to_hourly_empty() -> None:
    assert SchwabMarketData._aggregate_to_hourly([]) == []


def test_aggregate_to_hourly_lone_half_hour_bucket() -> None:
    bars = [_bar("2026-06-23T14:30:00Z", open=10, high=12, low=9, close=11, volume=500)]
    hourly = SchwabMarketData._aggregate_to_hourly(bars)
    assert len(hourly) == 1
    assert hourly[0].time == "2026-06-23T14:00:00Z"
    assert hourly[0].open == 10
    assert hourly[0].high == 12
    assert hourly[0].low == 9
    assert hourly[0].close == 11
    assert hourly[0].volume == 500


def test_aggregate_to_hourly_multiple_hours_and_two_halves() -> None:
    bars = [
        _bar("2026-06-23T13:00:00Z", open=1, high=2, low=0.5, close=1.5, volume=100),
        _bar("2026-06-23T13:30:00Z", open=1.5, high=3, low=1, close=2.5, volume=200),
        _bar("2026-06-23T14:00:00Z", open=10, high=11, low=9, close=10.5, volume=300),
        _bar("2026-06-23T15:30:00Z", open=20, high=22, low=19, close=21, volume=400),
    ]
    hourly = SchwabMarketData._aggregate_to_hourly(bars)
    assert [b.time for b in hourly] == [
        "2026-06-23T13:00:00Z",
        "2026-06-23T14:00:00Z",
        "2026-06-23T15:00:00Z",
    ]
    h13 = hourly[0]
    assert h13.open == 1
    assert h13.high == 3
    assert h13.low == 0.5
    assert h13.close == 2.5
    assert h13.volume == 300

    h14 = hourly[1]
    assert h14.open == 10
    assert h14.high == 11
    assert h14.low == 9
    assert h14.close == 10.5
    assert h14.volume == 300

    h15 = hourly[2]
    assert h15.open == 20
    assert h15.close == 21
    assert h15.volume == 400


# ---------------------------------------------------------------------------
# _aggregate_to_4hourly — futures anchor on the 18:00 ET Globex open
# ---------------------------------------------------------------------------


def test_aggregate_to_4hourly_utc_anchored_by_default() -> None:
    """Non-futures keep UTC-midnight buckets (00/04/08/12/16/20 UTC) — both bars
    fall in the same 20:00Z bucket, which is exactly what futures must NOT do."""
    bars = [_bar("2026-08-11T21:30:00Z"), _bar("2026-08-11T22:00:00Z")]
    four = SchwabMarketData._aggregate_to_4hourly(bars)
    assert [b.time for b in four] == ["2026-08-11T20:00:00Z"]


def test_aggregate_to_4hourly_futures_anchor_on_session_open() -> None:
    """22:00Z = 18:00 ET is a session OPEN — it must start its own bucket, not
    fall into the 20:00Z bucket that holds the prior session's 17:30 ET bar."""
    bars = [
        _bar("2026-08-11T21:30:00Z"),  # 17:30 ET — prior session's 14:00 ET stub
        _bar("2026-08-11T22:00:00Z"),  # 18:00 ET — new session opens
        _bar("2026-08-12T01:30:00Z"),  # 21:30 ET — same 18:00 ET bucket
        _bar("2026-08-12T02:00:00Z"),  # 22:00 ET — next bucket
    ]
    four = SchwabMarketData._aggregate_to_4hourly(bars, session_anchored=True)
    assert [b.time for b in four] == [
        "2026-08-11T18:00:00Z",  # 14:00 ET stub (14:00–17:00 ET)
        "2026-08-11T22:00:00Z",  # 18:00 ET session open
        "2026-08-12T02:00:00Z",  # 22:00 ET
    ]


def test_aggregate_to_4hourly_futures_anchor_survives_dst() -> None:
    """Under EST the 18:00 ET open is 23:00Z — the bucket must follow the ET
    wall clock, not stay pinned to the summer 22:00Z grid."""
    bars = [_bar("2026-01-05T23:00:00Z"), _bar("2026-01-06T02:30:00Z")]
    four = SchwabMarketData._aggregate_to_4hourly(bars, session_anchored=True)
    assert [b.time for b in four] == ["2026-01-05T23:00:00Z"]


# ---------------------------------------------------------------------------
# extended_hours threads to Schwab client
# ---------------------------------------------------------------------------


class _FakeResponse:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._payload


def _provider(client: MagicMock | None = None) -> SchwabMarketData:
    return SchwabMarketData(client or MagicMock())


@pytest.mark.asyncio
async def test_extended_hours_false_by_default() -> None:
    client = MagicMock()
    client.get_price_history.return_value = _FakeResponse(
        {"candles": [], "empty": True}
    )
    provider = _provider(client)
    start = datetime(2026, 6, 23, tzinfo=UTC)
    end = datetime(2026, 6, 24, tzinfo=UTC)

    await provider.aget_stock_bars_range(
        "AAPL", timeframe="5Min", start=start, end=end
    )

    kwargs = client.get_price_history.call_args.kwargs
    assert kwargs["need_extended_hours_data"] is False


@pytest.mark.asyncio
async def test_extended_hours_true_passed_to_client() -> None:
    client = MagicMock()
    client.get_price_history.return_value = _FakeResponse(
        {"candles": [], "empty": True}
    )
    provider = _provider(client)
    start = datetime(2026, 6, 23, tzinfo=UTC)
    end = datetime(2026, 6, 24, tzinfo=UTC)

    await provider.aget_stock_bars_range(
        "AAPL", timeframe="5Min", start=start, end=end, extended_hours=True
    )

    kwargs = client.get_price_history.call_args.kwargs
    assert kwargs["need_extended_hours_data"] is True


# ---------------------------------------------------------------------------
# 1Hour via 30Min aggregation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_aget_stock_bars_one_hour_fetches_30min_and_aggregates() -> None:
    client = MagicMock()
    epoch_1300 = 1_782_219_600_000  # 2026-06-23T13:00:00Z
    epoch_1330 = 1_782_221_400_000  # 2026-06-23T13:30:00Z
    client.get_price_history.return_value = _FakeResponse(
        {
            "candles": [
                {
                    "open": 1.0,
                    "high": 2.0,
                    "low": 0.5,
                    "close": 1.5,
                    "volume": 100,
                    "datetime": epoch_1300,
                },
                {
                    "open": 1.5,
                    "high": 3.0,
                    "low": 1.0,
                    "close": 2.5,
                    "volume": 200,
                    "datetime": epoch_1330,
                },
            ],
            "empty": False,
        }
    )
    provider = _provider(client)

    bars = await provider.aget_stock_bars(
        "AAPL", timeframe="1Hour", days=5, extended_hours=True
    )

    kwargs = client.get_price_history.call_args.kwargs
    assert kwargs["frequency"] == Client.PriceHistory.Frequency.EVERY_THIRTY_MINUTES
    assert kwargs["need_extended_hours_data"] is True
    assert len(bars) == 1
    assert bars[0].time == "2026-06-23T13:00:00Z"
    assert bars[0].open == 1
    assert bars[0].close == 2.5
    assert bars[0].volume == 300


@pytest.mark.asyncio
async def test_aget_stock_bars_range_one_hour_uses_30min_frequency() -> None:
    client = MagicMock()
    client.get_price_history.return_value = _FakeResponse(
        {
            "candles": [
                {
                    "open": 1.0,
                    "high": 2.0,
                    "low": 0.5,
                    "close": 1.5,
                    "volume": 100,
                    "datetime": 1_782_219_600_000,  # 2026-06-23T13:00:00Z
                },
                {
                    "open": 1.5,
                    "high": 3.0,
                    "low": 1.0,
                    "close": 2.5,
                    "volume": 200,
                    "datetime": 1_782_221_400_000,  # 2026-06-23T13:30:00Z
                },
            ],
            "empty": False,
        }
    )
    provider = _provider(client)
    start = datetime(2026, 6, 23, tzinfo=UTC)
    end = datetime(2026, 6, 24, tzinfo=UTC)

    bars = await provider.aget_stock_bars_range(
        "AAPL", timeframe="1Hour", start=start, end=end, extended_hours=True
    )

    kwargs = client.get_price_history.call_args.kwargs
    assert kwargs["frequency"] == Client.PriceHistory.Frequency.EVERY_THIRTY_MINUTES
    assert kwargs["need_extended_hours_data"] is True
    assert len(bars) == 1
    assert bars[0].time == "2026-06-23T13:00:00Z"


# ---------------------------------------------------------------------------
# /api/bars endpoint
# ---------------------------------------------------------------------------


@pytest.fixture()
def test_client():
    from fastapi.testclient import TestClient

    from api.main import app

    with TestClient(app, raise_server_exceptions=True) as client:
        yield client


def _sample_bars() -> list[StockBar]:
    return [
        StockBar(
            time="2026-06-23T13:00:00Z",
            open=100.0,
            high=101.0,
            low=99.0,
            close=100.5,
            volume=1000.0,
        )
    ]


def test_api_bars_uses_provider_with_extended_hours(
    test_client, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider = MagicMock(spec=SchwabMarketData)
    provider.aget_stock_bars = AsyncMock(return_value=_sample_bars())
    monkeypatch.setattr("api.bars_routes.get_market_data_provider", lambda: provider)

    resp = test_client.get("/api/bars?symbol=AAPL&timeframe=5Min&days=10")
    assert resp.status_code == 200
    data = resp.json()
    assert data["symbol"] == "AAPL"
    assert data["timeframe"] == "5Min"
    assert len(data["bars"]) == 1

    provider.aget_stock_bars.assert_awaited_once_with(
        "AAPL", timeframe="5Min", days=10, extended_hours=True
    )


def test_api_bars_falls_back_to_alpaca_on_provider_error(
    test_client, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider = MagicMock(spec=SchwabMarketData)
    provider.aget_stock_bars = AsyncMock(
        side_effect=BrokerError("Schwab unavailable")
    )
    alpaca = MagicMock(spec=AlpacaAdapter)
    alpaca.aget_stock_bars = AsyncMock(return_value=_sample_bars())

    monkeypatch.setattr("api.bars_routes.get_market_data_provider", lambda: provider)
    monkeypatch.setattr("api.bars_routes._require_alpaca", lambda: alpaca)

    resp = test_client.get("/api/bars?symbol=SPY&timeframe=5Min&days=10")
    assert resp.status_code == 200
    assert resp.json()["symbol"] == "SPY"

    provider.aget_stock_bars.assert_awaited_once()
    assert provider.aget_stock_bars.call_args.kwargs["extended_hours"] is True
    # Alpaca fallback stays RTH-only (free IEX feed ignores ext anyway).
    alpaca.aget_stock_bars.assert_awaited_once_with(
        "SPY", timeframe="5Min", days=10
    )


def test_api_bars_resolves_extended_flag(
    test_client, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The endpoint resolves the ext flag: intraday → ON, daily+ → OFF, explicit
    ``extended`` query param wins. Mock the fetch core so the assertion is hermetic
    (no cache/prewarm/provider interaction)."""
    calls: list[tuple] = []

    async def _fake(
        sym: str, timeframe: str, days: int, extended: bool, *, stale_ok: bool = False
    ) -> dict:
        calls.append((sym, timeframe, days, extended))
        return {"symbol": sym, "timeframe": timeframe, "bars": []}

    monkeypatch.setattr("api.bars_routes._get_bars_cached", _fake)

    assert test_client.get("/api/bars?symbol=AAA&timeframe=1Day&days=365").status_code == 200
    assert test_client.get("/api/bars?symbol=BBB&timeframe=5Min&days=10").status_code == 200
    assert (
        test_client.get(
            "/api/bars?symbol=CCC&timeframe=5Min&days=10&extended=false"
        ).status_code
        == 200
    )
    assert (
        test_client.get(
            "/api/bars?symbol=DDD&timeframe=1Day&days=365&extended=true"
        ).status_code
        == 200
    )

    assert calls == [
        ("AAA", "1Day", 365, False),  # daily default OFF
        ("BBB", "5Min", 10, True),  # intraday default ON
        ("CCC", "5Min", 10, False),  # explicit override wins
        ("DDD", "1Day", 365, True),  # explicit override wins
    ]


@pytest.mark.asyncio
async def test_get_bars_cached_threads_extended_to_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The fetch core resolves the per-timeframe default and threads it to the
    provider + into the cache key (ext / RTH must not alias)."""
    import api.bars_routes as bars_routes

    bars_routes._BARS_CACHE.clear()
    provider = MagicMock(spec=SchwabMarketData)
    provider.aget_stock_bars = AsyncMock(return_value=_sample_bars())
    monkeypatch.setattr("api.bars_routes.get_market_data_provider", lambda: provider)

    # Daily → ext OFF by default.
    await bars_routes._get_bars_cached("QDLY", "1Day", 365)
    provider.aget_stock_bars.assert_awaited_once_with(
        "QDLY", timeframe="1Day", days=365, extended_hours=False
    )
    assert ("QDLY", "1Day", 365, False) in bars_routes._BARS_CACHE

    # Intraday → ext ON by default (distinct cache key from an RTH-only variant).
    provider.aget_stock_bars.reset_mock()
    await bars_routes._get_bars_cached("QINT", "5Min", 10)
    provider.aget_stock_bars.assert_awaited_once_with(
        "QINT", timeframe="5Min", days=10, extended_hours=True
    )
    assert ("QINT", "5Min", 10, True) in bars_routes._BARS_CACHE

    # Explicit override on the same intraday spec → different key, refetched.
    provider.aget_stock_bars.reset_mock()
    await bars_routes._get_bars_cached("QINT", "5Min", 10, extended=False)
    provider.aget_stock_bars.assert_awaited_once_with(
        "QINT", timeframe="5Min", days=10, extended_hours=False
    )
    assert ("QINT", "5Min", 10, False) in bars_routes._BARS_CACHE
