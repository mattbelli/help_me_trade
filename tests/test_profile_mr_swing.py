"""Tests for research.profile_mr_swing — synthetic, deterministic, no network."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from research.profile_mr_swing import (
    _build_weekly_anchors,
    regime_buy_hold_points,
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
        "val": anchor.val,
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


def _regime_only_base(*, friday_close: float = 112.0) -> list[DatedBar]:
    """Minimal uptrend history + anchor/prior weeks; first regime_only slot is ENTRY_MON."""
    short_warmup = _weekdays_before(PROFILE_WEEK_MON, 4)
    warmup: list[DatedBar] = []
    for idx, day in enumerate(short_warmup):
        px = 90.0 + idx * 2.0
        warmup.append(_d(day, o=px, h=px + 1.0, low=px - 1.0, c=px, v=1000.0))
    return warmup + _anchor_week_bars() + _prior_week_bars(friday_close=friday_close)


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
    assert anchor_base.val == pytest.approx(anchor_mutated.val)
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


def _no_pullback_entry_bar(day: date = ENTRY_MON, *, close: float = 112.0) -> DatedBar:
    """First bar of entry week: uptrend regime, no touch of anchor POC."""
    return _d(day, o=close, h=close + 1.0, low=ANCHOR + 2.0, c=close, v=1000.0)


def test_regime_only_enters_without_profile_gates() -> None:
    base = _base_bars()
    bars = base + [_no_pullback_entry_bar()]
    profile = run_profile_mr_swing(bars, n_bins=50, entry_mode="profile")
    regime_only = run_profile_mr_swing(bars, n_bins=50, entry_mode="regime_only")
    assert profile.n_trades == 0
    assert regime_only.n_trades >= 1


def test_regime_only_respects_regime_and_one_position() -> None:
    """Downtrend blocks regime_only; open position skips a later weekly entry slot."""
    short_warmup = _weekdays_before(ANCHOR_WEEK_MON, 4)
    declining: list[DatedBar] = []
    for idx, day in enumerate(short_warmup):
        px = 120.0 - idx * 3.0
        declining.append(_d(day, o=px, h=px + 1.0, low=px - 1.0, c=px, v=1000.0))

    def _declining_week(start_monday: date) -> list[DatedBar]:
        bars: list[DatedBar] = []
        for offset in range(5):
            day = start_monday + timedelta(days=offset)
            px = 105.0 - offset * 2.0
            bars.append(_d(day, o=px, h=px + 1.0, low=px - 1.0, c=px, v=1000.0))
        return bars

    downtrend_bars = (
        declining
        + _declining_week(ANCHOR_WEEK_MON)
        + _declining_week(PROFILE_WEEK_MON)
        + _declining_week(PRIOR_MON)
        + [_no_pullback_entry_bar(close=100.0)]
    )
    downtrend = run_profile_mr_swing(
        downtrend_bars, n_bins=50, entry_mode="regime_only", max_hold_days=20
    )
    assert downtrend.n_trades == 0

    base = _regime_only_base()
    entry_close = 112.0
    long_hold = [
        _d(ENTRY_MON + timedelta(days=offset), o=102.0, h=110.0, low=100.0, c=105.0)
        for offset in range(0, 15)
        if (ENTRY_MON + timedelta(days=offset)).weekday() < 5
    ]
    bars = base + [_no_pullback_entry_bar(close=entry_close)] + long_hold
    result = run_profile_mr_swing(
        bars, n_bins=50, entry_mode="regime_only", max_hold_days=20
    )
    assert result.n_trades == 1


def test_regime_only_shared_exit_path_t1_then_t2() -> None:
    base = _regime_only_base()
    levels = _anchor_for_current_week(
        _uptrend_history(ANCHOR_WEEK_MON)
        + _establishment_week_bars()
        + _anchor_week_bars()
        + _prior_week_bars()
    )
    entry_close = 112.0
    t1 = levels["pwh"]
    t2 = levels["pwh"] + (levels["pwh"] - levels["pwl"])
    bars = base + [
        _no_pullback_entry_bar(close=entry_close),
        _d(date(2024, 6, 11), o=102.0, h=t1 + 1.0, low=100.0, c=entry_close),
        _d(date(2024, 6, 12), o=110.0, h=t2 + 1.0, low=entry_close + 1.0, c=140.0),
    ]
    result = run_profile_mr_swing(bars, cost_points=1.0, n_bins=50, entry_mode="regime_only")

    assert result.n_trades == 1
    trade = result.trades[0]
    assert trade.exit_reason == "t2"
    assert trade.t1_px == pytest.approx(t1)
    assert trade.final_px == pytest.approx(t2)
    expected_gross = 0.5 * (t1 - entry_close) + 0.5 * (t2 - entry_close)
    assert trade.points_gross == pytest.approx(expected_gross)


def test_regime_buy_hold_points() -> None:
    from research.profile_mr_swing import _regime_uptrend
    from signals.indicators import closes as closes_fn

    days = _weekdays_before(date(2024, 7, 1), 12)
    bars: list[DatedBar] = []
    for idx, day in enumerate(days):
        px = 90.0 + idx * 2.0
        bars.append(_d(day, o=px, h=px + 1.0, low=px - 1.0, c=px, v=1000.0))

    flat_day = days[-1] + timedelta(days=1)
    while flat_day.weekday() >= 5:
        flat_day += timedelta(days=1)
    bars.append(_d(flat_day, o=112.0, h=113.0, low=111.0, c=112.0, v=1000.0))

    down_day = flat_day + timedelta(days=1)
    while down_day.weekday() >= 5:
        down_day += timedelta(days=1)
    bars.append(_d(down_day, o=108.0, h=109.0, low=107.0, c=108.0, v=1000.0))

    total = regime_buy_hold_points(bars, n_bins=50)
    expected = sum(
        float(bars[i]["c"]) - float(bars[i - 1]["c"])
        for i in range(1, len(bars))
        if _regime_uptrend(closes_fn(bars[:i]))
    )
    assert total == pytest.approx(expected)


def _val_dip_entry_bar(
    day: date = ENTRY_MON,
    *,
    val: float,
    close: float | None = None,
) -> DatedBar:
    """Dip through VAL but close below it (fails POC close-acceptance)."""
    fill_close = close if close is not None else val - 1.0
    return _d(
        day,
        o=val + 2.0,
        h=val + 3.0,
        low=val - 0.5,
        c=fill_close,
        v=1000.0,
    )


def _descending_val_week(start_monday: date) -> list[DatedBar]:
    """Prior week with high POC and VAL near the low for favorable val-entry R:R."""
    mon = start_monday
    return [
        _d(mon, o=113.0, h=115.0, low=112.0, c=114.0, v=200_000.0),
        _d(mon + timedelta(days=1), o=110.0, h=115.0, low=101.0, c=110.0, v=200_000.0),
        _d(mon + timedelta(days=2), o=105.0, h=110.0, low=101.0, c=99.0, v=200_000.0),
        _d(mon + timedelta(days=3), o=100.0, h=102.0, low=PWL, c=100.0, v=200_000.0),
        _d(mon + timedelta(days=4), o=96.0, h=98.0, low=PWL + 1.0, c=96.0, v=200_000.0),
    ]


def _val_rr_base() -> list[DatedBar]:
    """Warmup without a complete prior ISO week so only ENTRY_MON can val-enter."""
    warmup_days = [
        date(2024, 5, 13),
        date(2024, 5, 14),
        date(2024, 5, 15),
        date(2024, 5, 16),
        date(2024, 5, 17),
        date(2024, 5, 22),
        date(2024, 5, 23),
        date(2024, 5, 28),
        date(2024, 5, 29),
        date(2024, 5, 30),
        date(2024, 5, 31),
    ]
    warmup: list[DatedBar] = []
    for idx, day in enumerate(warmup_days):
        px = 90.0 + idx * 1.5
        warmup.append(_d(day, o=px, h=px + 1.0, low=px - 1.0, c=px, v=1000.0))
    return warmup + _descending_val_week(PRIOR_MON)


def test_val_entry_fills_without_close_acceptance() -> None:
    base = _base_bars()
    levels = _anchor_for_current_week(base)
    val = levels["val"]
    bars = base + [_val_dip_entry_bar(val=val, close=val - 1.0)]

    val_result = run_profile_mr_swing(
        bars, n_bins=50, entry_mode="profile", entry_level="val"
    )
    poc_result = run_profile_mr_swing(
        bars, n_bins=50, entry_mode="profile", entry_level="poc"
    )

    assert val_result.n_trades == 1
    assert val_result.trades[0].entry_px == pytest.approx(val)
    assert poc_result.n_trades == 0


def test_val_entry_requires_prior_above_dip_and_regime() -> None:
    base = _base_bars()
    levels = _anchor_for_current_week(base)
    val = levels["val"]

    no_prior_above = run_profile_mr_swing(
        _base_bars(friday_close=val - 1.0)
        + [_val_dip_entry_bar(val=val, close=val - 1.0)],
        n_bins=50,
        entry_mode="profile",
        entry_level="val",
    )
    assert no_prior_above.n_trades == 0

    no_dip = run_profile_mr_swing(
        base
        + [
            _d(
                ENTRY_MON,
                o=val + 5.0,
                h=val + 6.0,
                low=val + 1.0,
                c=val + 4.0,
                v=1000.0,
            )
        ],
        n_bins=50,
        entry_mode="profile",
        entry_level="val",
    )
    assert no_dip.n_trades == 0

    history = _uptrend_history(ANCHOR_WEEK_MON)
    for idx in range(max(0, len(history) - 6), len(history)):
        day = date.fromisoformat(history[idx]["d"])
        px = 130.0 - (len(history) - idx) * 3.0
        history[idx] = _d(day, o=px, h=px + 1.0, low=px - 1.0, c=px, v=1000.0)
    downtrend_bars = (
        history
        + _establishment_week_bars()
        + _anchor_week_bars()
        + _prior_week_bars()
        + [_val_dip_entry_bar(val=val, close=val - 1.0)]
    )
    downtrend = run_profile_mr_swing(
        downtrend_bars, n_bins=50, entry_mode="profile", entry_level="val"
    )
    assert downtrend.n_trades == 0


def test_val_entry_favorable_rr_geometry() -> None:
    base = _val_rr_base()
    levels = _anchor_for_current_week(base)
    val = levels["val"]
    assert (levels["pwh"] - val) > (val - levels["pwl"])

    bars = base + [_val_dip_entry_bar(val=val, close=val - 1.0)]
    result = run_profile_mr_swing(
        bars, n_bins=50, entry_mode="profile", entry_level="val"
    )

    assert result.n_trades == 1
    trade = result.trades[0]
    assert trade.entry_date == ENTRY_MON
    assert trade.entry_px == pytest.approx(val)
    assert (trade.pwh - trade.entry_px) > (trade.entry_px - trade.pwl)


def test_poc_entry_level_default_unchanged() -> None:
    base = _base_bars()
    levels = _anchor_for_current_week(base)
    entry_close = levels["anchor_poc"] + 1.0
    bars = base + [_entry_bar(levels=levels, entry_close=entry_close)]

    result = run_profile_mr_swing(bars, n_bins=50, entry_mode="profile")
    assert result.n_trades == 1
    assert result.trades[0].entry_px == pytest.approx(entry_close)

    no_acceptance = run_profile_mr_swing(
        base
        + [
            _d(
                ENTRY_MON,
                o=105.0,
                h=106.0,
                low=levels["anchor_poc"] - 1.0,
                c=levels["anchor_poc"] - 0.5,
                v=1000.0,
            )
        ],
        n_bins=50,
        entry_mode="profile",
        entry_level="poc",
    )
    assert no_acceptance.n_trades == 0


def test_invalid_entry_level_raises() -> None:
    with pytest.raises(ValueError, match="unknown entry_level"):
        run_profile_mr_swing([], entry_level="bad")
