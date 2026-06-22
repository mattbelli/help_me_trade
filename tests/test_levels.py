"""Tests for research.levels — point-in-time support/resistance features."""

from __future__ import annotations

import pytest

from research.levels import cluster_levels, level_features, swing_pivots
from signals.indicators import Bar


def _bar(
    *,
    h: float,
    low: float,
    c: float | None = None,
    o: float | None = None,
    v: float = 1000.0,
) -> Bar:
    close = c if c is not None else (h + low) / 2.0
    return {
        "o": o if o is not None else close,
        "h": h,
        "l": low,
        "c": close,
        "v": v,
    }


def _swing_series(*, trailing_high: float | None = None) -> list[Bar]:
    """Crafted series: two swing lows (~100) and one swing high (~120)."""
    bars = [
        _bar(h=112.0, low=110.0),
        _bar(h=108.0, low=106.0),
        _bar(h=103.0, low=100.0),
        _bar(h=108.0, low=104.0),
        _bar(h=112.0, low=108.0),
        _bar(h=114.0, low=112.0),
        _bar(h=120.0, low=116.0),
        _bar(h=114.0, low=110.0),
        _bar(h=110.0, low=106.0),
        _bar(h=106.0, low=102.0),
        _bar(h=103.0, low=100.0),
        _bar(h=106.0, low=102.0),
        _bar(h=109.0, low=105.0),
        _bar(h=112.0, low=108.0),
        _bar(h=115.0, low=111.0),
    ]
    if trailing_high is not None:
        bars.append(_bar(h=trailing_high, low=trailing_high - 2.0))
        bars.append(_bar(h=trailing_high - 1.0, low=trailing_high - 3.0))
    else:
        bars.extend(
            [
                _bar(h=118.0, low=114.0),
                _bar(h=121.0, low=117.0),
            ]
        )
    return bars


def test_swing_pivots_detects_handcrafted_levels() -> None:
    bars = _swing_series()
    idx = len(bars) - 1
    window = 2

    support, resistance = swing_pivots(bars, idx, window=window)

    assert support == pytest.approx([100.0, 100.0])
    assert resistance == pytest.approx([120.0])


def test_cluster_levels_merges_nearby_prices() -> None:
    levels = cluster_levels([99.5, 100.0, 100.4], tol_pct=1.0)

    assert len(levels) == 1
    assert levels[0] == pytest.approx(100.0, rel=1e-3)


def test_level_features_distance_and_range_position() -> None:
    bars = _swing_series()
    idx = len(bars) - 1
    bars[idx] = _bar(h=111.0, low=109.0, c=110.0)

    features = level_features(bars, idx, window=2, tol_pct=1.0)

    assert features["dist_to_support_pct"] == pytest.approx(9.090909, rel=1e-4)
    assert features["dist_to_resistance_pct"] == pytest.approx(9.090909, rel=1e-4)
    assert features["range_position"] == pytest.approx(0.5)
    assert features["n_support"] == 1
    assert features["n_resistance"] == 1


def test_near_support_flag() -> None:
    bars = _swing_series()
    idx = len(bars) - 1
    bars[idx] = _bar(h=101.5, low=99.5, c=100.5)

    features = level_features(bars, idx, window=2, tol_pct=1.0)

    assert features["near_support"] == 1
    assert features["dist_to_support_pct"] == pytest.approx(0.497512, rel=1e-4)


def test_no_look_ahead_excludes_recent_high() -> None:
    bars = _swing_series(trailing_high=200.0)
    idx = len(bars) - 1
    window = 2

    _, resistance = swing_pivots(bars, idx, window=window)

    assert resistance == pytest.approx([120.0])
    assert 200.0 not in resistance


def test_insufficient_history_returns_empty() -> None:
    bars = [_bar(h=101.0, low=99.0, c=100.0) for _ in range(4)]

    assert swing_pivots(bars, 3, window=2) == ([], [])
    assert level_features(bars, 3, window=2) == {}
