"""Tests for research.databento_loader — the pure ohlcv-1m converter (no network)."""

from __future__ import annotations

import pandas as pd

from research.databento_loader import _records_to_bars


def _df(index, rows):
    return pd.DataFrame(rows, index=pd.DatetimeIndex(index))


def test_records_to_bars_tz_aware_utc() -> None:
    df = _df(
        pd.to_datetime(["2026-06-18T13:30:00Z", "2026-06-18T13:31:00Z"]),
        {
            "open": [7472.0, 7476.25],
            "high": [7475.5, 7480.0],
            "low": [7471.0, 7476.25],
            "close": [7475.5, 7479.75],
            "volume": [76, 41],
        },
    )
    bars = _records_to_bars(df)
    assert [b["t"] for b in bars] == [
        "2026-06-18T13:30:00+00:00",
        "2026-06-18T13:31:00+00:00",
    ]
    assert bars[0]["o"] == 7472.0 and bars[0]["c"] == 7475.5
    assert bars[1]["v"] == 41.0
    assert all(isinstance(b["o"], float) for b in bars)


def test_records_to_bars_naive_index_treated_as_utc() -> None:
    df = _df(
        pd.to_datetime(["2026-06-18T13:30:00"]),  # tz-naive
        {"open": [1], "high": [2], "low": [0.5], "close": [1.5], "volume": [100]},
    )
    bars = _records_to_bars(df)
    assert bars[0]["t"] == "2026-06-18T13:30:00+00:00"
    assert bars[0]["l"] == 0.5


def test_records_to_bars_non_utc_index_converted() -> None:
    df = _df(
        pd.to_datetime(["2026-06-18T09:30:00-04:00"]),  # ET -> 13:30 UTC
        {"open": [1], "high": [2], "low": [1], "close": [1.5], "volume": [10]},
    )
    bars = _records_to_bars(df)
    assert bars[0]["t"] == "2026-06-18T13:30:00+00:00"
