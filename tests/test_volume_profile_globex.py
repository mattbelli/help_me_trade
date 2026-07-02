"""Unit tests for Globex (futures) RVOL in signals.volume_profile.

Covers:
- compute_profile(session="globex") buckets overnight bars into Globex buckets
- a midnight-straddling session is grouped as ONE day (not split by calendar date)
- rvol_now(session="globex") returns a sane multiple and counts the whole session
- Equity path (session="rth" / default) is unchanged for the same equity inputs
- save_profile / load_profile use distinct paths for futures vs equity symbols

Globex bars are timestamped REALISTICALLY: a session ending on date D opens at
18:00 ET on D-1, so buckets 0-71 (18:00-23:55) fall on the PRIOR calendar date and
buckets 72+ (00:00-16:55) on D. Grouping by calendar date would split each session.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import pytest

from brokers.alpaca import StockBar
from research.intraday_profile import GLOBEX_N_BUCKETS, to_ny
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
# Helpers
# ---------------------------------------------------------------------------

def _bar(date_et: str, hhmm: tuple[int, int], vol: float) -> StockBar:
    et = datetime(*map(int, date_et.split("-")), hhmm[0], hhmm[1], tzinfo=_ET)
    utc = et.astimezone(timezone.utc)
    return StockBar(
        time=utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
        open=1, high=1, low=1, close=1, volume=vol,
    )


def _session_bar(session_date_et: str, bucket: int, vol: float) -> StockBar:
    """A 5-min bar at Globex ``bucket`` of the session ENDING on session_date_et.

    The session opens 18:00 ET the prior calendar day; bucket b = open + b*5min, so
    buckets 0-71 fall on the prior calendar date and 72+ on session_date_et — exactly
    how real Globex bars are timestamped (the session straddles midnight).
    """
    y, mo, d = map(int, session_date_et.split("-"))
    open_dt = datetime(y, mo, d, 18, 0, tzinfo=_ET) - timedelta(days=1)
    et = open_dt + timedelta(minutes=5 * bucket)
    utc = et.astimezone(timezone.utc)
    return StockBar(
        time=utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
        open=1, high=1, low=1, close=1, volume=vol,
    )


def _full_globex_day(session_date_et: str, daily_total: float) -> list[StockBar]:
    """One complete Globex session: GLOBEX_N_BUCKETS bars spread evenly."""
    vol_per = daily_total / GLOBEX_N_BUCKETS
    return [_session_bar(session_date_et, b, vol_per) for b in range(GLOBEX_N_BUCKETS)]


def _rth_bucket_hhmm(bucket: int) -> tuple[int, int]:
    total_minutes = 9 * 60 + 30 + bucket * 5
    return total_minutes // 60, total_minutes % 60


def _full_equity_day(date_et: str, daily_total: float) -> list[StockBar]:
    vol_per = daily_total / N_BUCKETS
    return [_bar(date_et, _rth_bucket_hhmm(b), vol_per) for b in range(N_BUCKETS)]


# ---------------------------------------------------------------------------
# Globex compute_profile tests
# ---------------------------------------------------------------------------

def test_globex_compute_profile_uses_overnight_bars() -> None:
    """A full 23h Globex profile builds; cumfrac is monotonic and sums to 1."""
    bars: list[StockBar] = []
    for d in range(2, 12):  # 10 sessions, ending 2026-06-02 .. 06-11
        bars.extend(_full_globex_day(f"2026-06-{d:02d}", 100_000 + d * 1_000))

    profile = compute_profile("/ES", bars, window=20, min_days=5, session="globex")
    assert profile is not None
    assert profile.n_days == 10
    assert len(profile.cumfrac) == GLOBEX_N_BUCKETS
    assert profile.cumfrac[-1] == pytest.approx(1.0, abs=1e-9)
    assert all(
        profile.cumfrac[i] <= profile.cumfrac[i + 1] + 1e-12
        for i in range(GLOBEX_N_BUCKETS - 1)
    )


def test_globex_session_straddles_midnight_grouped_as_one_day() -> None:
    """Each midnight-crossing session counts ONCE — not split by calendar date.

    Regression for the calendar-date-grouping bug: real Globex bars span two
    calendar dates per session, so grouping by ``et.date()`` would split every
    session into a dropped evening stub plus a frankensession of mismatched halves.
    """
    bars: list[StockBar] = []
    for d in range(2, 8):  # 6 sessions
        bars.extend(_full_globex_day(f"2026-06-{d:02d}", 100_000))
    # Sanity: the bars really do live on more calendar dates than there are
    # sessions (proof each session straddles midnight).
    cal_dates = {to_ny(b.time).date() for b in bars}
    assert len(cal_dates) > 6

    profile = compute_profile("/ES", bars, window=20, min_days=5, session="globex")
    assert profile is not None
    assert profile.n_days == 6  # six clean sessions, none dropped or merged


def test_globex_compute_profile_drops_truncated_sessions() -> None:
    """Sessions with last_populated < 270 must be excluded."""
    bars: list[StockBar] = []
    for d in range(2, 12):
        bars.extend(_full_globex_day(f"2026-06-{d:02d}", 100_000))
    # A half-session (only 100 buckets) on a non-colliding date — should be excluded.
    bars.extend(_session_bar("2026-06-14", b, 500.0) for b in range(100))

    profile = compute_profile("/ES", bars, window=20, min_days=5, session="globex")
    assert profile is not None
    assert profile.n_days == 10


def test_globex_compute_profile_too_few_days_returns_none() -> None:
    bars: list[StockBar] = []
    for d in range(2, 5):  # 3 sessions < min_days
        bars.extend(_full_globex_day(f"2026-06-{d:02d}", 100_000))
    assert compute_profile("/ES", bars, session="globex") is None


# ---------------------------------------------------------------------------
# Globex rvol_now tests
# ---------------------------------------------------------------------------

def _linear_globex_profile(daily_total: float) -> Profile:
    return Profile(
        symbol="/ES",
        cumfrac=[(i + 1) / GLOBEX_N_BUCKETS for i in range(GLOBEX_N_BUCKETS)],
        median_daily_total=daily_total,
        n_days=10,
    )


def test_globex_rvol_live_expected_pace() -> None:
    """At exactly median pace, RVOL should be ~1.0."""
    daily_total = 276_000.0
    profile = _linear_globex_profile(daily_total)
    vol_per = daily_total / GLOBEX_N_BUCKETS
    # Buckets 0-96 (18:00 -> 02:00 ET) of the session ending 2026-06-02.
    today_bars = [_session_bar("2026-06-02", b, vol_per) for b in range(97)]

    result = rvol_now(profile, today_bars, session="globex")
    assert result is not None
    assert result.state == "live"
    assert result.value == pytest.approx(1.0, rel=0.05)


def test_globex_rvol_includes_overnight_in_current_session() -> None:
    """rvol_now counts the WHOLE current session, including last night's evening.

    Discriminating regression: the evening (calendar D-1) and morning (calendar D)
    belong to the same session. Grouping ``today_bars`` by calendar date would drop
    the evening, undercounting today_cum and skewing RVOL well below 1.0.
    """
    daily_total = 276_000.0
    profile = _linear_globex_profile(daily_total)
    vol_per = daily_total / GLOBEX_N_BUCKETS
    # Buckets 0-119 (18:00 -> 08:00 ET) — straddles midnight across two calendar dates.
    today_bars = [_session_bar("2026-06-02", b, vol_per) for b in range(120)]
    assert len({to_ny(b.time).date() for b in today_bars}) == 2  # really straddles

    result = rvol_now(profile, today_bars, session="globex")
    assert result is not None
    assert result.value == pytest.approx(1.0, rel=0.02)


def test_globex_rvol_double_pace() -> None:
    daily_total = 276_000.0
    profile = _linear_globex_profile(daily_total)
    vol_per = daily_total / GLOBEX_N_BUCKETS
    today_bars = [_session_bar("2026-06-02", b, vol_per * 2) for b in range(50)]

    result = rvol_now(profile, today_bars, session="globex")
    assert result is not None
    assert result.value == pytest.approx(2.0, rel=0.05)


def test_globex_rvol_warming() -> None:
    """Fewer than WARMUP_MIN//5 buckets must yield state="warming"."""
    profile = _linear_globex_profile(276_000)
    # Only 2 buckets — under the 3-bucket warmup gate (WARMUP_MIN=15 -> 15//5=3).
    today_bars = [_session_bar("2026-06-02", b, 1000) for b in range(2)]
    result = rvol_now(profile, today_bars, session="globex")
    assert result is not None
    assert result.value is None
    assert result.state == "warming"


def test_globex_rvol_settled() -> None:
    """Full session (last_bucket >= 275) must yield state="settled"."""
    daily_total = 276_000.0
    profile = _linear_globex_profile(daily_total)
    vol_per = daily_total / GLOBEX_N_BUCKETS
    today_bars = [_session_bar("2026-06-02", b, vol_per) for b in range(GLOBEX_N_BUCKETS)]

    result = rvol_now(profile, today_bars, session="globex")
    assert result is not None
    assert result.state == "settled"
    assert result.value == pytest.approx(1.0, rel=0.05)


# ---------------------------------------------------------------------------
# Filename / path isolation tests
# ---------------------------------------------------------------------------

def test_futures_profile_path_does_not_collide_with_equity(tmp_path, monkeypatch) -> None:
    """Saving /ES must not overwrite or be read as equity ES."""
    monkeypatch.setenv("VOLUME_PROFILE_DIR", str(tmp_path))

    futures_profile = Profile(
        symbol="/ES",
        cumfrac=[(i + 1) / GLOBEX_N_BUCKETS for i in range(GLOBEX_N_BUCKETS)],
        median_daily_total=200_000,
        n_days=10,
    )
    equity_profile = Profile(
        symbol="ES",
        cumfrac=[(i + 1) / N_BUCKETS for i in range(N_BUCKETS)],
        median_daily_total=5_000,
        n_days=8,
    )

    save_profile(futures_profile)
    save_profile(equity_profile)

    loaded_futures = load_profile("/ES")
    loaded_equity = load_profile("ES")

    assert loaded_futures is not None
    assert loaded_futures.symbol == "/ES"
    assert loaded_futures.median_daily_total == pytest.approx(200_000)

    assert loaded_equity is not None
    assert loaded_equity.symbol == "ES"
    assert loaded_equity.median_daily_total == pytest.approx(5_000)

    # Distinct files; the futures one lives under futures/, not at the dir root
    # (Path(dir) / "/ES.json" would be FS root — that bug must not occur).
    assert (tmp_path / "futures" / "ES.json").is_file()
    assert (tmp_path / "ES.json").is_file()
    assert not (tmp_path / "ES.json").read_text().startswith('{"symbol": "/ES"')


# ---------------------------------------------------------------------------
# Equity regression: default path must be byte-identical
# ---------------------------------------------------------------------------

def test_equity_rvol_unchanged() -> None:
    """Equity RVOL (session="rth" / default) must behave exactly as before."""
    daily_total = 78_000.0
    profile = Profile(
        symbol="AAPL",
        cumfrac=[(i + 1) / N_BUCKETS for i in range(N_BUCKETS)],
        median_daily_total=daily_total,
        n_days=10,
    )
    vol_per = daily_total / N_BUCKETS
    today_bars = [_bar("2026-06-23", _rth_bucket_hhmm(b), vol_per) for b in range(41)]

    result_default = rvol_now(profile, today_bars)
    result_explicit = rvol_now(profile, today_bars, session="rth")
    assert result_default == result_explicit
    assert result_default is not None
    assert result_default.state == "live"
    assert result_default.value == pytest.approx(1.0, rel=0.05)


def test_equity_compute_profile_unchanged() -> None:
    """Equity compute_profile (default session) must use RTH buckets only."""
    bars: list[StockBar] = []
    for i in range(10):
        bars.extend(_full_equity_day(f"2026-06-{i + 1:02d}", 10_000))
    # Add overnight bars that must NOT be picked up for equity.
    for i in range(10):
        bars.append(_bar(f"2026-06-{i + 1:02d}", (2, 0), 99_999))

    profile = compute_profile("AAPL", bars, window=20, min_days=5)
    assert profile is not None
    assert profile.n_days == 10
    assert len(profile.cumfrac) == N_BUCKETS
    assert profile.median_daily_total == pytest.approx(10_000)


def test_equity_profile_store_unchanged(tmp_path, monkeypatch) -> None:
    """Equity save/load path must be unchanged (profile_dir/{symbol}.json)."""
    monkeypatch.setenv("VOLUME_PROFILE_DIR", str(tmp_path))
    profile = Profile(
        symbol="AAPL",
        cumfrac=[(i + 1) / N_BUCKETS for i in range(N_BUCKETS)],
        median_daily_total=78_000,
        n_days=10,
    )
    save_profile(profile)
    assert (tmp_path / "AAPL.json").is_file()
    loaded = load_profile("AAPL")
    assert loaded == profile

    # Staleness guard still works.
    filepath = tmp_path / "AAPL.json"
    old_mtime = os.path.getmtime(filepath) - (8 * 86400)
    os.utime(filepath, (old_mtime, old_mtime))
    assert load_profile("AAPL") is None
