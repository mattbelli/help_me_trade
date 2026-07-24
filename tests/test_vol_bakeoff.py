"""Tests for the daily EWMA vs GARCH bake-off.

Nothing here imports `arch` — it is an on-demand research dependency that is
deliberately absent from requirements.txt, so CI must never need it.
"""

from __future__ import annotations

import numpy as np

from research import vol_bakeoff


def test_ewma_sigma_tracks_a_volatility_shift():
    rng = np.random.default_rng(0)
    calm = rng.normal(0, 0.005, 400)
    storm = np.concatenate([calm, rng.normal(0, 0.03, 60)])
    assert vol_bakeoff.ewma_sigma(storm) > 3 * vol_bakeoff.ewma_sigma(calm)


def test_qlike_prefers_the_forecast_nearer_the_realized_move():
    realized = np.full(4, 0.02)
    assert (
        vol_bakeoff.qlike(np.full(4, 0.02), realized).mean()
        < vol_bakeoff.qlike(np.full(4, 0.10), realized).mean()
    )
    assert (
        vol_bakeoff.qlike(np.full(4, 0.02), realized).mean()
        < vol_bakeoff.qlike(np.full(4, 0.004), realized).mean()
    )


def test_garch_forward_is_the_textbook_recursion():
    assert vol_bakeoff.garch_forward(0.1, 0.2, 0.7, 4.0, 3.0) == 0.1 + 0.2 * 9 + 0.7 * 4


def test_ewma_wins_ties_and_straddling_intervals():
    # A tie keeps the zero-dependency incumbent.
    assert vol_bakeoff.verdict(0.0, 0.01) == "KEEP EWMA"
    # A mean win whose interval straddles zero is not a win.
    assert vol_bakeoff.verdict(-0.05, 0.02) == "KEEP EWMA"
    # Clear separation adopts GARCH.
    assert vol_bakeoff.verdict(-0.05, -0.01) == "ADOPT GARCH"


def test_paired_ci_resamples_dates_not_observations():
    # One date whose symbols all agree: resampling dates cannot vary the mean.
    diffs = np.array([0.5, 0.5, 0.5, 0.5])
    dates = np.array(["2026-01-05"] * 4)
    mean, lo, hi = vol_bakeoff.paired_ci(diffs, dates)
    assert mean == lo == hi == 0.5

    # Many dates with spread -> a real interval that brackets the mean.
    rng = np.random.default_rng(1)
    diffs = rng.normal(-0.2, 1.0, 300)
    dates = np.array([f"2026-{1 + i // 25:02d}-{1 + i % 25:02d}" for i in range(300)])
    mean, lo, hi = vol_bakeoff.paired_ci(diffs, dates)
    assert lo < mean < hi


def test_halted_windows_are_excluded_from_both_arms():
    # NBIS/Nebius was halted ~2.5 years; runs of identical closes decay EWMA's
    # variance to ~1e-9 and one symbol then swamps 170k honest observations.
    halted = np.concatenate([np.zeros(300), np.full(60, 0.01)])
    assert vol_bakeoff.is_degenerate(halted)
    healthy = np.concatenate([np.zeros(20), np.full(340, 0.01)])
    assert not vol_bakeoff.is_degenerate(healthy)
    assert vol_bakeoff.is_degenerate(np.array([]))


def test_sigma_floor_stops_the_qlike_blowup():
    # Without the floor this sigma is ~1e-9 and r^2/sigma^2 explodes past 1e17.
    sigma = vol_bakeoff.ewma_sigma(np.zeros(400))
    assert sigma >= vol_bakeoff.SIGMA_FLOOR
    assert vol_bakeoff.qlike(np.array([sigma]), np.array([0.5]))[0] < 3e7
