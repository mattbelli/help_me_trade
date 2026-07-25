"""Tests for the probe's decision-critical logic: barriers and scoring.

The probe's verdict is only as trustworthy as its resolver — a bug here would
silently decide whether we adopt a model, so it gets tests even though the package
is an experiment.
"""

from __future__ import annotations

import numpy as np
import pytest

from research.kronos_probe import barriers, baseline, config, data, score


def test_resolve_first_touch_wins():
    # up at index 2; down at index 1 -> down should win on the second path.
    closes = np.array(
        [
            [100.0, 100.0, 110.0, 90.0],
            [100.0, 90.0, 110.0, 100.0],
        ]
    )
    out = barriers.resolve(closes, upper=105.0, lower=95.0)
    assert out.tolist() == [barriers.UP, barriers.DOWN]


def test_resolve_no_touch_is_timeout():
    closes = np.array([[100.0, 101.0, 99.0]])
    assert barriers.resolve(closes, upper=110.0, lower=90.0).tolist() == [
        barriers.TIMEOUT
    ]


def test_resolve_accepts_single_path():
    assert barriers.resolve(np.array([100.0, 120.0]), 110.0, 90.0).tolist() == [
        barriers.UP
    ]


def test_ewma_sigma_weights_recent_observations():
    calm_then_wild = np.concatenate([np.zeros(50), np.array([0.05, -0.05] * 10)])
    wild_then_calm = calm_then_wild[::-1].copy()
    assert barriers.ewma_sigma(calm_then_wild, 0.94) > barriers.ewma_sigma(
        wild_then_calm, 0.94
    )


def test_bootstrap_paths_match_target_sigma():
    rng = np.random.default_rng(0)
    returns = rng.normal(0, 0.001, 600)
    paths = baseline.bootstrap_paths(
        returns, 100.0, horizon=12, n_paths=4000, sigma=0.002, rng=rng
    )
    step_sd = np.diff(np.log(paths), axis=1).std()
    assert 0.0017 < step_sd < 0.0023
    assert paths.shape == (4000, 12)


def test_intraday_returns_drop_overnight_gaps():
    from datetime import date

    closes = np.array([100.0, 101.0, 120.0, 121.0])
    dates = [date(2026, 1, 5), date(2026, 1, 5), date(2026, 1, 6), date(2026, 1, 6)]
    returns = barriers.context_log_returns(closes, dates, keep_gaps=False)
    # The 101 -> 120 overnight jump must not appear.
    assert returns.size == 2
    assert returns.max() < 0.02


def test_overnight_gaps_inflate_sigma_when_left_in():
    from datetime import date

    rng = np.random.default_rng(1)
    closes, dates, price = [], [], 100.0
    for session in range(6):
        price *= 1.03 if session else 1.0  # a gap at each session boundary
        for _ in range(20):
            price *= float(np.exp(rng.normal(0, 0.0005)))
            closes.append(price)
            dates.append(date(2026, 1, 5 + session))
    closes = np.array(closes)

    naive = barriers.ewma_sigma(np.diff(np.log(closes)), 0.94)
    clean = barriers.ewma_sigma(
        barriers.context_log_returns(closes, dates, keep_gaps=False), 0.94
    )
    assert naive > clean * 1.5


def test_summarize_prefers_the_better_calibrated_arm():
    # Truth: 'up' happens every time. Kronos says so; bootstrap is agnostic.
    records = [
        {
            "session_date": f"2026-01-{d:02d}",
            "actual": barriers.UP,
            "kronos_counts": [180, 10, 10],
            "bootstrap_counts": [70, 70, 60],
        }
        for d in range(1, 26)
    ]
    out = score.summarize(records)
    assert out["verdict"] == "PASS"
    assert out["difference"]["mean_log_loss_delta"] < 0
    assert out["difference"]["ci_high"] < 0


def test_summarize_fails_when_arms_are_equivalent():
    records = [
        {
            "session_date": f"2026-01-{d:02d}",
            "actual": barriers.UP if d % 2 else barriers.DOWN,
            "kronos_counts": [100, 100, 0],
            "bootstrap_counts": [100, 100, 0],
        }
        for d in range(1, 26)
    ]
    out = score.summarize(records)
    assert out["verdict"] == "FAIL"


def test_too_few_session_dates_is_not_a_verdict():
    # A smoke run whose points all land on one session produced a degenerate CI
    # (ci_low == ci_high) and still read PASS. It must refuse to call it.
    records = [
        {
            "session_date": "2026-01-05",
            "actual": barriers.UP,
            "kronos_counts": [180, 10, 10],
            "bootstrap_counts": [70, 70, 60],
        }
        for _ in range(30)
    ]
    out = score.summarize(records)
    assert out["verdict"] == "INSUFFICIENT DATA"
    assert out["n_session_dates"] == 1


def _vol_records(kronos_sigma, ewma_sigma, garch_sigma, realized, n=25):
    return [
        {
            "session_date": f"2026-01-{d:02d}",
            "actual": barriers.TIMEOUT,
            "kronos_counts": [50, 50, 100],
            "bootstrap_counts": [50, 50, 100],
            "realized_return": realized,
            "kronos_sigma": kronos_sigma,
            "ewma_sigma": ewma_sigma,
            "garch_sigma": garch_sigma,
        }
        for d in range(1, n + 1)
    ]


def test_qlike_prefers_the_forecast_nearer_the_realized_move():
    realized = np.full(4, 0.01)
    accurate = score.qlike(np.full(4, 0.01), realized)
    too_wide = score.qlike(np.full(4, 0.05), realized)
    too_narrow = score.qlike(np.full(4, 0.002), realized)
    assert accurate.mean() < too_wide.mean()
    assert accurate.mean() < too_narrow.mean()


def test_volatility_verdict_requires_beating_both_baselines():
    # Kronos matches the realized move; EWMA is badly wide; GARCH is just as good.
    out = score.summarize_volatility(
        _vol_records(kronos_sigma=0.01, ewma_sigma=0.06, garch_sigma=0.01, realized=0.01)
    )
    assert out["kronos_vs_ewma"]["mean_qlike_delta"] < 0
    # Ties GARCH -> must not pass on the strength of the weaker baseline alone.
    assert out["verdict"] == "FAIL"


def test_volatility_passes_when_kronos_beats_both():
    out = score.summarize_volatility(
        _vol_records(kronos_sigma=0.01, ewma_sigma=0.06, garch_sigma=0.05, realized=0.01)
    )
    assert out["verdict"] == "PASS"


def test_volatility_reports_ewma_vs_garch_independently_of_kronos():
    # Decides the per-symbol-volatility card's own bake-off, whatever Kronos does.
    out = score.summarize_volatility(
        _vol_records(kronos_sigma=0.9, ewma_sigma=0.01, garch_sigma=0.06, realized=0.01)
    )
    assert out["ewma_vs_garch"]["mean_qlike_delta"] < 0  # EWMA wins here
    assert out["ewma_vs_garch"]["ci_high"] < 0
    assert out["verdict"] == "FAIL"  # Kronos loses to both


def test_volatility_tolerates_garch_fit_failures():
    records = _vol_records(0.01, 0.02, None, 0.01)
    out = score.summarize_volatility(records)
    assert "garch" not in out  # dropped, not crashed
    assert out["n_points"] == 25


def test_smoothing_keeps_zero_probability_outcomes_finite():
    probs = score.smooth(np.array([200.0, 0.0, 0.0]))
    assert np.isfinite(score.log_loss(probs, barriers.DOWN))
    assert abs(probs.sum() - 1.0) < 1e-12


# --- Nightly profile (1-hour bars, one-session horizon) --------------------------
# The gap convention and the eval-point rule INVERT between the two profiles, and
# getting either wrong produces a confident wrong verdict rather than a crash.


@pytest.fixture
def nightly():
    """Activate the nightly profile, then put the 5-minute defaults back."""
    saved = {key: getattr(config, key) for key in config._NIGHTLY}
    config.activate("nightly_primary")
    yield
    for key, value in saved.items():
        setattr(config, key, value)


def _sessions(first_day, n_sessions, *, short_sessions=()):
    """Weekday RTH sessions of hourly bars stamped at their start (09:00..15:00).

    ``short_sessions`` holds session ordinals that get an early close, which is what
    a half-day looks like on this grid.
    """
    from datetime import datetime, timedelta

    bars, day, made = [], first_day, 0
    while made < n_sessions:
        if day.weekday() < 5:
            for k in range(4 if made in short_sessions else 7):
                bars.append(
                    {"et": datetime(day.year, day.month, day.day, 9 + k), "v": 1e6}
                )
            made += 1
        day += timedelta(days=1)
    return bars


def test_context_returns_keep_overnight_gaps_when_asked():
    from datetime import date

    closes = np.array([100.0, 101.0, 120.0, 121.0])
    dates = [date(2026, 1, 5), date(2026, 1, 5), date(2026, 1, 6), date(2026, 1, 6)]
    returns = barriers.context_log_returns(closes, dates, keep_gaps=True)
    # All three transitions survive, including the 101 -> 120 gap the 5-min profile
    # drops. A gap-crossing horizon needs its sigma estimated on gap-crossing returns.
    assert returns.size == 3
    assert returns.max() > 0.15


def test_activate_is_a_no_op_for_the_five_minute_windows():
    before = {key: getattr(config, key) for key in config._NIGHTLY}
    config.activate("primary")
    assert {key: getattr(config, key) for key in config._NIGHTLY} == before
    assert config.HORIZON_BARS == 12
    assert config.KEEP_GAP_RETURNS is False


def test_nightly_profile_horizon_is_exactly_one_session(nightly):
    # 7 RTH 1-hour bars per session, so H=7 is one session and one overnight gap.
    assert config.HORIZON_BARS == 7
    assert config.BLOCK_BARS == 7
    assert config.KEEP_GAP_RETURNS is True
    assert config.CONTEXT_BARS < config.MAX_CONTEXT  # upstream appends, never rolls


def test_nightly_eval_points_are_session_closes_spanning_the_next_session(nightly):
    from datetime import date

    bars = _sessions(date(2025, 3, 3), 160)
    indices = data.eval_indices(bars, "nightly_primary")

    assert indices
    for i in indices:
        et = bars[i]["et"]
        assert et.hour == 15  # last RTH bar of its session
        assert et.date() >= date(2025, 9, 1)  # inside the window
        assert i >= config.CONTEXT_BARS  # full context behind it
        next_date = bars[i + 1]["et"].date()
        assert next_date != et.date()  # the path starts after the gap
        assert bars[i + config.HORIZON_BARS]["et"].date() == next_date  # one session

    # One point per session, not three like the clock-time profile.
    assert len(indices) == len({bars[i]["et"].date() for i in indices})


def test_nightly_skips_points_whose_next_session_is_a_half_day(nightly):
    from datetime import date

    def points(bars):
        return {bars[i]["et"].date() for i in data.eval_indices(bars, "nightly_primary")}

    full = _sessions(date(2025, 3, 3), 160)
    half_day = _sessions(date(2025, 3, 3), 160, short_sessions={140})
    # Exactly the session preceding the half-day loses its point: its 7-bar forward
    # window would run past the early close into a second session, crossing two gaps.
    assert len(points(full) - points(half_day)) == 1


def test_bootstrap_block_follows_the_active_profile(nightly):
    rng = np.random.default_rng(0)
    returns = rng.normal(0, 0.004, 300)
    paths = baseline.bootstrap_paths(
        returns, 100.0, horizon=7, n_paths=2000, sigma=0.006, rng=rng
    )
    assert paths.shape == (2000, 7)
    step_sd = np.diff(np.log(paths), axis=1).std()
    assert 0.005 < step_sd < 0.007


def test_early_close_sessions_spot_the_after_hours_stub(nightly):
    from datetime import date, datetime

    bars = []
    for day, volumes in (
        (date(2025, 11, 26), [13e6, 20e6, 16e6, 11e6, 13e6, 12e6, 22e6]),  # normal
        (date(2025, 11, 28), [5.1e6, 7.9e6, 10e6, 14e6, 9.6e6, 1.7e6, 89e3]),  # 1pm close
    ):
        for k, volume in enumerate(volumes):
            bars.append(
                {"et": datetime(day.year, day.month, day.day, 9 + k), "v": volume}
            )

    # The half-day has a FULL 7-bar grid, so a bar count cannot see it; only the
    # final bar's volume against its own session median can.
    assert data.early_close_sessions(bars) == {date(2025, 11, 28)}
