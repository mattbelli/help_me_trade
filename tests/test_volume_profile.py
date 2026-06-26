"""Tests for research.volume_profile (composite profile + point-in-time features)
and signals.volume_profile (intraday RVOL compute + store)."""

from __future__ import annotations

import copy
import os
import statistics
from datetime import datetime, timezone

import numpy as np
import pytest

from brokers.alpaca import StockBar
from research.volume_profile import build_volume_profile, compute_profile_features
from signals.indicators import Bar
from signals.volume_profile import (
    N_BUCKETS,
    Profile,
    compute_profile,
    load_profile,
    rvol_now,
    save_profile,
)
from trading.market_hours import _ET

# ---------------------------------------------------------------------------
# Helpers for research.volume_profile tests (Bar dicts with lowercase keys)
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Helpers for signals.volume_profile tests (StockBar objects)
# ---------------------------------------------------------------------------


def _rvol_bar(date_et: str, hhmm: tuple[int, int], vol: float) -> StockBar:
    et = datetime(*map(int, date_et.split("-")), hhmm[0], hhmm[1], tzinfo=_ET)
    utc = et.astimezone(timezone.utc)
    return StockBar(
        time=utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
        open=1,
        high=1,
        low=1,
        close=1,
        volume=vol,
    )


def _bucket_hhmm(bucket: int) -> tuple[int, int]:
    total_minutes = 9 * 60 + 30 + bucket * 5
    return total_minutes // 60, total_minutes % 60


def _full_day_bars(date_et: str, daily_total: float) -> list[StockBar]:
    vol_per_bucket = daily_total / N_BUCKETS
    return [_rvol_bar(date_et, _bucket_hhmm(b), vol_per_bucket) for b in range(N_BUCKETS)]


def _partial_day_bars(date_et: str, last_bucket: int, daily_total: float) -> list[StockBar]:
    vol_per_bucket = daily_total / (last_bucket + 1)
    return [_rvol_bar(date_et, _bucket_hhmm(b), vol_per_bucket) for b in range(last_bucket + 1)]


# ---------------------------------------------------------------------------
# research.volume_profile tests
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# signals.volume_profile tests
# ---------------------------------------------------------------------------


def test_compute_profile_happy_path() -> None:
    daily_totals = [10_000 + i * 500 for i in range(10)]
    bars: list[StockBar] = []
    for i, total in enumerate(daily_totals):
        bars.extend(_full_day_bars(f"2026-06-{i + 1:02d}", total))

    profile = compute_profile("AAPL", bars, window=20, min_days=5)
    assert profile is not None
    assert profile.n_days == 10
    assert profile.median_daily_total == pytest.approx(statistics.median(daily_totals))
    assert len(profile.cumfrac) == N_BUCKETS
    assert profile.cumfrac[-1] == pytest.approx(1.0)
    assert all(
        profile.cumfrac[i] <= profile.cumfrac[i + 1] + 1e-12 for i in range(N_BUCKETS - 1)
    )


def test_compute_profile_excludes_half_day() -> None:
    bars: list[StockBar] = []
    for i in range(10):
        bars.extend(_full_day_bars(f"2026-06-{i + 1:02d}", 10_000))
    bars.extend(_partial_day_bars("2026-06-11", 41, 5_000))

    profile = compute_profile("AAPL", bars, window=20, min_days=5)
    assert profile is not None
    assert profile.n_days == 10


def test_compute_profile_too_few_days_returns_none() -> None:
    bars: list[StockBar] = []
    for i in range(3):
        bars.extend(_full_day_bars(f"2026-06-{i + 1:02d}", 10_000))

    assert compute_profile("AAPL", bars) is None


def test_rvol_now_warming() -> None:
    profile = Profile(
        symbol="AAPL",
        cumfrac=[(i + 1) / N_BUCKETS for i in range(N_BUCKETS)],
        median_daily_total=78_000,
        n_days=10,
    )
    today_bars = [
        _rvol_bar("2026-06-23", (9, 30), 1000),
        _rvol_bar("2026-06-23", (9, 35), 1000),
    ]
    result = rvol_now(profile, today_bars)
    assert result is not None
    assert result.value is None
    assert result.state == "warming"


def test_rvol_now_live_expected_pace() -> None:
    daily_total = 78_000.0
    profile = Profile(
        symbol="AAPL",
        cumfrac=[(i + 1) / N_BUCKETS for i in range(N_BUCKETS)],
        median_daily_total=daily_total,
        n_days=10,
    )
    vol_per_bucket = daily_total / N_BUCKETS
    today_bars = [_rvol_bar("2026-06-23", _bucket_hhmm(b), vol_per_bucket) for b in range(41)]

    result = rvol_now(profile, today_bars)
    assert result is not None
    assert result.state == "live"
    assert result.value == pytest.approx(1.0, rel=0.05)

    doubled = [_rvol_bar("2026-06-23", _bucket_hhmm(b), vol_per_bucket * 2) for b in range(41)]
    result2 = rvol_now(profile, doubled)
    assert result2 is not None
    assert result2.value == pytest.approx(2.0, rel=0.05)


def test_rvol_now_settled() -> None:
    daily_total = 50_000.0
    profile = Profile(
        symbol="AAPL",
        cumfrac=[(i + 1) / N_BUCKETS for i in range(N_BUCKETS)],
        median_daily_total=daily_total,
        n_days=10,
    )
    today_total = 75_000.0
    today_bars = _full_day_bars("2026-06-23", today_total)

    result = rvol_now(profile, today_bars)
    assert result is not None
    assert result.state == "settled"
    assert result.value == pytest.approx(round(today_total / daily_total, 2))


def test_profile_store_round_trip_and_staleness(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("VOLUME_PROFILE_DIR", str(tmp_path))
    profile = Profile(
        symbol="AAPL",
        cumfrac=[(i + 1) / N_BUCKETS for i in range(N_BUCKETS)],
        median_daily_total=78_000,
        n_days=10,
    )
    save_profile(profile)
    loaded = load_profile("AAPL")
    assert loaded == profile

    filepath = tmp_path / "AAPL.json"
    old_mtime = os.path.getmtime(filepath) - (8 * 86400)
    os.utime(filepath, (old_mtime, old_mtime))
    assert load_profile("AAPL") is None
