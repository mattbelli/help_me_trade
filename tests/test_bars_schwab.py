"""Tests for research.bars Schwab daily path — deterministic, no network."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from research.bars import (
    _daily_candles_to_bars,
    cache_bars,
    cached_symbols,
    get_daily_bars,
)


def test_daily_candles_to_bars_conversion() -> None:
    payload = {
        "candles": [
            {
                "datetime": 1747526400000,
                "open": 1,
                "high": 2,
                "low": 0.5,
                "close": 1.5,
                "volume": 99,
            }
        ]
    }
    bars = _daily_candles_to_bars(payload)
    assert len(bars) == 1
    assert bars[0]["d"] == "2025-05-18"
    assert bars[0]["o"] == 1.0
    assert bars[0]["h"] == 2.0
    assert bars[0]["l"] == 0.5
    assert bars[0]["c"] == 1.5
    assert bars[0]["v"] == 99.0


def test_daily_candles_dedupes_by_date() -> None:
    payload = {
        "candles": [
            {
                "datetime": 1747526400000,
                "open": 1,
                "high": 2,
                "low": 0.5,
                "close": 1.5,
                "volume": 99,
            },
            {
                "datetime": 1747526400000,
                "open": 10,
                "high": 20,
                "low": 5,
                "close": 15,
                "volume": 999,
            },
        ]
    }
    bars = _daily_candles_to_bars(payload)
    assert len(bars) == 1
    assert bars[0]["c"] == 15.0


def test_schwab_auto_fetch_caches_and_reuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = tmp_path / "bars.db"
    call_count = 0
    fetched_bars = [
        {"d": "2020-01-01", "o": 1.0, "h": 2.0, "l": 0.5, "c": 1.5, "v": 99.0},
        {"d": "2020-01-02", "o": 2.0, "h": 3.0, "l": 1.5, "c": 2.5, "v": 100.0},
    ]

    def fake_fetch(symbol: str, start: date, end: date) -> list:
        nonlocal call_count
        call_count += 1
        assert symbol == "/ES"
        return fetched_bars

    monkeypatch.setattr("research.bars._fetch_schwab_daily", fake_fetch)

    start = date(2020, 1, 1)
    end = date(2020, 1, 2)
    result = get_daily_bars("/ES", start, end, source="schwab", db_path=db_path)
    assert result == fetched_bars
    assert call_count == 1

    result2 = get_daily_bars("/ES", start, end, source="schwab", db_path=db_path)
    assert result2 == fetched_bars
    assert call_count == 1


def test_es_symbol_preserved_verbatim(tmp_path: Path) -> None:
    db_path = tmp_path / "bars.db"
    bars = [{"d": "2020-01-01", "o": 1.0, "h": 2.0, "l": 0.5, "c": 1.5, "v": 99.0}]
    cache_bars("/ES", bars, source="schwab", db_path=db_path)

    assert cached_symbols(source="schwab", db_path=db_path) == ["/ES"]

    loaded = get_daily_bars(
        "/ES", date(2020, 1, 1), date(2020, 1, 1), source="schwab", db_path=db_path
    )
    assert len(loaded) == 1
    assert loaded[0]["d"] == "2020-01-01"


def test_no_auto_fetch_for_non_fetchable_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = tmp_path / "bars.db"
    alpaca_called = False
    schwab_called = False

    def fake_alpaca(*args, **kwargs):
        nonlocal alpaca_called
        alpaca_called = True
        return []

    def fake_schwab(*args, **kwargs):
        nonlocal schwab_called
        schwab_called = True
        return []

    monkeypatch.setattr("research.bars._fetch_alpaca", fake_alpaca)
    monkeypatch.setattr("research.bars._fetch_schwab_daily", fake_schwab)

    loaded = get_daily_bars(
        "SPY", date(2020, 1, 1), date(2020, 1, 31), source="norgate", db_path=db_path
    )

    assert loaded == []
    assert alpaca_called is False
    assert schwab_called is False


def test_alpaca_auto_fetch_still_routes_to_fetch_alpaca(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = tmp_path / "bars.db"
    called = False

    def fake_alpaca(symbol: str, start: date, end: date) -> list:
        nonlocal called
        called = True
        assert symbol == "SPY"
        return [{"d": "2020-01-01", "o": 1.0, "h": 1.0, "l": 1.0, "c": 1.0, "v": 1.0}]

    monkeypatch.setattr("research.bars._fetch_alpaca", fake_alpaca)

    result = get_daily_bars(
        "SPY", date(2020, 1, 1), date(2020, 1, 1), source="alpaca", db_path=db_path
    )

    assert called is True
    assert len(result) == 1
    assert result[0]["d"] == "2020-01-01"
