"""Live pre-earnings-drift entry predicate (mirrors research/earnings_strategy.py)."""

from __future__ import annotations

from datetime import date, timedelta

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


def test_blowout_in_window_enters():
    bars, idx, earnings = _setup(reaction_close=106.0, entry_td_out=10)
    assert is_earnings_drift_entry(bars, idx, earnings_dates=earnings) is True


def test_no_blowout_no_entry():
    # +2% prior reaction < 5% blowout threshold
    bars, idx, earnings = _setup(reaction_close=102.0, entry_td_out=10)
    assert is_earnings_drift_entry(bars, idx, earnings_dates=earnings) is False


def test_too_far_from_earnings_no_entry():
    # 20 trading days out > entry_lead_td (10) => not yet in the entry window
    bars, idx, earnings = _setup(reaction_close=106.0, entry_td_out=20)
    assert is_earnings_drift_entry(bars, idx, earnings_dates=earnings) is False


def test_inside_exit_lead_no_entry():
    # 1 trading day out == exit_lead_td => past the entry window (would hold through)
    bars, idx, earnings = _setup(reaction_close=106.0, entry_td_out=1)
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
