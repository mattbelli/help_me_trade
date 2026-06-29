"""Schwab read-only market data provider tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from schwab.client import Client

from brokers.alpaca import AlpacaAdapter
from brokers.base import MarketDataProvider
from brokers.errors import BrokerError
from brokers.factory import get_market_data_provider
from brokers.schwab_market_data import SchwabMarketData

_EPOCH_MS_EARLY = 1_782_221_700_000  # 2026-06-23T13:35:00Z
_EPOCH_MS_LATE = 1_782_225_300_000  # 2026-06-23T14:35:00Z


def _candles_payload(*, empty: bool = False) -> dict:
    if empty:
        return {"candles": [], "symbol": "AAPL", "empty": True}
    return {
        "candles": [
            {
                "open": 200.0,
                "high": 201.0,
                "low": 199.0,
                "close": 200.5,
                "volume": 12_345,
                "datetime": _EPOCH_MS_LATE,
            },
            {
                "open": 100.0,
                "high": 101.0,
                "low": 99.0,
                "close": 100.5,
                "volume": 9_876,
                "datetime": _EPOCH_MS_EARLY,
            },
        ],
        "symbol": "AAPL",
        "empty": False,
    }


class _FakeResponse:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._payload


def _provider(client: MagicMock | None = None) -> SchwabMarketData:
    return SchwabMarketData(client or MagicMock())


def test_parse_candles_maps_fields_and_sorts_ascending() -> None:
    bars = SchwabMarketData._parse_candles(_candles_payload())

    assert len(bars) == 2
    assert bars[0].time == "2026-06-23T13:35:00Z"
    assert bars[1].time == "2026-06-23T14:35:00Z"
    assert bars[0].open == 100.0
    assert bars[0].high == 101.0
    assert bars[0].low == 99.0
    assert bars[0].close == 100.5
    assert bars[0].volume == 9876.0
    assert isinstance(bars[0].volume, float)


def test_parse_candles_empty_payload() -> None:
    assert SchwabMarketData._parse_candles(_candles_payload(empty=True)) == []
    assert SchwabMarketData._parse_candles({"candles": [], "empty": False}) == []


def test_resolve_timeframe_daily_and_minute() -> None:
    daily = SchwabMarketData._resolve_timeframe("1Day")
    assert daily == {
        "period_type": Client.PriceHistory.PeriodType.YEAR,
        "frequency_type": Client.PriceHistory.FrequencyType.DAILY,
        "frequency": Client.PriceHistory.Frequency.DAILY,
    }

    minute = SchwabMarketData._resolve_timeframe("5Min")
    assert minute == {
        "period_type": Client.PriceHistory.PeriodType.DAY,
        "frequency_type": Client.PriceHistory.FrequencyType.MINUTE,
        "frequency": Client.PriceHistory.Frequency.EVERY_FIVE_MINUTES,
    }


def test_resolve_timeframe_one_hour_raises() -> None:
    with pytest.raises(BrokerError, match="1Hour"):
        SchwabMarketData._resolve_timeframe("1Hour")


@pytest.mark.asyncio
async def test_aget_stock_bars_range_daily_call() -> None:
    client = MagicMock()
    client.get_price_history.return_value = _FakeResponse(_candles_payload())
    provider = _provider(client)

    start = datetime(2026, 6, 1, tzinfo=UTC)
    end = datetime(2026, 6, 23, tzinfo=UTC)
    bars = await provider.aget_stock_bars_range(
        "aapl", timeframe="1Day", start=start, end=end
    )

    assert len(bars) == 2
    client.get_price_history.assert_called_once()
    kwargs = client.get_price_history.call_args.kwargs
    assert client.get_price_history.call_args.args[0] == "AAPL"
    assert kwargs["frequency_type"] == Client.PriceHistory.FrequencyType.DAILY
    assert kwargs["need_extended_hours_data"] is False
    assert kwargs["start_datetime"] == start
    assert kwargs["end_datetime"] == end


@pytest.mark.asyncio
async def test_aget_stock_bars_range_minute_call() -> None:
    client = MagicMock()
    client.get_price_history.return_value = _FakeResponse(_candles_payload())
    provider = _provider(client)

    start = datetime(2026, 6, 23, tzinfo=UTC)
    end = datetime(2026, 6, 24, tzinfo=UTC)
    await provider.aget_stock_bars_range(
        "MSFT", timeframe="5Min", start=start, end=end
    )

    kwargs = client.get_price_history.call_args.kwargs
    assert kwargs["period_type"] == Client.PriceHistory.PeriodType.DAY
    assert kwargs["frequency_type"] == Client.PriceHistory.FrequencyType.MINUTE
    assert kwargs["frequency"] == Client.PriceHistory.Frequency.EVERY_FIVE_MINUTES


@pytest.mark.asyncio
async def test_aget_stock_bars_range_one_hour_aggregates_from_30min() -> None:
    client = MagicMock()
    epoch_1300 = 1_782_219_600_000  # 2026-06-23T13:00:00Z
    epoch_1330 = 1_782_221_400_000  # 2026-06-23T13:30:00Z
    client.get_price_history.return_value = _FakeResponse(
        {
            "candles": [
                {
                    "open": 100.0,
                    "high": 101.0,
                    "low": 99.0,
                    "close": 100.5,
                    "volume": 100,
                    "datetime": epoch_1300,
                },
                {
                    "open": 100.5,
                    "high": 102.0,
                    "low": 100.0,
                    "close": 101.5,
                    "volume": 200,
                    "datetime": epoch_1330,
                },
            ],
            "empty": False,
        }
    )
    provider = _provider(client)

    bars = await provider.aget_stock_bars_range(
        "AAPL",
        timeframe="1Hour",
        start=datetime(2026, 6, 1, tzinfo=UTC),
        end=datetime(2026, 6, 2, tzinfo=UTC),
    )

    kwargs = client.get_price_history.call_args.kwargs
    assert kwargs["frequency"] == Client.PriceHistory.Frequency.EVERY_THIRTY_MINUTES
    assert kwargs["need_extended_hours_data"] is False
    assert len(bars) == 1
    assert bars[0].time == "2026-06-23T13:00:00Z"
    assert bars[0].open == 100.0
    assert bars[0].high == 102.0
    assert bars[0].low == 99.0
    assert bars[0].close == 101.5
    assert bars[0].volume == 300.0


@pytest.mark.asyncio
async def test_aget_stock_bars_batch_returns_dict_and_omits_failed_symbol() -> None:
    client = MagicMock()

    def fake_get_price_history(symbol: str, **kwargs: object) -> _FakeResponse:
        if symbol == "BAD":
            raise RuntimeError("boom")
        return _FakeResponse(_candles_payload())

    client.get_price_history.side_effect = fake_get_price_history
    provider = _provider(client)

    result = await provider.aget_stock_bars_batch(
        ["AAPL", "BAD", "MSFT"], timeframe="1Day", days=5
    )

    assert set(result.keys()) == {"AAPL", "MSFT"}
    assert len(result["AAPL"]) == 2
    assert len(result["MSFT"]) == 2
    assert "BAD" not in result


def test_schwab_market_data_satisfies_market_data_provider() -> None:
    assert isinstance(_provider(), MarketDataProvider)


def test_schwab_market_data_has_no_order_methods() -> None:
    forbidden = {
        "place_order",
        "aplace_order",
        "submit",
        "submit_order",
        "cancel_order",
        "acancel_order",
        "get_open_orders",
        "aget_open_orders",
    }
    public_callables = {
        name
        for name in dir(SchwabMarketData)
        if callable(getattr(SchwabMarketData, name, None))
        and not name.startswith("_")
    }
    assert forbidden.isdisjoint(public_callables)
    assert not any("order" in name.lower() for name in public_callables)


def test_get_market_data_provider_defaults_to_alpaca(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("MARKET_DATA_PROVIDER", raising=False)
    alpaca = SimpleNamespace(name="alpaca")

    monkeypatch.setattr(
        "brokers.factory.broker_is_configured", lambda name: name == "alpaca"
    )
    monkeypatch.setattr(
        "brokers.factory.AlpacaAdapter.from_settings", lambda: alpaca
    )

    provider = get_market_data_provider()
    assert provider is alpaca


def test_alpaca_adapter_satisfies_market_data_provider() -> None:
    adapter = AlpacaAdapter(
        base_url="https://paper-api.alpaca.markets",
        key_id="test-key",
        secret="test-secret",
    )
    assert isinstance(adapter, MarketDataProvider)


def test_get_market_data_provider_schwab(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    schwab = SchwabMarketData(MagicMock())
    monkeypatch.setenv("MARKET_DATA_PROVIDER", "schwab")
    monkeypatch.setattr(
        "brokers.factory.broker_is_configured", lambda name: name == "schwab"
    )
    monkeypatch.setattr(
        "brokers.factory.SchwabMarketData.from_settings", lambda: schwab
    )

    provider = get_market_data_provider()
    assert provider is schwab


@pytest.mark.asyncio
async def test_aget_stock_bars_uses_days_window() -> None:
    provider = _provider()
    captured: dict[str, object] = {}

    async def fake_range(
        symbol: str,
        *,
        timeframe: str,
        start: datetime,
        end: datetime,
        extended_hours: bool = False,
    ) -> list:
        captured["symbol"] = symbol
        captured["timeframe"] = timeframe
        captured["start"] = start
        captured["end"] = end
        return []

    provider.aget_stock_bars_range = fake_range  # type: ignore[method-assign]

    await provider.aget_stock_bars("spy", timeframe="1Day", days=10)

    assert captured["symbol"] == "spy"
    assert captured["timeframe"] == "1Day"
    assert (captured["end"] - captured["start"]) == timedelta(days=10)


# ---------------------------------------------------------------------------
# aget_quotes tests
# ---------------------------------------------------------------------------

_QUOTES_PAYLOAD = {
    "SPY": {
        # quote.lastPrice = live consolidated last (post-market 593.1); the regular block
        # holds the 16:00 close (592.5); the `extended` block is STALE (590.0) and must be
        # ignored in favour of quote.lastPrice.
        "quote": {
            "lastPrice": 593.1,
            "closePrice": 589.0,
            "netPercentChangeInDouble": 0.59,
            "totalVolume": 85_432_100,
        },
        "regular": {
            "regularMarketLastPrice": 592.5,
            "regularMarketPercentChange": 0.59,
        },
        "extended": {
            "lastPrice": 590.0,
            "mark": 590.0,
        },
    },
    "AAPL": {
        "quote": {"lastPrice": 211.0, "closePrice": 210.0},
        "regular": {},
        "extended": {},
    },
}


@pytest.mark.asyncio
async def test_aget_quotes_parses_regular_and_extended_fields() -> None:
    client = MagicMock()
    client.get_quotes.return_value = _FakeResponse(_QUOTES_PAYLOAD)
    provider = _provider(client)

    result = await provider.aget_quotes(["SPY", "AAPL"])

    assert set(result.keys()) == {"SPY", "AAPL"}

    spy = result["SPY"]
    assert spy["regular_last"] == 592.5  # regular block's 16:00 close
    assert spy["regular_change_pct"] == 0.59
    assert spy["ext_last"] == 593.1  # quote.lastPrice, NOT the stale extended block (590.0)
    assert spy["prev_close"] == 589.0
    # ext_change_pct: no postMarketPercentChange in this fixture, so it's computed vs
    # today's regular close (regular_last) — correct for pre/post/overnight.
    expected_ext_pct = round((593.1 - 592.5) / 592.5 * 100, 2)
    assert spy["ext_change_pct"] == pytest.approx(expected_ext_pct, abs=0.01)

    # total_volume is parsed from quote.totalVolume
    assert spy["total_volume"] == 85_432_100.0

    aapl = result["AAPL"]
    # empty regular/extended blocks → both reg_last and ext_last fall back to quote.lastPrice
    # (the consolidated last trade; ext shows flat-at-last when there's no extended print).
    assert aapl["regular_last"] == 211.0
    assert aapl["ext_last"] == 211.0
    assert aapl["ext_change_pct"] == 0.0
    assert aapl["prev_close"] == 210.0
    # AAPL fixture has no totalVolume → None
    assert aapl["total_volume"] is None


@pytest.mark.asyncio
async def test_aget_quotes_ignores_stale_extended_block() -> None:
    """Regression (TSLA post-market): the `extended` block held a ~15h-old pre-market print
    (371.09) while quote.lastPrice (377.93) was the live post-market last. Freshest-by-tradeTime
    picks the live quote block."""
    payload = {
        "TSLA": {
            "quote": {
                "lastPrice": 377.93,
                "tradeTime": 2_000_000_000_000,  # fresh
                "closePrice": 375.12,
                "postMarketPercentChange": -0.4687788,
            },
            "regular": {"regularMarketLastPrice": 379.71},
            "extended": {"lastPrice": 371.09, "tradeTime": 1_999_946_000_000},  # ~15h older
        }
    }
    client = MagicMock()
    client.get_quotes.return_value = _FakeResponse(payload)
    provider = _provider(client)

    tsla = (await provider.aget_quotes(["TSLA"]))["TSLA"]

    assert tsla["ext_last"] == 377.93  # fresh quote block, not the stale extended 371.09
    assert tsla["regular_last"] == 379.71
    assert tsla["ext_change_pct"] == -0.47  # (377.93-379.71)/379.71, agrees with ext_last


@pytest.mark.asyncio
async def test_aget_quotes_picks_fresh_extended_overnight() -> None:
    """Regression (SPY Sunday overnight): the consolidated `quote` block is FROZEN at Friday's
    last trade (731.20) while the `extended` block is live (733.3). Freshest-by-tradeTime must
    pick the live extended price — the inverse of the TSLA post-market case."""
    payload = {
        "SPY": {
            "quote": {
                "lastPrice": 731.20,
                "tradeTime": 1_999_000_000_000,  # Friday, stale
                "closePrice": 728.99,
            },
            "regular": {"regularMarketLastPrice": 728.99},
            # live now — a real overnight session carries a bid/ask + volume
            "extended": {
                "lastPrice": 733.3,
                "tradeTime": 2_000_000_000_000,
                "bidPrice": 733.2,
                "askPrice": 733.4,
                "totalVolume": 1_500_000,
            },
        }
    }
    client = MagicMock()
    client.get_quotes.return_value = _FakeResponse(payload)
    spy = (await _provider(client).aget_quotes(["SPY"]))["SPY"]

    assert spy["ext_last"] == 733.3  # live extended, not the frozen quote-block 731.20
    assert spy["ext_change_pct"] == round((733.3 - 728.99) / 728.99 * 100, 2)  # agrees w/ price


@pytest.mark.asyncio
async def test_aget_quotes_ignores_empty_extended_with_recent_tradetime() -> None:
    """Regression (SPY pre-market phantom price): Schwab left a STALE `extended` block —
    bid/ask/volume all 0 — carrying a tradeTime MORE recent than the genuinely-traded
    `quote` block. Freshest-by-tradeTime alone would pick the stale 720 and fire phantom
    price alerts (real last was ~737). Liveness must exclude the empty block."""
    payload = {
        "SPY": {
            "quote": {
                "lastPrice": 737.35,
                "tradeTime": 2_000_000_000_000,  # older than the stale extended below
                "closePrice": 728.99,
                "bidPrice": 737.33,
                "askPrice": 737.35,
                "totalVolume": 639_969,
            },
            "regular": {"regularMarketLastPrice": 728.99},
            # stale placeholder: a recent tradeTime but NO market (bid/ask/volume all 0)
            "extended": {
                "lastPrice": 720.0,
                "tradeTime": 2_000_000_500_000,  # newer than quote — would win by time alone
                "bidPrice": 0.0,
                "askPrice": 0.0,
                "totalVolume": 0,
            },
        }
    }
    client = MagicMock()
    client.get_quotes.return_value = _FakeResponse(payload)
    spy = (await _provider(client).aget_quotes(["SPY"]))["SPY"]

    assert spy["ext_last"] == 737.35  # live quote, NOT the empty extended's stale 720


@pytest.mark.asyncio
async def test_aget_quotes_empty_list_returns_empty_dict() -> None:
    provider = _provider()
    result = await provider.aget_quotes([])
    assert result == {}


@pytest.mark.asyncio
async def test_aget_quotes_raises_broker_error_on_http_failure() -> None:
    client = MagicMock()
    resp = MagicMock()
    resp.raise_for_status.side_effect = RuntimeError("403 Forbidden")
    client.get_quotes.return_value = resp
    provider = _provider(client)

    with pytest.raises(BrokerError, match="get_quotes failed"):
        await provider.aget_quotes(["SPY"])


# ---------------------------------------------------------------------------
# fundamental block parsing tests
# ---------------------------------------------------------------------------

# lastEarningsDate epoch-ms → 2026-04-25 UTC
_LAST_EARNINGS_EPOCH_MS = 1_777_075_200_000  # 2026-04-25T00:00:00Z

_QUOTES_WITH_FUNDAMENTAL = {
    "AAPL": {
        "quote": {"lastPrice": 211.0, "closePrice": 210.0, "totalVolume": 50_000_000},
        "regular": {"regularMarketLastPrice": 211.0, "regularMarketPercentChange": 0.5},
        "extended": {},
        "fundamental": {
            "peRatio": 28.5,
            "eps": 7.42,
            "divYield": 0.52,
            "sharesOutstanding": 15_200_000_000,
            "lastEarningsDate": _LAST_EARNINGS_EPOCH_MS,
        },
    },
    "ETF": {
        # ETF-like: no fundamental block at all → all None
        "quote": {"lastPrice": 500.0, "closePrice": 498.0},
        "regular": {},
        "extended": {},
    },
}


@pytest.mark.asyncio
async def test_aget_quotes_parses_fundamental_fields() -> None:
    client = MagicMock()
    client.get_quotes.return_value = _FakeResponse(_QUOTES_WITH_FUNDAMENTAL)
    provider = _provider(client)

    result = await provider.aget_quotes(["AAPL", "ETF"])

    aapl = result["AAPL"]
    assert aapl["pe_ratio"] == pytest.approx(28.5)
    assert aapl["eps"] == pytest.approx(7.42)
    assert aapl["div_yield"] == pytest.approx(0.52)
    assert aapl["shares_outstanding"] == pytest.approx(15_200_000_000)
    assert aapl["last_earnings_date"] == "2026-04-25"

    etf = result["ETF"]
    assert etf["pe_ratio"] is None
    assert etf["eps"] is None
    assert etf["div_yield"] is None
    assert etf["shares_outstanding"] is None
    assert etf["last_earnings_date"] is None


def test_market_cap_computation_from_shares_and_price() -> None:
    """market_cap = shares_outstanding × price (tested as pure arithmetic here)."""
    shares = 15_200_000_000.0
    price = 211.0
    market_cap = round(shares * price, 2)
    # ~$3.207T — just verify the formula
    assert market_cap == pytest.approx(3_207_200_000_000.0, rel=1e-6)
    # And None propagates cleanly when shares_outstanding is absent
    shares_none = None
    mc = round(float(shares_none) * price, 2) if shares_none is not None else None
    assert mc is None


def test_parse_candles_dedupes_duplicate_timestamps():
    """Schwab can return duplicate candles for the same minute (extended hours across
    day boundaries); _parse_candles must collapse to one bar per timestamp so charts
    (unique ascending times) and RVOL (no double-counted volume) stay correct."""
    from brokers.schwab_market_data import SchwabMarketData

    ts = 1_750_000_000_000  # epoch ms
    payload = {
        "candles": [
            {"datetime": ts, "open": 1, "high": 2, "low": 0.5, "close": 1.5, "volume": 100},
            {"datetime": ts + 300_000, "open": 1.5, "high": 3, "low": 1, "close": 2, "volume": 200},
            # duplicate of the first timestamp (later/authoritative wins)
            {"datetime": ts, "open": 1.1, "high": 2.2, "low": 0.6, "close": 1.6, "volume": 150},
        ]
    }
    bars = SchwabMarketData._parse_candles(payload)
    times = [b.time for b in bars]
    assert len(times) == len(set(times)) == 2  # deduped
    assert times == sorted(times)  # ascending
    assert bars[0].close == 1.6  # last-wins on the duplicate
