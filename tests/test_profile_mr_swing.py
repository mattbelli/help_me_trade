"""Tests for research.profile_mr_swing — synthetic, deterministic, no network."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from research.profile_mr_swing import (
    _build_weekly_anchors,
    run_profile_mr_swing,
)
from research.volume_profile import build_volume_profile
from signals.trade_context import DatedBar

ANCHOR = 100.0
PWH = 115.0
PWL = 85.0

PRIOR_MON = date(2024, 6, 3)
ANCHOR_WEEK_MON = date(2024, 5, 20)
PROFILE_WEEK_MON = date(2024, 5, 27)
ENTRY_MON = date(2024, 6, 10)


def _d(
    day: date,
    *,
    o: float | None = None,
    h: float | None = None,
    low: float | None = None,
    c: float | None = None,
    v: float = 1000.0,
) -> DatedBar:
    close = c if c is not None else 100.0
    return {
        "d": day.isoformat(),
        "o": o if o is not None else close,
        "h": h if h is not None else close,
        "l": low if low is not None else close,
        "c": close,
        "v": v,
    }


def _weekdays_before(start: date, count: int) -> list[date]:
    days: list[date] = []
    cursor = start
    while len(days) < count:
        if cursor.weekday() < 5:
            days.append(cursor)
        cursor -= timedelta(days=1)
    days.reverse()
    return days


def _uptrend_history(end_before: date, count: int = 12) -> list[DatedBar]:
    days = _weekdays_before(end_before, count)
    bars: list[DatedBar] = []
    for idx, day in enumerate(days):
        px = 90.0 + idx * 1.5
        bars.append(_d(day, o=px, h=px + 1, low=px - 1, c=px, v=1000.0))
    return bars


def _profile_week(start_monday: date, *, friday_close: float = 105.0) -> list[DatedBar]:
    """Five-day week with POC ~100, PWH=115, PWL=85; days do not trigger entries."""
    mon = start_monday
    tue = start_monday + timedelta(days=1)
    wed = start_monday + timedelta(days=2)
    thu = start_monday + timedelta(days=3)
    fri = start_monday + timedelta(days=4)
    return [
        _d(mon, o=101.0, h=102.0, low=101.0, c=105.0, v=1000.0),
        _d(tue, o=101.0, h=102.0, low=101.0, c=105.0, v=1000.0),
        _d(
            wed,
            o=105.0,
            h=PWH,
            low=ANCHOR - 0.5,
            c=ANCHOR - 1.0,
            v=100_000.0,
        ),
        _d(thu, o=101.0, h=101.0, low=PWL, c=ANCHOR - 1.0, v=5000.0),
        _d(fri, o=friday_close, h=friday_close + 1.0, low=101.0, c=friday_close, v=10_000.0),
    ]


def _prior_week_bars(*, friday_close: float = 105.0) -> list[DatedBar]:
    return _profile_week(PRIOR_MON, friday_close=friday_close)


def _anchor_week_bars() -> list[DatedBar]:
    return _profile_week(PROFILE_WEEK_MON)


def _establishment_week_bars() -> list[DatedBar]:
    """First full week in sample; no prior-week anchor exists yet."""
    return _profile_week(ANCHOR_WEEK_MON)


def _anchor_for_current_week(bars: list[DatedBar]) -> dict:
    anchors = _build_weekly_anchors(bars, n_bins=50, value_area_pct=0.70)
    prior_key = (PRIOR_MON.isocalendar().year, PRIOR_MON.isocalendar().week)
    anchor = anchors[prior_key]
    return {
        "anchor_poc": anchor.anchor_poc,
        "pwh": anchor.pwh,
        "pwl": anchor.pwl,
    }


def _base_bars(*, friday_close: float = 112.0) -> list[DatedBar]:
    return (
        _uptrend_history(ANCHOR_WEEK_MON)
        + _establishment_week_bars()
        + _anchor_week_bars()
        + _prior_week_bars(friday_close=friday_close)
    )


def _entry_bar(
    day: date = ENTRY_MON,
    *,
    levels: dict | None = None,
    entry_close: float | None = None,
) -> DatedBar:
    anchor = levels["anchor_poc"] if levels else ANCHOR
    close = entry_close if entry_close is not None else anchor + 1.0
    return _d(
        day,
        o=105.0,
        h=106.0,
        low=anchor - 1.0,
        c=close,
        v=1000.0,
    )


def test_t1_then_t2_win() -> None:
    base = _base_bars()
    levels = _anchor_for_current_week(base)
    entry_close = levels["anchor_poc"] + 1.0
    t1 = levels["pwh"]
    t2 = levels["pwh"] + (levels["pwh"] - levels["pwl"])
    bars = base + [
        _entry_bar(levels=levels, entry_close=entry_close),
        _d(date(2024, 6, 11), o=102.0, h=t1 + 1.0, low=100.0, c=110.0),
        _d(date(2024, 6, 12), o=110.0, h=t2 + 1.0, low=108.0, c=140.0),
    ]
    result = run_profile_mr_swing(bars, cost_points=1.0, n_bins=50, max_hold_days=10)

    assert result.n_trades == 1
    trade = result.trades[0]
    assert trade.exit_reason == "t2"
    assert trade.t1_px == pytest.approx(t1)
    assert trade.t1_date == date(2024, 6, 11)
    assert trade.final_px == pytest.approx(t2)
    expected_gross = 0.5 * (t1 - entry_close) + 0.5 * (t2 - entry_close)
    assert trade.points_gross == pytest.approx(expected_gross)
    assert trade.points_net == pytest.approx(expected_gross - 1.0)
    assert trade.points_net > 0


def test_stop_hit() -> None:
    base = _base_bars()
    levels = _anchor_for_current_week(base)
    pwl = levels["pwl"]
    entry_close = levels["anchor_poc"] + 1.0
    bars = base + [
        _entry_bar(levels=levels, entry_close=entry_close),
        _d(date(2024, 6, 11), o=100.0, h=101.0, low=pwl - 1.0, c=pwl - 2.0),
    ]
    result = run_profile_mr_swing(bars, cost_points=1.0, n_bins=50)

    assert result.n_trades == 1
    trade = result.trades[0]
    assert trade.exit_reason == "stop"
    assert trade.final_px == pytest.approx(pwl - 2.0)
    assert trade.points_net < 0


def test_t1_then_stop_breakeven() -> None:
    base = _base_bars()
    levels = _anchor_for_current_week(base)
    entry_close = levels["anchor_poc"] + 1.0
    t1 = levels["pwh"]
    bars = base + [
        _entry_bar(levels=levels, entry_close=entry_close),
        _d(date(2024, 6, 11), o=102.0, h=t1 + 1.0, low=100.0, c=110.0),
        _d(
            date(2024, 6, 12),
            o=110.0,
            h=111.0,
            low=entry_close - 1.0,
            c=entry_close - 1.0,
        ),
    ]
    result = run_profile_mr_swing(bars, cost_points=1.0, n_bins=50)

    assert result.n_trades == 1
    trade = result.trades[0]
    assert trade.exit_reason == "t1_then_stop"
    assert trade.t1_px == pytest.approx(t1)
    expected_gross = 0.5 * (t1 - entry_close) + 0.5 * ((entry_close - 1.0) - entry_close)
    assert trade.points_gross == pytest.approx(expected_gross)


def test_time_exit() -> None:
    base = _base_bars()
    levels = _anchor_for_current_week(base)
    entry_close = levels["anchor_poc"] + 1.0
    drift_days = [
        _d(ENTRY_MON + timedelta(days=offset), o=102.0, h=110.0, low=100.0, c=105.0)
        for offset in range(1, 11)
        if (ENTRY_MON + timedelta(days=offset)).weekday() < 5
    ]
    bars = base + [_entry_bar(levels=levels, entry_close=entry_close)] + drift_days[:10]
    result = run_profile_mr_swing(bars, cost_points=1.0, n_bins=50, max_hold_days=10)

    assert result.n_trades == 1
    trade = result.trades[0]
    assert trade.exit_reason == "time"
    assert trade.final_px == pytest.approx(105.0)


def test_no_entry_without_acceptance() -> None:
    base = _base_bars()
    levels = _anchor_for_current_week(base)
    anchor = levels["anchor_poc"]
    bars = base + [
        _d(ENTRY_MON, o=105.0, h=106.0, low=anchor - 1.0, c=anchor - 0.5, v=1000.0),
    ]
    result = run_profile_mr_swing(bars, n_bins=50)
    assert result.n_trades == 0


def test_no_entry_without_prior_above() -> None:
    base = _base_bars(friday_close=95.0)
    levels = _anchor_for_current_week(base)
    anchor = levels["anchor_poc"]
    bars = base + [
        _d(ENTRY_MON, o=96.0, h=101.0, low=anchor - 1.0, c=anchor + 1.0, v=1000.0),
    ]
    result = run_profile_mr_swing(bars, n_bins=50)
    assert result.n_trades == 0


def test_regime_gate_blocks_entry() -> None:
    history = _uptrend_history(ANCHOR_WEEK_MON)
    for idx in range(max(0, len(history) - 6), len(history)):
        day = date.fromisoformat(history[idx]["d"])
        px = 130.0 - (len(history) - idx) * 3.0
        history[idx] = _d(day, o=px, h=px + 1.0, low=px - 1.0, c=px, v=1000.0)
    bars = (
        history
        + _establishment_week_bars()
        + _anchor_week_bars()
        + _prior_week_bars()
        + [_entry_bar(levels=_anchor_for_current_week(
            history + _establishment_week_bars() + _anchor_week_bars() + _prior_week_bars()
        ))]
    )
    result = run_profile_mr_swing(bars, n_bins=50)
    assert result.n_trades == 0


def test_no_look_ahead_anchor() -> None:
    base = _base_bars()
    anchors_base = _build_weekly_anchors(base, n_bins=50, value_area_pct=0.70)
    prior_key = (PRIOR_MON.isocalendar().year, PRIOR_MON.isocalendar().week)
    anchor_base = anchors_base[prior_key]

    mutated = base + [
        _d(date(2024, 6, 10), o=500.0, h=900.0, low=400.0, c=800.0, v=999_999.0),
        _d(date(2024, 6, 11), o=800.0, h=900.0, low=700.0, c=850.0, v=999_999.0),
    ]
    anchors_mutated = _build_weekly_anchors(mutated, n_bins=50, value_area_pct=0.70)
    anchor_mutated = anchors_mutated[prior_key]

    assert anchor_base.anchor_poc == pytest.approx(anchor_mutated.anchor_poc)
    assert anchor_base.pwh == pytest.approx(anchor_mutated.pwh)
    assert anchor_base.pwl == pytest.approx(anchor_mutated.pwl)


def test_avg_r_and_costs() -> None:
    base = _base_bars()
    levels = _anchor_for_current_week(base)
    entry_close = levels["anchor_poc"] + 1.0
    t1 = levels["pwh"]
    t2 = levels["pwh"] + (levels["pwh"] - levels["pwl"])
    bars = base + [
        _entry_bar(levels=levels, entry_close=entry_close),
        _d(date(2024, 6, 11), o=102.0, h=t1 + 1.0, low=100.0, c=110.0),
        _d(date(2024, 6, 12), o=110.0, h=t2 + 1.0, low=108.0, c=140.0),
    ]
    cost_points = 1.0
    result = run_profile_mr_swing(
        bars, cost_points=cost_points, n_bins=50, max_hold_days=10
    )

    trade = result.trades[0]
    assert trade.points_net == pytest.approx(trade.points_gross - cost_points)
    assert result.avg_points_net == pytest.approx(trade.points_net)
    assert result.total_points_net == pytest.approx(trade.points_net)
    initial_risk = entry_close - levels["pwl"]
    assert result.avg_R == pytest.approx(trade.points_net / initial_risk)


def test_stop_wins_on_same_bar_tie() -> None:
    base = _base_bars()
    levels = _anchor_for_current_week(base)
    entry_close = levels["anchor_poc"] + 1.0
    pwl = levels["pwl"]
    t1 = levels["pwh"]
    bars = base + [
        _entry_bar(levels=levels, entry_close=entry_close),
        _d(date(2024, 6, 11), o=100.0, h=t1 + 5.0, low=pwl, c=100.0),
    ]
    result = run_profile_mr_swing(bars, n_bins=50)
    trade = result.trades[0]
    assert trade.exit_reason == "stop"
    assert trade.final_px == pytest.approx(pwl)


def test_prior_week_profile_levels() -> None:
    levels = _anchor_for_current_week(_base_bars())
    assert 98.0 <= levels["anchor_poc"] <= 102.0
    assert levels["pwh"] == pytest.approx(PWH)
    assert levels["pwl"] == pytest.approx(PWL)
    profile = build_volume_profile(_prior_week_bars(), n_bins=50)
    assert profile["poc_price"] == pytest.approx(levels["anchor_poc"], abs=1.0)
