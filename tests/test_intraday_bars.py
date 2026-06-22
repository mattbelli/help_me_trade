"""Tests for research.intraday_bars — deterministic, no network."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from research.intraday_bars import (
    IntradayBar,
    _candles_to_bars,
    _fetch_schwab,
    _split_time_windows,
    cache_minute_bars,
    cached_minute_symbols,
    get_minute_bars,
)


def _bar(ts: str, close: float = 100.0) -> IntradayBar:
    return {"t": ts, "o": close, "h": close, "l": close, "c": close, "v": 1000.0}


def test_roundtrip_cache_and_load(tmp_path: Path) -> None:
    db_path = tmp_path / "intraday.db"
    bars = [
        _bar("2026-05-18T13:30:00+00:00", 100.0),
        _bar("2026-05-18T13:31:00+00:00", 101.0),
        _bar("2026-05-18T13:32:00+00:00", 102.0),
    ]
    cache_minute_bars("/ES", bars, source="schwab", db_path=db_path)

    start = datetime(2026, 5, 18, 13, 30, tzinfo=UTC)
    end = datetime(2026, 5, 18, 13, 32, tzinfo=UTC)
    loaded = get_minute_bars("/ES", start, end, source="schwab", db_path=db_path)

    assert loaded == bars
    assert [bar["t"] for bar in loaded] == sorted(bar["t"] for bar in loaded)


def test_range_filter_inclusive(tmp_path: Path) -> None:
    db_path = tmp_path / "intraday.db"
    bars = [
        _bar("2026-05-18T13:30:00+00:00"),
        _bar("2026-05-18T13:31:00+00:00"),
        _bar("2026-05-18T13:32:00+00:00"),
        _bar("2026-05-18T13:33:00+00:00"),
    ]
    cache_minute_bars("/ES", bars, source="schwab", db_path=db_path)

    start = datetime(2026, 5, 18, 13, 31, tzinfo=UTC)
    end = datetime(2026, 5, 18, 13, 32, tzinfo=UTC)
    loaded = get_minute_bars("/ES", start, end, source="schwab", db_path=db_path)

    assert len(loaded) == 2
    assert loaded[0]["t"] == "2026-05-18T13:31:00+00:00"
    assert loaded[1]["t"] == "2026-05-18T13:32:00+00:00"


def test_upsert_replaces_same_key(tmp_path: Path) -> None:
    db_path = tmp_path / "intraday.db"
    ts = "2026-05-18T13:30:00+00:00"
    cache_minute_bars("/ES", [_bar(ts, close=100.0)], source="schwab", db_path=db_path)
    cache_minute_bars("/ES", [_bar(ts, close=200.0)], source="schwab", db_path=db_path)

    start = datetime(2026, 5, 18, 13, 30, tzinfo=UTC)
    end = datetime(2026, 5, 18, 13, 30, tzinfo=UTC)
    loaded = get_minute_bars("/ES", start, end, source="schwab", db_path=db_path)

    assert len(loaded) == 1
    assert loaded[0]["c"] == 200.0


def test_candles_to_bars_conversion() -> None:
    payload = {
        "candles": [
            {
                "datetime": 1747575000000,
                "open": 1,
                "high": 2,
                "low": 0.5,
                "close": 1.5,
                "volume": 100,
            }
        ]
    }
    bars = _candles_to_bars(payload)
    assert len(bars) == 1
    assert bars[0]["t"] == "2025-05-18T13:30:00+00:00"
    assert bars[0]["o"] == 1.0
    assert bars[0]["h"] == 2.0
    assert bars[0]["l"] == 0.5
    assert bars[0]["c"] == 1.5
    assert bars[0]["v"] == 100.0


def test_chunking_splits_and_dedupes(monkeypatch: pytest.MonkeyPatch) -> None:
    recorded: list[tuple[datetime, datetime]] = []

    def fake_window(symbol: str, start: datetime, end: datetime) -> list[IntradayBar]:
        recorded.append((start, end))
        overlap_ts = start.isoformat()
        return [
            _bar(overlap_ts, close=1.0),
            _bar((start + timedelta(minutes=1)).isoformat(), close=2.0),
        ]

    monkeypatch.setattr("research.intraday_bars._fetch_schwab_window", fake_window)

    start = datetime(2026, 1, 1, tzinfo=UTC)
    end = datetime(2026, 3, 15, tzinfo=UTC)
    windows = _split_time_windows(start, end)
    assert all((w_end - w_start) <= timedelta(days=30) for w_start, w_end in windows)
    assert len(windows) >= 3

    bars = _fetch_schwab("/ES", start, end)
    assert len(recorded) == len(windows)
    assert all((w_end - w_start) <= timedelta(days=30) for w_start, w_end in recorded)

    ts_values = [bar["t"] for bar in bars]
    assert ts_values == sorted(ts_values)
    assert len(ts_values) == len(set(ts_values))


def test_no_auto_fetch_for_non_schwab_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = tmp_path / "intraday.db"
    called = False

    def boom(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("Schwab fetch should not be called")

    monkeypatch.setattr("research.intraday_bars._fetch_schwab", boom)

    start = datetime(2026, 5, 18, tzinfo=UTC)
    end = datetime(2026, 5, 19, 23, 59, 59, tzinfo=UTC)
    loaded = get_minute_bars("/ES", start, end, source="norgate", db_path=db_path)

    assert loaded == []
    assert called is False


def test_symbol_preserved_verbatim(tmp_path: Path) -> None:
    db_path = tmp_path / "intraday.db"
    bars = [_bar("2026-05-18T13:30:00+00:00")]
    cache_minute_bars("/ES", bars, source="schwab", db_path=db_path)

    assert cached_minute_symbols(source="schwab", db_path=db_path) == ["/ES"]

    start = datetime(2026, 5, 18, 13, 30, tzinfo=UTC)
    end = datetime(2026, 5, 18, 13, 30, tzinfo=UTC)
    loaded = get_minute_bars("/ES", start, end, source="schwab", db_path=db_path)
    assert len(loaded) == 1
