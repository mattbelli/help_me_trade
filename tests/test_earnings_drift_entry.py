"""Live pre-earnings-drift entry predicate (mirrors research/earnings_strategy.py)."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from research.strategy_features import (
    _trading_days_until,
    is_earnings_drift_entry,
)
from signals.trade_context import DatedBar


def _weekday_bars(n: int, start: date, base: float = 100.0) -> list[DatedBar]:
    """n consecutive weekday bars (so bar count == trading days), flat closes."""
    bars: list[DatedBar] = []
    d = start
    while len(bars) < n:
        if d.weekday() < 5:
            bars.append(
                {"d": d.isoformat(), "o": base, "h": base, "l": base, "c": base, "v": 1000.0}
            )
        d += timedelta(days=1)
    return bars


def _date_n_trading_days_after(d: date, n: int) -> str:
    cur = d
    count = 0
    while count < n:
        cur += timedelta(days=1)
        if cur.weekday() < 5:
            count += 1
    return cur.isoformat()


def _setup(
    *,
    reaction_close: float = 106.0,  # +6% vs day-before => blowout
    entry_td_out: int = 10,
    prior_idx: int = 35,
) -> tuple[list[DatedBar], int, list[str]]:
    bars = _weekday_bars(80, date(2026, 1, 5))
    # Inject the prior-earnings reaction: close[j-1]=100, close[j+1]=reaction_close
    bars[prior_idx - 1]["c"] = 100.0
    bars[prior_idx + 1]["c"] = reaction_close
    idx = len(bars) - 1
    last_e = bars[prior_idx]["d"]
    today = date.fromisoformat(bars[idx]["d"])
    next_e = _date_n_trading_days_after(today, entry_td_out)
    return bars, idx, [last_e, next_e]


def test_blowout_at_exact_lead_enters():
    # Exactly entry_lead_td (10) out => the validated backtest's entry day.
    bars, idx, earnings = _setup(reaction_close=106.0, entry_td_out=10)
    assert is_earnings_drift_entry(bars, idx, earnings_dates=earnings) is True


def test_no_blowout_no_entry():
    # +2% prior reaction < 5% blowout threshold
    bars, idx, earnings = _setup(reaction_close=102.0, entry_td_out=10)
    assert is_earnings_drift_entry(bars, idx, earnings_dates=earnings) is False


def test_too_far_from_earnings_no_entry():
    # 20 trading days out > entry_lead_td (10) => not yet at the entry day
    bars, idx, earnings = _setup(reaction_close=106.0, entry_td_out=20)
    assert is_earnings_drift_entry(bars, idx, earnings_dates=earnings) is False


def test_inside_exit_lead_no_entry():
    # 1 trading day out == exit_lead_td => past the entry day (would hold through)
    bars, idx, earnings = _setup(reaction_close=106.0, entry_td_out=1)
    assert is_earnings_drift_entry(bars, idx, earnings_dates=earnings) is False


@pytest.mark.parametrize("td_out", [2, 3, 4, 5, 6, 7, 8, 9])
def test_late_entry_inside_window_no_entry(td_out: int):
    """REGRESSION: entry fires ONLY at exactly entry_lead_td, never late.

    The old predicate was `exit_lead_td < td_to_next <= entry_lead_td`, so a name
    first seen mid-window (e.g. 4 td out) entered with a ~3-day hold — a different,
    unvalidated trade. The backtest enters at `jn - entry_lead_td` and always holds
    ~9 days, so anything inside the window but off the entry day must NOT fire.
    """
    bars, idx, earnings = _setup(reaction_close=106.0, entry_td_out=td_out)
    assert is_earnings_drift_entry(bars, idx, earnings_dates=earnings) is False


def test_mid_window_vs_entry_day_side_by_side():
    """The bug, isolated: same blowout name, td=4 stays out while td=10 enters."""
    late_bars, late_idx, late_earnings = _setup(reaction_close=106.0, entry_td_out=4)
    on_bars, on_idx, on_earnings = _setup(reaction_close=106.0, entry_td_out=10)

    assert is_earnings_drift_entry(late_bars, late_idx, earnings_dates=late_earnings) is False
    assert is_earnings_drift_entry(on_bars, on_idx, earnings_dates=on_earnings) is True


def test_entry_lead_td_is_honoured():
    # A tuned lead moves the entry day with it (5 fires at 5, not at the default 10).
    bars, idx, earnings = _setup(reaction_close=106.0, entry_td_out=5)
    assert (
        is_earnings_drift_entry(bars, idx, earnings_dates=earnings, entry_lead_td=5)
        is True
    )
    assert is_earnings_drift_entry(bars, idx, earnings_dates=earnings) is False


def test_no_future_earnings_no_entry():
    bars, idx, earnings = _setup()
    only_past = [d for d in earnings if d <= bars[idx]["d"]]
    assert is_earnings_drift_entry(bars, idx, earnings_dates=only_past) is False


def test_gap_out_of_range_no_entry():
    # Prior earnings only ~5 trading days before today => gap far below min_gap_td (40)
    bars, idx, earnings = _setup(reaction_close=106.0, entry_td_out=10, prior_idx=73)
    assert is_earnings_drift_entry(bars, idx, earnings_dates=earnings) is False


def test_trading_days_until_counts_weekdays():
    # Mon 2026-06-22 -> Mon 2026-06-29 = 5 weekdays
    assert _trading_days_until("2026-06-22", "2026-06-29") == 5
    assert _trading_days_until("2026-06-22", "2026-06-22") == 0
    assert _trading_days_until("2026-06-29", "2026-06-22") == 0  # past
