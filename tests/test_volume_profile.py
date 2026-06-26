"""Unit tests for signals.volume_profile — intraday RVOL compute + store."""

from __future__ import annotations

import os
import statistics
from datetime import datetime, timezone

import pytest

from brokers.alpaca import StockBar
from signals.volume_profile import (
    N_BUCKETS,
    Profile,
    compute_profile,
    load_profile,
    rvol_now,
    save_profile,
)
from trading.market_hours import _ET


def _bar(date_et: str, hhmm: tuple[int, int], vol: float) -> StockBar:
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
    return [_bar(date_et, _bucket_hhmm(b), vol_per_bucket) for b in range(N_BUCKETS)]


def _partial_day_bars(date_et: str, last_bucket: int, daily_total: float) -> list[StockBar]:
    vol_per_bucket = daily_total / (last_bucket + 1)
    return [_bar(date_et, _bucket_hhmm(b), vol_per_bucket) for b in range(last_bucket + 1)]


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
        _bar("2026-06-23", (9, 30), 1000),
        _bar("2026-06-23", (9, 35), 1000),
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
    today_bars = [_bar("2026-06-23", _bucket_hhmm(b), vol_per_bucket) for b in range(41)]

    result = rvol_now(profile, today_bars)
    assert result is not None
    assert result.state == "live"
    assert result.value == pytest.approx(1.0, rel=0.05)

    doubled = [_bar("2026-06-23", _bucket_hhmm(b), vol_per_bucket * 2) for b in range(41)]
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
