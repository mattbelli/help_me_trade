"""Alpaca market data feed resolution tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from brokers.alpaca import AlpacaAdapter
from brokers.alpaca_tier import clear_alpaca_tier_cache
from brokers.market_stream import alpaca_market_ws_url
from config.settings import alpaca_config, clear_settings_cache, normalize_alpaca_data_feed


@pytest.fixture(autouse=True)
def _reset_settings_cache(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Isolate from the dev's real data/settings.json (whose alpaca block would
    # otherwise shadow the APCA_DATA_FEED env override).
    monkeypatch.delenv("APCA_DATA_FEED", raising=False)
    monkeypatch.setattr("config.settings.SETTINGS_PATH", tmp_path / "settings.json")
    clear_settings_cache()
    clear_alpaca_tier_cache()
    yield
    clear_settings_cache()
    clear_alpaca_tier_cache()


def _adapter(*, data_feed: str = "iex") -> AlpacaAdapter:
    return AlpacaAdapter(
        base_url="https://paper-api.alpaca.markets",
        key_id="test-key",
        secret="test-secret",
        data_feed=data_feed,
    )


def test_normalize_alpaca_data_feed_defaults_to_auto() -> None:
    assert normalize_alpaca_data_feed(None) == "auto"
    assert normalize_alpaca_data_feed("") == "auto"
    assert normalize_alpaca_data_feed("invalid") == "auto"
    assert normalize_alpaca_data_feed("sip") == "sip"
    assert normalize_alpaca_data_feed("auto") == "auto"


def test_alpaca_config_defaults_to_auto() -> None:
    assert alpaca_config()["data_feed"] == "auto"


def test_alpaca_config_reads_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APCA_DATA_FEED", "sip")
    clear_settings_cache()

    assert alpaca_config()["data_feed"] == "sip"


def test_bar_params_use_resolved_feed() -> None:
    adapter = _adapter(data_feed="sip")

    _, batch_params = adapter._batch_bar_params(["AAPL"], timeframe="5Min", days=30)
    assert batch_params["feed"] == "sip"

    start = datetime.now(UTC) - timedelta(days=10)
    end = datetime.now(UTC)
    range_params = {
        "timeframe": "1Min",
        "start": start.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "end": end.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "limit": 10000,
        "feed": adapter._data_feed,
        "sort": "asc",
    }
    assert range_params["feed"] == "sip"


def test_market_stream_url_uses_resolved_feed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "brokers.market_stream.effective_alpaca_feed",
        lambda **kwargs: "iex",
    )
    assert alpaca_market_ws_url() == "wss://stream.data.alpaca.markets/v2/iex"

    monkeypatch.setenv("APCA_DATA_FEED", "sip")
    clear_settings_cache()

    assert alpaca_market_ws_url() == "wss://stream.data.alpaca.markets/v2/sip"
    assert (
        alpaca_market_ws_url("sip")
        == "wss://stream.data.alpaca.markets/v2/sip"
    )


def _raw_bar(time: str, close: float) -> dict[str, float | str]:
    return {
        "t": time,
        "o": close - 0.5,
        "h": close + 0.5,
        "l": close - 1.0,
        "c": close,
        "v": 100,
    }


def test_get_stock_bars_range_follows_next_page_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = _adapter()
    calls: list[dict[str, object]] = []

    def fake_get_data(_path: str, *, params: dict[str, object] | None = None) -> dict:
        calls.append(dict(params or {}))
        if len(calls) == 1:
            return {
                "bars": [_raw_bar("2025-01-01T10:00:00Z", 100.0)],
                "next_page_token": "tok",
            }
        return {
            "bars": [_raw_bar("2025-01-01T11:00:00Z", 101.0)],
            "next_page_token": None,
        }

    monkeypatch.setattr(adapter, "_get_data", fake_get_data)

    start = datetime(2025, 1, 1, tzinfo=UTC)
    end = datetime(2025, 1, 2, tzinfo=UTC)
    bars = adapter.get_stock_bars_range(
        "SPY", timeframe="1Hour", start=start, end=end
    )

    assert [bar.time for bar in bars] == [
        "2025-01-01T10:00:00Z",
        "2025-01-01T11:00:00Z",
    ]
    assert len(calls) == 2
    assert "page_token" not in calls[0]
    assert calls[1].get("page_token") == "tok"


@pytest.mark.asyncio
async def test_aget_stock_bars_range_follows_next_page_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = _adapter()
    calls: list[dict[str, object]] = []

    async def fake_aget_data(
        _path: str, *, params: dict[str, object] | None = None
    ) -> dict:
        calls.append(dict(params or {}))
        if len(calls) == 1:
            return {
                "bars": [_raw_bar("2025-01-01T10:00:00Z", 100.0)],
                "next_page_token": "tok",
            }
        return {
            "bars": [_raw_bar("2025-01-01T11:00:00Z", 101.0)],
            "next_page_token": None,
        }

    monkeypatch.setattr(adapter, "_aget_data", fake_aget_data)

    start = datetime(2025, 1, 1, tzinfo=UTC)
    end = datetime(2025, 1, 2, tzinfo=UTC)
    bars = await adapter.aget_stock_bars_range(
        "SPY", timeframe="1Hour", start=start, end=end
    )

    assert [bar.time for bar in bars] == [
        "2025-01-01T10:00:00Z",
        "2025-01-01T11:00:00Z",
    ]
    assert len(calls) == 2
    assert "page_token" not in calls[0]
    assert calls[1].get("page_token") == "tok"


def test_get_stock_bars_batch_merges_pages_per_symbol(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = _adapter()
    calls: list[dict[str, object]] = []

    def fake_get_data(_path: str, *, params: dict[str, object] | None = None) -> dict:
        calls.append(dict(params or {}))
        if len(calls) == 1:
            return {
                "bars": {
                    "AAPL": [_raw_bar("2025-01-01T10:00:00Z", 200.0)],
                    "MSFT": [_raw_bar("2025-01-01T10:00:00Z", 400.0)],
                },
                "next_page_token": "tok",
            }
        return {
            "bars": {
                "AAPL": [_raw_bar("2025-01-01T11:00:00Z", 201.0)],
                "MSFT": [],
            },
            "next_page_token": None,
        }

    monkeypatch.setattr(adapter, "_get_data", fake_get_data)

    result = adapter.get_stock_bars_batch(
        ["AAPL", "MSFT"], timeframe="1Hour", days=30
    )

    assert [bar.time for bar in result["AAPL"]] == [
        "2025-01-01T10:00:00Z",
        "2025-01-01T11:00:00Z",
    ]
    assert [bar.time for bar in result["MSFT"]] == ["2025-01-01T10:00:00Z"]
    assert len(calls) == 2
    assert "page_token" not in calls[0]
    assert calls[1].get("page_token") == "tok"


@pytest.mark.asyncio
async def test_aget_stock_bars_batch_merges_pages_per_symbol(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = _adapter()
    calls: list[dict[str, object]] = []

    async def fake_aget_data(
        _path: str, *, params: dict[str, object] | None = None
    ) -> dict:
        calls.append(dict(params or {}))
        if len(calls) == 1:
            return {
                "bars": {
                    "AAPL": [_raw_bar("2025-01-01T10:00:00Z", 200.0)],
                    "MSFT": [_raw_bar("2025-01-01T10:00:00Z", 400.0)],
                },
                "next_page_token": "tok",
            }
        return {
            "bars": {
                "AAPL": [_raw_bar("2025-01-01T11:00:00Z", 201.0)],
                "MSFT": [],
            },
            "next_page_token": None,
        }

    monkeypatch.setattr(adapter, "_aget_data", fake_aget_data)

    result = await adapter.aget_stock_bars_batch(
        ["AAPL", "MSFT"], timeframe="1Hour", days=30
    )

    assert [bar.time for bar in result["AAPL"]] == [
        "2025-01-01T10:00:00Z",
        "2025-01-01T11:00:00Z",
    ]
    assert [bar.time for bar in result["MSFT"]] == ["2025-01-01T10:00:00Z"]
    assert len(calls) == 2
    assert "page_token" not in calls[0]
    assert calls[1].get("page_token") == "tok"


@pytest.mark.asyncio
async def test_stock_bar_requests_ask_for_split_adjustment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """History spanning a split must not show the raw pre-split price scale
    (CRWD 4:1 2026-07-02 drew an $772->$191 cliff on every chart)."""
    adapter = _adapter()
    calls: list[dict[str, object]] = []

    def fake_get_data(_path: str, *, params: dict[str, object] | None = None) -> dict:
        calls.append(dict(params or {}))
        return {"bars": [], "next_page_token": None}

    async def fake_aget_data(
        _path: str, *, params: dict[str, object] | None = None
    ) -> dict:
        calls.append(dict(params or {}))
        return {"bars": [], "next_page_token": None}

    monkeypatch.setattr(adapter, "_get_data", fake_get_data)
    monkeypatch.setattr(adapter, "_aget_data", fake_aget_data)

    start = datetime(2025, 1, 1, tzinfo=UTC)
    end = datetime(2025, 1, 2, tzinfo=UTC)
    adapter.get_stock_bars_range("CRWD", timeframe="1Day", start=start, end=end)
    await adapter.aget_stock_bars_range(
        "CRWD", timeframe="1Day", start=start, end=end
    )
    await adapter.aget_stock_bars_batch(["CRWD"], timeframe="1Day", days=5)

    assert len(calls) == 3
    assert all(c.get("adjustment") == "split" for c in calls)
