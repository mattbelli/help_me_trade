"""Tests for research.volume_profile — composite profile and point-in-time features."""

from __future__ import annotations

import copy

import numpy as np
import pytest

from research.volume_profile import build_volume_profile, compute_profile_features
from signals.indicators import Bar


def _bar(
    *,
    low: float,
    h: float | None = None,
    c: float | None = None,
    o: float | None = None,
    v: float = 1000.0,
) -> Bar:
    high = h if h is not None else low
    close = c if c is not None else (low + high) / 2.0
    return {
        "o": o if o is not None else close,
        "h": high,
        "l": low,
        "c": close,
        "v": v,
    }


def _flat_price_bars(
    price: float,
    count: int,
    *,
    volume: float = 1000.0,
) -> list[Bar]:
    return [_bar(low=price, h=price, c=price, v=volume) for _ in range(count)]


def test_poc_falls_in_high_volume_band() -> None:
    bars = (
        _flat_price_bars(80.0, 3, volume=500.0)
        + _flat_price_bars(100.0, 10, volume=10_000.0)
        + _flat_price_bars(120.0, 3, volume=500.0)
    )
    profile = build_volume_profile(bars, n_bins=50)

    assert 98.0 <= profile["poc_price"] <= 102.0


def test_value_area_holds_seventy_pct_and_orders_val_poc_vah() -> None:
    centers = np.linspace(90.0, 110.0, 21)
    weights = np.exp(-0.5 * ((centers - 100.0) / 4.0) ** 2)
    weights /= weights.sum()

    bars: list[Bar] = []
    for center, weight in zip(centers, weights, strict=True):
        vol = float(weight * 100_000.0)
        bars.append(_bar(low=center - 0.5, h=center + 0.5, c=center, v=vol))

    profile = build_volume_profile(bars, n_bins=100, value_area_pct=0.70)
    bin_volume = profile["bin_volume"]
    total = float(bin_volume.sum())

    val = profile["val"]
    vah = profile["vah"]
    poc = profile["poc_price"]
    assert val < poc < vah

    in_va = (profile["bin_centers"] >= val) & (profile["bin_centers"] <= vah)
    va_share = float(bin_volume[in_va].sum()) / total
    assert va_share >= 0.70


def test_bimodal_profile_finds_two_hvns_and_lvn_valley() -> None:
    bars = (
        _flat_price_bars(86.0, 20, volume=5_000.0)
        + _flat_price_bars(98.0, 1, volume=500.0)
        + _flat_price_bars(99.0, 1, volume=750.0)
        + _flat_price_bars(100.0, 1, volume=100.0)
        + _flat_price_bars(101.0, 1, volume=750.0)
        + _flat_price_bars(102.0, 1, volume=500.0)
        + _flat_price_bars(114.0, 20, volume=5_000.0)
    )
    profile = build_volume_profile(bars, n_bins=50)

    hvn = profile["hvn_prices"]
    lvn = profile["lvn_prices"]

    assert len(hvn) >= 2
    assert any(84.0 <= price <= 88.0 for price in hvn)
    assert any(112.0 <= price <= 116.0 for price in hvn)
    assert len(lvn) >= 1
    assert any(98.0 <= price <= 102.0 for price in lvn)


def test_compute_profile_features_no_lookahead() -> None:
    window = 20
    idx = 25
    base = [_bar(low=95.0 + i * 0.1, h=105.0 + i * 0.1, c=100.0 + i * 0.1, v=1000.0 + i) for i in range(40)]

    series_a = copy.deepcopy(base)
    series_b = copy.deepcopy(base)
    for j in range(idx + 1, len(series_b)):
        series_b[j] = _bar(low=50.0, h=200.0, c=150.0, v=999_999.0)

    feats_a = compute_profile_features(series_a, idx, window=window, n_bins=50)
    feats_b = compute_profile_features(series_b, idx, window=window, n_bins=50)

    assert feats_a == feats_b


def test_is_poor_low_true_on_thin_new_low() -> None:
    window = 10
    idx = window
    history = [_bar(low=100.0, h=102.0, c=101.0, v=5_000.0) for _ in range(window)]
    poke = _bar(low=95.0, h=96.0, c=95.5, v=50.0)
    bars = history + [poke]

    feats = compute_profile_features(bars, idx, window=window, n_bins=50)

    assert feats["is_poor_low"] is True


def test_is_poor_low_false_on_heavy_volume_new_low() -> None:
    window = 10
    idx = window
    history = [_bar(low=100.0, h=102.0, c=101.0, v=5_000.0) for _ in range(window)]
    poke = _bar(low=95.0, h=96.0, c=95.5, v=50_000.0)
    bars = history + [poke]

    feats = compute_profile_features(bars, idx, window=window, n_bins=50)

    assert feats["is_poor_low"] is False


def test_guards_idx_below_window() -> None:
    bars = _flat_price_bars(100.0, 5)
    feats = compute_profile_features(bars, 3, window=10)

    assert feats["poc"] is None
    assert feats["in_value_area"] is False
    assert feats["is_poor_low"] is False


def test_guards_single_bar_profile() -> None:
    bars = [_bar(low=100.0, h=102.0, c=101.0, v=1000.0)]
    profile = build_volume_profile(bars)

    assert profile["poc_price"] == pytest.approx(101.0)
    assert profile["hvn_prices"] == []
    assert profile["lvn_prices"] == []


def test_guards_zero_volume_profile() -> None:
    bars = [
        _bar(low=100.0, h=102.0, c=101.0, v=0.0),
        _bar(low=99.0, h=101.0, c=100.0, v=0.0),
    ]
    profile = build_volume_profile(bars)

    assert profile["poc_price"] == pytest.approx(100.0)
    assert profile["hvn_prices"] == []
    assert profile["lvn_prices"] == []


def test_feature_dict_keys() -> None:
    window = 5
    idx = window
    bars = [_bar(low=99.0, h=101.0, c=100.0, v=1000.0) for _ in range(window + 1)]
    feats = compute_profile_features(bars, idx, window=window, n_bins=20)

    assert set(feats) == {
        "poc",
        "vah",
        "val",
        "va_width_pct",
        "dist_to_poc_pct",
        "in_value_area",
        "value_position",
        "at_hvn_support",
        "at_lvn",
        "below_value",
        "is_poor_low",
    }
