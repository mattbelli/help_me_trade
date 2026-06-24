"""Tests for research.intraday_profile — synthetic, deterministic, no network."""

from __future__ import annotations

import copy
from datetime import date

import numpy as np
import pytest

from research.intraday_bars import IntradayBar
from research.intraday_profile import (
    is_rth,
    prior_session_reference,
    reference_features,
    segment_rth_sessions,
    session_profile,
    to_ny,
)


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
    """Build a bar at a given NY local RTH time in June (EDT, UTC-4)."""
    utc_hour = ny_hour + 4
    ts = (
        f"{ny_date.isoformat()}T{utc_hour:02d}:{ny_minute:02d}:00+00:00"
    )
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


def test_rth_boundary_and_dst_june() -> None:
    # June = EDT (UTC-4): 09:30 EDT = 13:30 UTC
    assert is_rth("2024-06-03T13:30:00+00:00") is True
    assert is_rth("2024-06-03T13:29:00+00:00") is False
    # 16:00 EDT = 20:00 UTC — half-open, excluded
    assert is_rth("2024-06-03T20:00:00+00:00") is False
    # 15:59 EDT = 19:59 UTC — still RTH
    assert is_rth("2024-06-03T19:59:00+00:00") is True

    ny = to_ny("2024-06-03T13:30:00+00:00")
    assert ny.date() == date(2024, 6, 3)
    assert ny.hour == 9 and ny.minute == 30


def test_segment_rth_sessions_filters_and_orders() -> None:
    day1 = date(2024, 6, 3)
    day2 = date(2024, 6, 4)
    bars = [
        _bar("2024-06-03T08:00:00+00:00"),  # overnight, not RTH
        _rth_bar(day1, 9, 31, price=101.0),
        _rth_bar(day1, 9, 30, price=100.0),
        _bar("2024-06-03T20:00:00+00:00"),  # 16:00 EDT, not RTH
        _rth_bar(day2, 10, 0, price=200.0),
        _rth_bar(day2, 9, 30, price=199.0),
        _bar("2024-06-04T08:00:00+00:00"),  # overnight
    ]
    sessions = segment_rth_sessions(bars)

    assert list(sessions.keys()) == [day1, day2]
    assert len(sessions[day1]) == 2
    assert sessions[day1][0]["c"] == 100.0
    assert sessions[day1][1]["c"] == 101.0
    assert len(sessions[day2]) == 2
    assert sessions[day2][0]["c"] == 199.0
    assert sessions[day2][1]["c"] == 200.0


def test_session_profile_poc_and_ohlc() -> None:
    day = date(2024, 6, 3)
    bars = (
        _flat_session(day, 80.0, 2, volume=500.0)
        + _flat_session(day, 100.0, 10, volume=10_000.0, start_minute=32)
        + _flat_session(day, 120.0, 2, volume=500.0, start_minute=42)
    )
    bars[0] = _bar(bars[0]["t"], o=99.0, h=101.0, low=98.0, c=100.0, v=bars[0]["v"])
    bars[-1] = _bar(bars[-1]["t"], o=119.0, h=122.0, low=118.0, c=121.0, v=bars[-1]["v"])

    profile = session_profile(bars, n_bins=50)

    assert 98.0 <= profile["poc_price"] <= 102.0
    assert profile["session_high"] == 122.0
    assert profile["session_low"] == 80.0
    assert profile["session_open"] == 99.0
    assert profile["session_close"] == 121.0
    assert profile["total_volume"] == pytest.approx(102_000.0)
    assert profile["n_bars"] == len(bars)


def test_prior_session_reference_no_look_ahead() -> None:
    day1 = date(2024, 6, 3)
    day2 = date(2024, 6, 4)
    day3 = date(2024, 6, 5)

    sessions = {
        day1: _flat_session(day1, 100.0, 5, volume=1000.0),
        day2: _flat_session(day2, 200.0, 5, volume=2000.0),
        day3: _flat_session(day3, 300.0, 5, volume=3000.0),
    }

    assert prior_session_reference(sessions, day1) is None

    ref3 = prior_session_reference(sessions, day3, n_bins=50)
    expected = session_profile(sessions[day2], n_bins=50)
    expected["ref_date"] = day2

    assert ref3 is not None
    assert ref3["ref_date"] == day2
    assert ref3["poc_price"] == pytest.approx(expected["poc_price"])
    assert ref3["session_close"] == pytest.approx(200.0)

    ref3_before = copy.deepcopy(ref3)
    sessions[day3] = _flat_session(day3, 999.0, 20, volume=99_999.0)
    ref3_after = prior_session_reference(sessions, day3, n_bins=50)
    assert ref3_after is not None
    assert ref3_after["ref_date"] == ref3_before["ref_date"]
    assert ref3_after["poc_price"] == pytest.approx(ref3_before["poc_price"])
    assert ref3_after["session_close"] == pytest.approx(ref3_before["session_close"])
    assert ref3_after["n_bars"] == ref3_before["n_bars"]
    np.testing.assert_array_equal(ref3_after["bin_volume"], ref3_before["bin_volume"])


def test_reference_features_below_and_inside_value_area() -> None:
    ref = {
        "poc_price": 100.0,
        "vah": 105.0,
        "val": 95.0,
        "lvn_prices": [90.0],
        "session_low": 90.0,
        "bin_edges": np.linspace(85.0, 110.0, 11),
        "bin_volume": np.array(
            [100.0, 50.0, 20.0, 10.0, 500.0, 10.0, 20.0, 50.0, 100.0, 100.0]
        ),
    }

    below = reference_features(90.0, ref)
    assert below["below_value"] is True
    assert below["above_value"] is False
    assert below["in_value_area"] is False
    assert below["dist_to_poc_pct"] == pytest.approx((90.0 - 100.0) / 90.0 * 100.0)
    assert below["value_position"] == pytest.approx(0.0)

    inside = reference_features(100.0, ref)
    assert inside["below_value"] is False
    assert inside["above_value"] is False
    assert inside["in_value_area"] is True
    assert inside["value_position"] == pytest.approx(0.5)
    assert 0.0 <= inside["value_position"] <= 1.0


def test_reference_features_guards() -> None:
    empty_profile = session_profile([])
    assert empty_profile["n_bars"] == 0
    assert empty_profile["session_high"] is None

    defaults = reference_features(100.0, None)
    assert defaults["dist_to_poc_pct"] is None
    assert defaults["below_value"] is False
    assert defaults["in_value_area"] is False
    assert defaults["at_lvn"] is False
    assert defaults["at_poor_low"] is False

    defaults_empty = reference_features(100.0, {})
    assert defaults_empty == defaults
