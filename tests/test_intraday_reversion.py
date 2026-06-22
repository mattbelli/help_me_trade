"""Tests for research.intraday_reversion — synthetic, deterministic, no network."""

from __future__ import annotations

from datetime import date

import pytest

from research.intraday_bars import IntradayBar
from research.intraday_profile import prior_session_reference, session_profile
from research.intraday_reversion import run_reversion_backtest

DAY1 = date(2024, 6, 3)
DAY2 = date(2024, 6, 4)


def _bar(
    ts: str,
    *,
    o: float | None = None,
    h: float | None = None,
    low: float | None = None,
    c: float | None = None,
    v: float = 1000.0,
) -> IntradayBar:
    close = c if c is not None else 100.0
    return {
        "t": ts,
        "o": o if o is not None else close,
        "h": h if h is not None else close,
        "l": low if low is not None else close,
        "c": close,
        "v": v,
    }


def _rth_bar(
    ny_date: date,
    ny_hour: int,
    ny_minute: int,
    *,
    price: float = 100.0,
    v: float = 1000.0,
) -> IntradayBar:
    utc_hour = ny_hour + 4
    ts = f"{ny_date.isoformat()}T{utc_hour:02d}:{ny_minute:02d}:00+00:00"
    return _bar(ts, o=price, h=price, low=price, c=price, v=v)


def _flat_session(
    ny_date: date,
    price: float,
    count: int,
    *,
    volume: float = 1000.0,
    start_minute: int = 30,
) -> list[IntradayBar]:
    bars: list[IntradayBar] = []
    for i in range(count):
        minute = start_minute + i
        hour = 9 + minute // 60
        minute = minute % 60
        bars.append(_rth_bar(ny_date, hour, minute, price=price, v=volume))
    return bars


def _reference_session(ny_date: date) -> list[IntradayBar]:
    """Day-1 bars: POC ~100, VAL ~85 (wide gap for deterministic exits)."""
    return (
        _flat_session(ny_date, 85.0, 50, volume=10_000.0)
        + _flat_session(ny_date, 100.0, 10, volume=100_000.0, start_minute=80)
        + _flat_session(ny_date, 115.0, 3, volume=500.0, start_minute=90)
    )


def _prior_ref(day1_bars: list[IntradayBar]) -> dict:
    sessions = {DAY1: day1_bars}
    ref = prior_session_reference(sessions, DAY2, n_bins=50)
    assert ref is not None
    assert ref["val"] is not None
    assert ref["poc_price"] is not None
    assert ref["poc_price"] > ref["val"]
    return ref


def test_target_hit() -> None:
    ref = _prior_ref(_reference_session(DAY1))
    entry = ref["val"]
    target = ref["poc_price"]

    day2 = [
        _bar(
            _rth_bar(DAY2, 9, 30)["t"],
            o=entry + 2.0,
            h=entry + 2.0,
            low=entry,
            c=entry + 1.0,
        ),
        _bar(
            _rth_bar(DAY2, 9, 31)["t"],
            o=entry + 1.0,
            h=target + 1.0,
            low=entry,
            c=target,
        ),
    ]
    result = run_reversion_backtest(
        _reference_session(DAY1) + day2, stop_points=10.0, cost_points=1.0, n_bins=50
    )

    assert result.n_trades == 1
    trade = result.trades[0]
    assert trade.exit_reason == "target"
    assert trade.points == pytest.approx(target - entry)
    assert trade.points > 0
    assert trade.entry_px == pytest.approx(entry)
    assert trade.exit_px == pytest.approx(target)


def test_stop_hit() -> None:
    ref = _prior_ref(_reference_session(DAY1))
    entry = ref["val"]
    stop_points = 10.0
    stop = entry - stop_points

    day2 = [
        _bar(
            _rth_bar(DAY2, 9, 30)["t"],
            o=entry + 1.0,
            h=entry + 1.0,
            low=entry,
            c=entry,
        ),
        _bar(
            _rth_bar(DAY2, 9, 31)["t"],
            o=entry,
            h=entry,
            low=stop - 1.0,
            c=stop - 1.0,
        ),
    ]
    result = run_reversion_backtest(
        _reference_session(DAY1) + day2, stop_points=stop_points, n_bins=50
    )

    assert result.n_trades == 1
    trade = result.trades[0]
    assert trade.exit_reason == "stop"
    assert trade.points == pytest.approx(-stop_points)
    assert trade.exit_px == pytest.approx(stop)


def test_time_exit_at_close() -> None:
    ref = _prior_ref(_reference_session(DAY1))
    entry = ref["val"]
    target = ref["poc_price"]
    last_close = entry + 2.0

    day2 = [
        _bar(
            _rth_bar(DAY2, 9, 30)["t"],
            o=entry + 1.0,
            h=entry + 1.0,
            low=entry,
            c=entry + 0.5,
        ),
        _bar(
            _rth_bar(DAY2, 9, 31)["t"],
            o=entry + 0.5,
            h=target - 1.0,
            low=entry + 0.5,
            c=last_close,
        ),
        _bar(
            _rth_bar(DAY2, 9, 32)["t"],
            o=last_close,
            h=target - 0.5,
            low=entry + 1.0,
            c=last_close,
        ),
    ]
    result = run_reversion_backtest(
        _reference_session(DAY1) + day2, stop_points=10.0, n_bins=50
    )

    assert result.n_trades == 1
    trade = result.trades[0]
    assert trade.exit_reason == "time"
    assert trade.exit_px == pytest.approx(last_close)
    assert trade.exit_ts == day2[-1]["t"]


def test_no_touch_no_trade() -> None:
    ref = _prior_ref(_reference_session(DAY1))
    entry = ref["val"]

    day2 = [
        _bar(
            _rth_bar(DAY2, 9, 30)["t"],
            o=entry + 1.0,
            h=entry + 2.0,
            low=entry + 0.5,
            c=entry + 1.0,
        ),
        _bar(
            _rth_bar(DAY2, 9, 31)["t"],
            o=entry + 1.0,
            h=entry + 3.0,
            low=entry + 0.5,
            c=entry + 2.0,
        ),
    ]
    result = run_reversion_backtest(_reference_session(DAY1) + day2, n_bins=50)

    assert result.n_trades == 0
    assert result.trades == []


def test_stop_before_target_on_same_bar() -> None:
    ref = _prior_ref(_reference_session(DAY1))
    entry = ref["val"]
    target = ref["poc_price"]
    stop_points = 10.0
    stop = entry - stop_points

    day2 = [
        _bar(
            _rth_bar(DAY2, 9, 30)["t"],
            o=entry,
            h=target + 1.0,
            low=stop,
            c=entry,
        ),
    ]
    result = run_reversion_backtest(
        _reference_session(DAY1) + day2, stop_points=stop_points, n_bins=50
    )

    assert result.n_trades == 1
    trade = result.trades[0]
    assert trade.exit_reason == "stop"
    assert trade.exit_px == pytest.approx(stop)


def test_first_session_skipped_and_no_look_ahead() -> None:
    day1_bars = _reference_session(DAY1)
    ref = _prior_ref(day1_bars)
    entry = ref["val"]
    target = ref["poc_price"]

    day2_base = [
        _bar(
            _rth_bar(DAY2, 9, 30)["t"],
            o=entry + 1.0,
            h=entry + 1.0,
            low=entry,
            c=entry,
        ),
        _bar(
            _rth_bar(DAY2, 9, 31)["t"],
            o=entry,
            h=target + 1.0,
            low=entry,
            c=target,
        ),
    ]
    result_base = run_reversion_backtest(day1_bars + day2_base, n_bins=50)
    assert result_base.n_trades == 1
    assert result_base.trades[0].date == DAY2
    assert result_base.trades[0].ref_date == DAY1

    only_day1 = run_reversion_backtest(day1_bars, n_bins=50)
    assert only_day1.n_trades == 0

    day2_deep = [
        _bar(
            _rth_bar(DAY2, 9, 30)["t"],
            o=entry + 1.0,
            h=entry + 1.0,
            low=entry,
            c=entry,
        ),
        _bar(
            _rth_bar(DAY2, 9, 31)["t"],
            o=entry,
            h=entry,
            low=entry - 50.0,
            c=entry - 50.0,
        ),
    ]
    result_deep = run_reversion_backtest(day1_bars + day2_deep, n_bins=50)
    assert result_deep.n_trades == 1
    assert result_deep.trades[0].entry_px == pytest.approx(entry)
    assert result_deep.trades[0].entry_px == pytest.approx(result_base.trades[0].entry_px)
    assert result_deep.trades[0].entry_px == pytest.approx(ref["val"])

    profile_mutated = session_profile(
        _reference_session(DAY1) + _flat_session(DAY1, 999.0, 20, volume=99_999.0),
        n_bins=50,
    )
    assert profile_mutated["poc_price"] != pytest.approx(ref["poc_price"])


def test_costs_and_net_win_rate() -> None:
    ref = _prior_ref(_reference_session(DAY1))
    entry = ref["val"]
    target = ref["poc_price"]
    cost_points = 1.0

    day2 = [
        _bar(
            _rth_bar(DAY2, 9, 30)["t"],
            o=entry + 1.0,
            h=entry + 1.0,
            low=entry,
            c=entry,
        ),
        _bar(
            _rth_bar(DAY2, 9, 31)["t"],
            o=entry,
            h=target + 1.0,
            low=entry,
            c=target,
        ),
    ]
    result = run_reversion_backtest(
        _reference_session(DAY1) + day2,
        stop_points=10.0,
        cost_points=cost_points,
        n_bins=50,
    )

    trade = result.trades[0]
    gross = trade.points
    net = gross - cost_points
    assert result.avg_points_net == pytest.approx(net)
    assert result.total_points_net == pytest.approx(net)
    assert result.win_rate == (1.0 if net > 0 else 0.0)
