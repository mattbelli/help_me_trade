"""Alpaca market data feed resolution tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from brokers.alpaca import AlpacaAdapter
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
    yield
    clear_settings_cache()


def _adapter(*, data_feed: str = "iex") -> AlpacaAdapter:
    return AlpacaAdapter(
        base_url="https://paper-api.alpaca.markets",
        key_id="test-key",
        secret="test-secret",
        data_feed=data_feed,
    )


def test_normalize_alpaca_data_feed_defaults_to_iex() -> None:
    assert normalize_alpaca_data_feed(None) == "iex"
    assert normalize_alpaca_data_feed("") == "iex"
    assert normalize_alpaca_data_feed("invalid") == "iex"
    assert normalize_alpaca_data_feed("sip") == "sip"


def test_alpaca_config_defaults_to_iex() -> None:
    assert alpaca_config()["data_feed"] == "iex"


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
    assert alpaca_market_ws_url() == "wss://stream.data.alpaca.markets/v2/iex"

    monkeypatch.setenv("APCA_DATA_FEED", "sip")
    clear_settings_cache()

    assert alpaca_market_ws_url() == "wss://stream.data.alpaca.markets/v2/sip"
    assert (
        alpaca_market_ws_url("sip")
        == "wss://stream.data.alpaca.markets/v2/sip"
    )
