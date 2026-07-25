"""Per-symbol volatility estimator + vol-targeted size multiplier."""

from __future__ import annotations

import math
from datetime import date
from decimal import Decimal

import pytest

from signals.volatility import (
    IDENTITY_MULTIPLIER,
    MIN_OBS,
    SIGMA_FLOOR,
    VOL_TARGET_PROFILES,
    forecast_vol,
    is_degenerate,
    resolve_profile,
    size_multiplier,
)


def _bars(returns: list[float], *, start: str = "2026-01-01", price: float = 100.0):
    """Daily bars whose log returns are (approximately) `returns`."""
    d = date.fromisoformat(start)
    bars = [{"o": price, "h": price, "l": price, "c": price, "v": 1e6, "d": d.isoformat()}]
    for i, r in enumerate(returns, start=1):
        price = price * math.exp(r)
        bars.append(
            {
                "o": price,
                "h": price,
                "l": price,
                "c": price,
                "v": 1e6,
                "d": (date.fromordinal(d.toordinal() + i)).isoformat(),
            }
        )
    return bars


TOMORROW = date(2030, 1, 1)  # far past every fixture date → nothing gets dropped


def test_forecast_none_below_min_obs():
    bars = _bars([0.01] * (MIN_OBS - 5))
    assert forecast_vol("AAA", bars, today=TOMORROW) is None


def test_forecast_returns_sane_sigma():
    # Alternating +/-1% daily → daily sigma ~1%, annualized ~16%.
    returns = [0.01 if i % 2 == 0 else -0.01 for i in range(200)]
    got = forecast_vol("AAA", _bars(returns), today=TOMORROW)
    assert got is not None
    assert got.daily_sigma == pytest.approx(0.01, abs=0.002)
    assert got.annualized == pytest.approx(0.01 * math.sqrt(252), abs=0.03)
    assert got.observations == 200


def test_todays_partial_bar_is_dropped():
    """The newest bar carries the highest EWMA weight — a forming bar must not."""
    returns = [0.01 if i % 2 == 0 else -0.01 for i in range(200)]
    bars = _bars(returns)
    last_completed = bars[-1]["d"]

    # A huge "today" bar appended after the last completed one.
    today = date.fromordinal(date.fromisoformat(last_completed).toordinal() + 1)
    bars.append({"o": 1.0, "h": 1.0, "l": 1.0, "c": 1.0, "v": 1e6, "d": today.isoformat()})

    got = forecast_vol("AAA", bars, today=today)
    assert got is not None
    assert got.as_of == last_completed
    # The -98% fiction never reached the recursion.
    assert got.daily_sigma == pytest.approx(0.01, abs=0.002)


# --- the halt guard: the bug the bake-off surfaced (NBIS, ~2.5y halted) ---


def test_is_degenerate_flags_halted_window():
    returns = [0.0] * 80 + [0.01] * 20
    assert is_degenerate(returns) is True


def test_is_degenerate_passes_a_normal_window():
    returns = [0.01 if i % 2 == 0 else -0.01 for i in range(100)]
    assert is_degenerate(returns) is False


def test_halted_symbol_gets_no_forecast_and_normal_size():
    """A halted name must NOT be upsized to clamp_hi — that is exactly backwards."""
    returns = [0.0] * 200 + [0.05]
    got = forecast_vol("NBIS", _bars(returns), today=TOMORROW)
    assert got is None
    assert size_multiplier(got, profile="balanced") == IDENTITY_MULTIPLIER


def test_sigma_floor_refuses_rather_than_floors():
    """A sub-floor sigma is a data artifact — refuse it, never divide by it."""
    # Tiny but nonzero moves: below the 1bp floor, and not caught by the zero guard.
    returns = [1e-6 if i % 2 == 0 else -1e-6 for i in range(200)]
    got = forecast_vol("AAA", _bars(returns), today=TOMORROW)
    assert got is None
    assert size_multiplier(got) == IDENTITY_MULTIPLIER


# --- the multiplier: the clamp is the safety property ---


def test_multiplier_shrinks_a_stormy_name():
    returns = [0.06 if i % 2 == 0 else -0.06 for i in range(200)]  # ~95% annualized
    got = forecast_vol("AAA", _bars(returns), today=TOMORROW)
    assert got is not None
    mult = size_multiplier(got, profile="balanced")
    assert mult < Decimal("1")
    assert mult >= Decimal(str(VOL_TARGET_PROFILES["balanced"][1]))


def test_multiplier_grows_a_calm_name_but_never_past_the_clamp():
    returns = [0.0005 if i % 2 == 0 else -0.0005 for i in range(200)]
    got = forecast_vol("AAA", _bars(returns), today=TOMORROW)
    assert got is not None
    for name, (_target, _lo, hi) in VOL_TARGET_PROFILES.items():
        assert size_multiplier(got, profile=name) == Decimal(str(hi))


def test_multiplier_is_identity_without_a_forecast():
    assert size_multiplier(None) == IDENTITY_MULTIPLIER


def test_unknown_profile_falls_back_instead_of_raising():
    assert resolve_profile("nonsense") == VOL_TARGET_PROFILES["balanced"]
    assert resolve_profile(None) == VOL_TARGET_PROFILES["balanced"]


def test_every_profile_shrinks_the_same_amount():
    """clamp_lo is 0.25x everywhere — shrinking into a storm is never the risky side."""
    assert {lo for _t, lo, _hi in VOL_TARGET_PROFILES.values()} == {0.25}


def test_sigma_floor_constant_is_one_basis_point():
    assert SIGMA_FLOOR == pytest.approx(1e-4)
