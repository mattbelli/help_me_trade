"""Chain-snapshot shaping — the vendor quirks and the refuse-rather-than-invent rules."""

from __future__ import annotations

import math
from datetime import date

from signals.options_chain_snapshot import (
    MAX_DTE,
    MONEYNESS_BAND,
    atm_iv,
    bar_volume,
    board_aggregates,
    constant_maturity_iv,
    front_atm_iv,
    in_band,
    parse_occ,
)

SESSION = "2026-07-24"
SESSION_DATE = date(2026, 7, 24)


def test_parses_a_standard_occ_symbol() -> None:
    c = parse_occ("SPY260821C00691000")
    assert c is not None
    assert (c.root, c.opt_type, c.strike) == ("SPY", "call", 691.0)
    assert c.expiration == date(2026, 8, 21)


def test_parses_a_root_containing_digits() -> None:
    # Anchoring on the fixed-width tail is what keeps these from mis-splitting.
    c = parse_occ("BRKB270115P00400000")
    assert c is not None
    assert (c.root, c.opt_type, c.strike) == ("BRKB", "put", 400.0)


def test_rejects_a_non_occ_symbol() -> None:
    assert parse_occ("AAPL") is None
    assert parse_occ("") is None


def test_stale_daily_bar_reports_zero_volume_not_the_old_bar() -> None:
    """The trap: dailyBar is the LAST TRADED session, not the session sampled."""
    stale = {"t": "2026-07-22T04:00:00Z", "v": 4242}
    volume, bar_date = bar_volume(stale, SESSION)
    assert volume == 0
    assert bar_date == "2026-07-22"


def test_current_daily_bar_reports_its_volume() -> None:
    fresh = {"t": "2026-07-24T04:00:00Z", "v": 17}
    assert bar_volume(fresh, SESSION) == (17, "2026-07-24")


def test_missing_daily_bar_is_zero_with_no_date() -> None:
    assert bar_volume(None, SESSION) == (0, None)


def test_band_keeps_near_the_money_and_drops_the_wings() -> None:
    spot = 100.0
    assert in_band(100.0, spot, 30)
    assert in_band(spot * (1 + MONEYNESS_BAND), spot, 30)
    assert not in_band(spot * (1 + MONEYNESS_BAND) + 0.01, spot, 30)
    assert not in_band(100.0, spot, MAX_DTE + 1)


def test_atm_iv_interpolates_between_the_bracketing_strikes() -> None:
    points = [(90.0, 0.30), (110.0, 0.20)]
    # spot sits exactly halfway, so the ATM read is the midpoint of the two.
    assert atm_iv(points, 100.0) == 0.25


def test_atm_iv_averages_the_call_and_put_at_each_strike() -> None:
    points = [(100.0, 0.20), (100.0, 0.30), (120.0, 0.40)]
    assert atm_iv(points, 110.0) == 0.325


def test_atm_iv_refuses_to_extrapolate_past_the_quoted_board() -> None:
    """A spot outside the quoted strikes gets None — never an invented number."""
    points = [(50.0, 0.30), (60.0, 0.32)]
    assert atm_iv(points, 100.0) is None
    assert atm_iv(points, 10.0) is None


def test_constant_maturity_interpolates_in_variance_not_in_vol() -> None:
    iv = constant_maturity_iv([(20, 0.20), (50, 0.40)], target_dte=30)
    assert iv is not None
    # linear in total variance: var20=.04*20=.8, var50=.16*50=8 -> w=1/3
    expected = math.sqrt((0.8 + (8.0 - 0.8) / 3) / 30)
    assert abs(iv - expected) < 1e-12
    # Total variance grows faster than linearly across this sloped term structure,
    # so the variance-space read sits ABOVE the naive vol-space one — that gap is
    # the bias interpolating vol directly would bake into every IV-rank input.
    assert iv > 0.20 + (0.40 - 0.20) / 3


def test_constant_maturity_returns_an_exact_expiry_untouched() -> None:
    assert constant_maturity_iv([(10, 0.5), (30, 0.22), (60, 0.3)]) == 0.22


def test_constant_maturity_refuses_when_the_target_is_not_bracketed() -> None:
    assert constant_maturity_iv([(40, 0.2), (70, 0.25)], target_dte=30) is None
    assert constant_maturity_iv([(5, 0.2), (12, 0.25)], target_dte=30) is None
    assert constant_maturity_iv([], target_dte=30) is None


def test_front_atm_skips_the_zero_dte_expiries() -> None:
    by_expiry = {
        date(2026, 7, 27): [(95.0, 0.90), (105.0, 0.95)],  # 3 dte — pin noise
        date(2026, 8, 21): [(95.0, 0.20), (105.0, 0.22)],  # 28 dte
    }
    iv, dte = front_atm_iv(by_expiry, 100.0, SESSION_DATE)
    assert dte == 28
    assert iv is not None and 0.20 <= iv <= 0.22


def test_aggregates_cover_the_whole_board_and_count_fresh_bars() -> None:
    rows = [
        {"opt_type": "call", "volume": 10, "open_interest": 100,
         "bar_date": SESSION, "session": SESSION},
        {"opt_type": "put", "volume": 5, "open_interest": 50,
         "bar_date": SESSION, "session": SESSION},
        {"opt_type": "call", "volume": 0, "open_interest": 7,
         "bar_date": "2026-07-20", "session": SESSION},
    ]
    agg = board_aggregates(rows)
    assert (agg.call_volume, agg.put_volume) == (10, 5)
    assert (agg.call_oi, agg.put_oi) == (107, 50)
    assert agg.contracts_observed == 3
    # the third row's bar predates the session, so it is not a fresh observation
    assert agg.bars_current == 2
