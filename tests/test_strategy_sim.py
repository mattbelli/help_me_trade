"""Tests for scale-out exit path simulation."""

from __future__ import annotations

import pytest

from research.strategy_sim import simulate_exit
from signals.indicators import Bar


def _bar(
    c: float,
    *,
    o: float | None = None,
    h: float | None = None,
    low: float | None = None,
    v: float = 1000.0,
) -> Bar:
    return {
        "o": o if o is not None else c,
        "h": h if h is not None else c + 1.0,
        "l": low if low is not None else c - 1.0,
        "c": c,
        "v": v,
    }


def _entry_bars(*forward: Bar) -> list[Bar]:
    return [_bar(100.0, o=100.0, h=100.0, low=100.0)] + list(forward)


def test_immediate_stop() -> None:
    bars = _entry_bars(_bar(98.0, h=99.0, low=96.0))
    result = simulate_exit(bars, 0)
    assert result is not None
    assert result["outcome"] == "stopped"
    assert result["blended_return_pct"] == pytest.approx(-3.0)
    assert result["hit_stop"] is True
    assert result["hit_first_target"] is False


def test_full_win_first_then_runner() -> None:
    bars = _entry_bars(
        _bar(100.0, h=103.0, low=99.0),
        _bar(105.0, h=110.0, low=104.0),
    )
    result = simulate_exit(bars, 0)
    assert result is not None
    assert result["outcome"] == "first_then_runner"
    assert result["blended_return_pct"] == pytest.approx(6.5)
    assert result["hit_first_target"] is True
    assert result["hit_runner"] is True
    assert result["holding_days"] > 1


def test_first_then_breakeven() -> None:
    bars = _entry_bars(
        _bar(102.0, h=103.0, low=101.0),
        _bar(100.0, h=101.0, low=100.0),
    )
    result = simulate_exit(bars, 0)
    assert result is not None
    assert result["outcome"] == "first_then_breakeven"
    assert result["blended_return_pct"] == pytest.approx(1.5)
    assert result["label"] == 1
    assert result["hit_first_target"] is True


def test_timeout_phase_1() -> None:
    forward = [
        _bar(100.0, h=102.0, low=98.0) for _ in range(19)
    ] + [_bar(101.0, h=102.0, low=98.0)]
    bars = _entry_bars(*forward)
    result = simulate_exit(bars, 0, max_holding=20)
    assert result is not None
    assert result["outcome"] == "timeout"
    assert result["blended_return_pct"] == pytest.approx(1.0)
    assert result["timed_out"] is True
    assert result["hit_first_target"] is False


def test_timeout_phase_2() -> None:
    forward = [_bar(102.0, h=103.0, low=101.0)] + [
        _bar(104.0, h=109.0, low=101.0) for _ in range(18)
    ] + [_bar(105.0, h=109.0, low=101.0)]
    bars = _entry_bars(*forward)
    result = simulate_exit(bars, 0, max_holding=20)
    assert result is not None
    assert result["outcome"] == "first_then_timeout"
    assert result["blended_return_pct"] == pytest.approx(4.0)
    assert result["timed_out"] is True
    assert result["hit_first_target"] is True


def test_same_day_ambiguous_phase_1_stop_wins() -> None:
    bars = _entry_bars(_bar(100.0, h=103.0, low=97.0))
    result = simulate_exit(bars, 0)
    assert result is not None
    assert result["outcome"] == "stopped"
    assert result["blended_return_pct"] == pytest.approx(-3.0)
    assert result["same_day_ambiguous"] is True
    assert result["hit_stop"] is True


def test_same_day_ambiguous_phase_2_breakeven_wins() -> None:
    bars = _entry_bars(
        _bar(102.0, h=103.0, low=101.0),
        _bar(105.0, h=110.0, low=100.0),
    )
    result = simulate_exit(bars, 0)
    assert result is not None
    assert result["outcome"] == "first_then_breakeven"
    assert result["blended_return_pct"] == pytest.approx(1.5)
    assert result["same_day_ambiguous"] is True
    assert result["hit_first_target"] is True


def test_bad_bar_guard_returns_none() -> None:
    bars = _entry_bars(_bar(99.0, h=99.0, low=0.0))
    assert simulate_exit(bars, 0) is None


def test_runner_not_booked_on_first_target_bar() -> None:
    bars = _entry_bars(
        _bar(110.0, h=110.0, low=99.0),
        _bar(105.0, h=106.0, low=104.0),
    )
    result = simulate_exit(bars, 0)
    assert result is not None
    assert result["hit_first_target"] is True
    assert result["hit_runner"] is False
    assert result["holding_days"] == 2


def test_trail_captures_big_move() -> None:
    bars = _entry_bars(
        _bar(102.0, h=103.0, low=101.0),
        _bar(115.0, h=115.0, low=112.0),
        _bar(118.0, h=120.0, low=117.0),
        _bar(116.0, h=118.0, low=113.0),
    )
    result = simulate_exit(bars, 0, trail_pct=5.0)
    assert result is not None
    assert result["outcome"] == "first_then_trail"
    assert result["hit_trail"] is True
    assert result["hit_runner"] is False
    # Peak 120 from bars 1-3; trail stop 114; remainder +14%; blended 0.5*3 + 0.5*14 = 8.5%
    assert result["blended_return_pct"] == pytest.approx(8.5)
    assert result["blended_return_pct"] > 6.5


def test_trail_protects_breakeven() -> None:
    bars = _entry_bars(
        _bar(102.0, h=103.0, low=101.0),
        _bar(99.0, h=100.0, low=99.0),
    )
    result = simulate_exit(bars, 0, trail_pct=5.0)
    assert result is not None
    assert result["outcome"] == "first_then_trail"
    assert result["hit_trail"] is True
    assert result["blended_return_pct"] == pytest.approx(1.5)


def test_trail_no_lookahead_on_spike_bar() -> None:
    bars = _entry_bars(
        _bar(102.0, h=103.0, low=101.0),
        _bar(98.0, h=150.0, low=95.0),
    )
    result = simulate_exit(bars, 0, trail_pct=5.0)
    assert result is not None
    assert result["outcome"] == "first_then_trail"
    assert result["hit_trail"] is True
    # Prior peak 103 → trail 97.85; floored at breakeven 100; low 95 triggers exit at 0%
    assert result["blended_return_pct"] == pytest.approx(1.5)


def test_default_unchanged_with_trail_none() -> None:
    full_win_bars = _entry_bars(
        _bar(100.0, h=103.0, low=99.0),
        _bar(105.0, h=110.0, low=104.0),
    )
    full_win = simulate_exit(full_win_bars, 0, trail_pct=None)
    assert full_win is not None
    assert full_win["outcome"] == "first_then_runner"
    assert full_win["blended_return_pct"] == pytest.approx(6.5)
    assert full_win["hit_trail"] is False

    be_bars = _entry_bars(
        _bar(102.0, h=103.0, low=101.0),
        _bar(100.0, h=101.0, low=100.0),
    )
    be = simulate_exit(be_bars, 0, trail_pct=None)
    assert be is not None
    assert be["outcome"] == "first_then_breakeven"
    assert be["blended_return_pct"] == pytest.approx(1.5)
    assert be["hit_trail"] is False


def test_long_unchanged_with_explicit_side() -> None:
    bars = _entry_bars(
        _bar(100.0, h=103.0, low=99.0),
        _bar(105.0, h=110.0, low=104.0),
    )
    result = simulate_exit(bars, 0, side="long")
    assert result is not None
    assert result["outcome"] == "first_then_runner"
    assert result["blended_return_pct"] == pytest.approx(6.5)
    assert result["side"] == "long"


def test_short_immediate_stop() -> None:
    bars = _entry_bars(_bar(100.0, h=103.0, low=99.0))
    result = simulate_exit(bars, 0, side="short")
    assert result is not None
    assert result["outcome"] == "stopped"
    assert result["blended_return_pct"] == pytest.approx(-3.0)
    assert result["hit_stop"] is True
    assert result["side"] == "short"


def test_short_full_win_first_then_runner() -> None:
    bars = _entry_bars(
        _bar(100.0, h=101.0, low=97.0),
        _bar(95.0, h=96.0, low=90.0),
    )
    result = simulate_exit(bars, 0, side="short")
    assert result is not None
    assert result["outcome"] == "first_then_runner"
    assert result["blended_return_pct"] == pytest.approx(6.5)
    assert result["hit_first_target"] is True
    assert result["hit_runner"] is True


def test_short_trail_captures_big_move() -> None:
    bars = _entry_bars(
        _bar(100.0, h=101.0, low=97.0),
        _bar(82.0, h=85.0, low=80.0),
        _bar(83.0, h=86.0, low=82.0),
    )
    result = simulate_exit(bars, 0, side="short", trail_pct=5.0)
    assert result is not None
    assert result["outcome"] == "first_then_trail"
    assert result["hit_trail"] is True
    # Trough 80 → trail stop 84; short remainder +16%; blended 0.5*3 + 0.5*16 = 9.5%
    assert result["blended_return_pct"] == pytest.approx(9.5)
