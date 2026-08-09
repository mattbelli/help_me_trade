"""Active-set selection — dollar-volume ranking, long-DTE floor, pre-open warm.

(The capped Alpaca stream's anchor/hot quote budgets were retired 2026-08-09
with the lane; the selection logic below sizes the POLL lane's active set.)
"""

from __future__ import annotations

from datetime import datetime

import pytest

from signals.options_flow.constants import (
    ACTIVE_SET_LONG_DTE_MIN,
    LONG_DTE_THRESHOLD_DAYS,
)
from signals.options_flow.poll import (
    _day_dollar_volume,
    _select_active_picks,
    _skip_index_zero_dte,
)
from signals.options_flow.runner import _in_preopen_warm
from trading.market_hours import _ET

# --- selection score -----------------------------------------------------------------


def test_dollar_volume_outranks_recency():
    """The old key was (ts_ms, size): a 1-lot from 2s ago beat a $2M block from
    3min ago. Dollar volume must invert that."""
    recent_tiny = _day_dollar_volume(
        {"dailyBar": {"v": 10}}, {"p": 0.05, "s": 1, "t": "2026-07-15T13:59:59Z"}
    )
    older_block = _day_dollar_volume(
        {"dailyBar": {"v": 40_000}}, {"p": 5.0, "s": 400, "t": "2026-07-15T13:31:00Z"}
    )
    assert older_block > recent_tiny


def test_dollar_volume_falls_back_to_trade_notional_at_the_bell():
    # dailyBar absent right at the open — the last trade's notional stands in.
    assert _day_dollar_volume({}, {"p": 2.0, "s": 50}) == pytest.approx(100.0)
    assert _day_dollar_volume({"dailyBar": {}}, {"p": 2.0, "s": 50}) == pytest.approx(100.0)


def test_index_megacap_zero_dte_skipped_at_selection():
    """ZERO_DTE_EXCLUDE discards these prints downstream — measured 07-15: 57-60%
    of SPY/QQQ's slots sat on contracts whose prints we threw away."""
    assert _skip_index_zero_dte("SPY", 0) is True
    assert _skip_index_zero_dte("QQQ", 1) is True
    assert _skip_index_zero_dte("SPY", 2) is False
    assert _skip_index_zero_dte("NVDA", 0) is False  # single names keep 0DTE


def test_long_dated_floor_swaps_in_leaps():
    """07-14 tape: 65% of >=100k premium lives at DTE>=90; a pure $vol rank still
    skews short — the floor must hold when long-dated supply exists."""
    short = [(1000.0 - i, 5, f"SHORT{i:03d}") for i in range(60)]
    long_dated = [(1.0, LONG_DTE_THRESHOLD_DAYS + 30, f"LEAP{i:03d}") for i in range(20)]
    picks = _select_active_picks(short + long_dated, 60)
    assert len(picks) == 60
    n_long = sum(1 for _, dte, _ in picks if dte >= LONG_DTE_THRESHOLD_DAYS)
    assert n_long >= ACTIVE_SET_LONG_DTE_MIN
    # the floor evicts the LOWEST-scored short picks, not the winners
    assert picks[0][2] == "SHORT000"


def test_long_dated_floor_is_supply_limited_not_fabricated():
    short = [(100.0 - i, 3, f"S{i:02d}") for i in range(30)]
    picks = _select_active_picks(short, 20)
    assert len(picks) == 20
    assert all(dte < LONG_DTE_THRESHOLD_DAYS for _, dte, _ in picks)


# --- anchors -------------------------------------------------------------------------


# --- pre-open warm window ------------------------------------------------------------


def _et(hour: int, minute: int) -> datetime:
    # Tue 2026-07-14, a regular session day
    return datetime(2026, 7, 14, hour, minute, tzinfo=_ET)


def test_preopen_warm_window():
    assert _in_preopen_warm(_et(9, 25)) is True  # 5 min out
    assert _in_preopen_warm(_et(9, 21)) is True  # 9 min out
    assert _in_preopen_warm(_et(9, 15)) is False  # too early
    assert _in_preopen_warm(_et(9, 30)) is False  # bell itself -> regular session's job
    assert _in_preopen_warm(_et(10, 0)) is False
