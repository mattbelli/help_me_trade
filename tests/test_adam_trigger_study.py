"""Tests for research.adam_trigger_study — synthetic, in-memory, no network/DB."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest

from research.adam_trigger_study import (
    _compute_slippage,
    load_triggers,
    run_trigger_study,
)
from research.intraday_bars import IntradayBar

MONDAY = date(2025, 6, 2)
TUESDAY = date(2025, 6, 3)
WEDNESDAY = date(2025, 6, 4)
THURSDAY = date(2025, 6, 5)


def _ts(base: datetime, minutes: int = 0) -> str:
    return (base + timedelta(minutes=minutes)).isoformat()


def _bar(base: datetime, minutes: int, *, o: float, h: float, low: float, c: float) -> IntradayBar:
    return {"t": _ts(base, minutes), "o": o, "h": h, "l": low, "c": c, "v": 100.0}


def _rth_ts(ny_date: date, hour: int, minute: int) -> str:
    utc_hour = hour + 4
    return f"{ny_date.isoformat()}T{utc_hour:02d}:{minute:02d}:00+00:00"


def _rth_bar(
    ny_date: date,
    hour: int,
    minute: int,
    *,
    o: float,
    h: float,
    low: float,
    c: float,
    v: float = 100.0,
) -> IntradayBar:
    return {"t": _rth_ts(ny_date, hour, minute), "o": o, "h": h, "l": low, "c": c, "v": v}


def _session_close_bar(ny_date: date, close: float) -> IntradayBar:
    return _rth_bar(ny_date, 15, 59, o=close, h=close, low=close, c=close)


def _minimal_rth_session(
    ny_date: date,
    *,
    fill_bar: IntradayBar | None = None,
    close_px: float,
    extra_bars: list[IntradayBar] | None = None,
) -> list[IntradayBar]:
    bars: list[IntradayBar] = []
    if fill_bar is not None:
        bars.append(fill_bar)
    bars.extend(
        [
            _rth_bar(ny_date, 11, 0, o=close_px, h=close_px, low=close_px, c=close_px),
            _session_close_bar(ny_date, close_px),
        ]
    )
    if extra_bars:
        bars.extend(extra_bars)
    return bars


def _ts(base: datetime, minutes: int = 0) -> str:
    return (base + timedelta(minutes=minutes)).isoformat()


def _bar(base: datetime, minutes: int, *, o: float, h: float, low: float, c: float) -> IntradayBar:
    return {"t": _ts(base, minutes), "o": o, "h": h, "l": low, "c": c, "v": 100.0}


def _trigger(
    *,
    post_id: str = "1",
    post_ts: str,
    level: float,
    direction: str = "long",
) -> dict:
    return {
        "post_id": post_id,
        "post_ts": post_ts,
        "level": level,
        "target": None,
        "direction": direction,
        "forward": True,
        "confidence": "high",
        "raw_text": "test",
    }


BASE = datetime(2025, 6, 1, 14, 0, tzinfo=UTC)
LEVEL = 5000.0
POST_TS = _ts(BASE, 0)


def test_fill_only_after_post_ts():
    """A touch before post_ts must not fill; first touch after post_ts fills at level."""
    bars = [
        _bar(BASE, -30, o=LEVEL, h=LEVEL + 1, low=LEVEL - 1, c=LEVEL),  # before post
        _bar(BASE, 10, o=LEVEL + 5, h=LEVEL + 5, low=LEVEL + 2, c=LEVEL + 3),
        _bar(BASE, 20, o=LEVEL + 1, h=LEVEL + 1, low=LEVEL - 1, c=LEVEL),  # fills
        _bar(BASE, 30, o=LEVEL, h=LEVEL + 25, low=LEVEL, c=LEVEL + 20),
    ]
    trigger = _trigger(post_ts=POST_TS, level=LEVEL, direction="long")
    study = run_trigger_study([trigger], bars, cost_points=0.0, entry_mode="his_level")
    assert study.n_filled == 1
    row = study.results[0]
    assert row.filled
    assert row.entry_ts == _ts(BASE, 20)


def test_no_fill_when_level_not_touched_in_window():
    bars = [
        _bar(BASE, 10, o=LEVEL + 50, h=LEVEL + 55, low=LEVEL + 45, c=LEVEL + 50),
        _bar(BASE, 20, o=LEVEL + 50, h=LEVEL + 55, low=LEVEL + 45, c=LEVEL + 50),
    ]
    trigger = _trigger(post_ts=POST_TS, level=LEVEL, direction="long")
    study = run_trigger_study([trigger], bars, fill_window_hours=1, cost_points=0.0, entry_mode="his_level")
    row = study.results[0]
    assert not row.filled
    assert row.points_net is None
    assert study.n_filled == 0
    assert study.hit_rate == 0.0
    assert study.avg_points_net == 0.0
    assert study.total_points_net == 0.0


def test_long_target_exit():
    level = LEVEL
    target = level + 20
    bars = [
        _bar(BASE, 5, o=level, h=level, low=level, c=level),
        _bar(BASE, 6, o=level, h=target, low=level, c=target),
    ]
    trigger = _trigger(post_ts=POST_TS, level=level, direction="long")
    study = run_trigger_study(
        [trigger], bars, target_points=20, stop_points=10, max_hold_hours=48, cost_points=0.0,
        entry_mode="his_level",
    )
    row = study.results[0]
    assert row.filled
    assert row.exit_reason == "target"
    assert row.points_net == pytest.approx(20.0)


def test_long_stop_exit():
    level = LEVEL
    stop = level - 10
    bars = [
        _bar(BASE, 5, o=level, h=level, low=level, c=level),
        _bar(BASE, 6, o=level, h=level, low=stop, c=stop),
    ]
    trigger = _trigger(post_ts=POST_TS, level=level, direction="long")
    study = run_trigger_study(
        [trigger], bars, target_points=20, stop_points=10, max_hold_hours=48, cost_points=0.0,
        entry_mode="his_level",
    )
    row = study.results[0]
    assert row.exit_reason == "stop"
    assert row.points_net == pytest.approx(-10.0)


def test_long_time_exit():
    level = LEVEL
    bars = [
        _bar(BASE, 5, o=level, h=level + 2, low=level - 2, c=level + 1),
        _bar(BASE, 5 + 60 * 48, o=level + 1, h=level + 3, low=level, c=level + 5),
    ]
    trigger = _trigger(post_ts=POST_TS, level=level, direction="long")
    study = run_trigger_study(
        [trigger], bars, target_points=20, stop_points=10, max_hold_hours=48, cost_points=0.0,
        entry_mode="his_level",
    )
    row = study.results[0]
    assert row.exit_reason == "time"
    assert row.points_net == pytest.approx(5.0)


def test_short_profits_when_price_falls():
    level = LEVEL
    target = level - 20
    bars = [
        _bar(BASE, 5, o=level, h=level, low=level, c=level),
        _bar(BASE, 6, o=level, h=level, low=target, c=target),
    ]
    trigger = _trigger(post_ts=POST_TS, level=level, direction="short")
    study = run_trigger_study(
        [trigger], bars, target_points=20, stop_points=10, max_hold_hours=48, cost_points=0.0,
        entry_mode="his_level",
    )
    row = study.results[0]
    assert row.exit_reason == "target"
    assert row.points_net == pytest.approx(20.0)


def test_short_stop_on_rise():
    level = LEVEL
    stop = level + 10
    bars = [
        _bar(BASE, 5, o=level, h=level, low=level, c=level),
        _bar(BASE, 6, o=level, h=stop, low=level, c=stop),
    ]
    trigger = _trigger(post_ts=POST_TS, level=level, direction="short")
    study = run_trigger_study(
        [trigger], bars, target_points=20, stop_points=10, max_hold_hours=48, cost_points=0.0,
        entry_mode="his_level",
    )
    row = study.results[0]
    assert row.exit_reason == "stop"
    assert row.points_net == pytest.approx(-10.0)


def test_long_stop_wins_same_bar_tie():
    level = LEVEL
    target = level + 20
    stop = level - 10
    bars = [
        _bar(BASE, 5, o=level, h=level, low=level, c=level),
        _bar(BASE, 6, o=level, h=target, low=stop, c=level),
    ]
    trigger = _trigger(post_ts=POST_TS, level=level, direction="long")
    study = run_trigger_study(
        [trigger], bars, target_points=20, stop_points=10, max_hold_hours=48, cost_points=0.0,
        entry_mode="his_level",
    )
    row = study.results[0]
    assert row.exit_reason == "stop"
    assert row.points_net == pytest.approx(-10.0)


def test_short_stop_wins_same_bar_tie():
    level = LEVEL
    target = level - 20
    stop = level + 10
    bars = [
        _bar(BASE, 5, o=level, h=level, low=level, c=level),
        _bar(BASE, 6, o=level, h=stop, low=target, c=level),
    ]
    trigger = _trigger(post_ts=POST_TS, level=level, direction="short")
    study = run_trigger_study(
        [trigger], bars, target_points=20, stop_points=10, max_hold_hours=48, cost_points=0.0,
        entry_mode="his_level",
    )
    row = study.results[0]
    assert row.exit_reason == "stop"
    assert row.points_net == pytest.approx(-10.0)


def test_net_subtracts_cost_points():
    level = LEVEL
    target = level + 20
    bars = [
        _bar(BASE, 5, o=level, h=level, low=level, c=level),
        _bar(BASE, 6, o=level, h=target, low=level, c=target),
    ]
    trigger = _trigger(post_ts=POST_TS, level=level, direction="long")
    study = run_trigger_study([trigger], bars, target_points=20, stop_points=10, cost_points=1.0, entry_mode="his_level")
    assert study.results[0].points_net == pytest.approx(19.0)


def test_pivot_triggers_skipped():
    pivot = _trigger(post_ts=POST_TS, level=LEVEL, direction="pivot")
    long = _trigger(post_id="2", post_ts=POST_TS, level=LEVEL, direction="long")
    target = LEVEL + 20
    bars = [
        _bar(BASE, 5, o=LEVEL, h=LEVEL, low=LEVEL, c=LEVEL),
        _bar(BASE, 6, o=LEVEL, h=target, low=LEVEL, c=target),
    ]
    study = run_trigger_study([pivot, long], bars, cost_points=0.0, entry_mode="his_level")
    assert study.n_triggers == 1
    assert study.results[0].direction == "long"


def test_by_direction_and_beta_benchmark():
    """Shorts profit while always-long beta loses over the same hold windows."""
    level = LEVEL
    short_post = _ts(BASE, 0)
    long_post = _ts(BASE, 120)
    short_target = level - 20
    short_bars = [
        _bar(BASE, 5, o=level, h=level, low=level, c=level),
        _bar(BASE, 6, o=level, h=level, low=short_target, c=short_target),
    ]
    long_fill = level
    long_time_close = level - 15
    long_bars = [
        _bar(BASE, 125, o=long_fill, h=long_fill, low=long_fill, c=long_fill),
        _bar(BASE, 125 + 60 * 48, o=long_fill, h=long_fill + 1, low=long_time_close, c=long_time_close),
    ]
    triggers = [
        _trigger(post_id="s1", post_ts=short_post, level=level, direction="short"),
        _trigger(post_id="l1", post_ts=long_post, level=level, direction="long"),
    ]
    bars = short_bars + long_bars
    study = run_trigger_study(
        triggers,
        bars,
        target_points=20,
        stop_points=10,
        max_hold_hours=48,
        cost_points=0.0,
        entry_mode="his_level",
    )
    assert study.by_direction["short"]["avg_points_net"] > 0
    assert study.by_direction["short"]["total"] > 0
    assert study.beta_avg_points < 0


def test_load_triggers_filters_directions(tmp_path):
    path = tmp_path / "triggers.json"
    path.write_text(
        '[{"post_id":"1","post_ts":"2025-01-01T00:00:00+00:00","level":1,"direction":"long"},'
        '{"post_id":"2","post_ts":"2025-01-01T00:00:00+00:00","level":1,"direction":"pivot"},'
        '{"post_id":"3","post_ts":"2025-01-01T00:00:00+00:00","level":1,"direction":"short"}]',
        encoding="utf-8",
    )
    loaded = load_triggers(path)
    assert len(loaded) == 2
    assert {row["direction"] for row in loaded} == {"long", "short"}


def test_post_price_enters_at_next_bar_open_not_level():
    """First bar after post_ts: entry at its open, not Adam's named level."""
    entry_open = LEVEL + 7.0
    bars = [
        _bar(BASE, -1, o=LEVEL, h=LEVEL + 1, low=LEVEL - 1, c=LEVEL),  # before post
        _bar(BASE, 5, o=entry_open, h=entry_open + 2, low=entry_open - 1, c=entry_open),
        _bar(BASE, 6, o=entry_open, h=entry_open + 25, low=entry_open, c=entry_open + 20),
    ]
    trigger = _trigger(post_ts=POST_TS, level=LEVEL, direction="long")
    study = run_trigger_study([trigger], bars, cost_points=0.0, entry_mode="post_price")
    row = study.results[0]
    assert row.filled
    assert row.entry_ts == _ts(BASE, 5)
    assert row.slippage == pytest.approx(entry_open - LEVEL)


def test_post_price_fills_without_touching_level():
    """post_price fills on next bar even when price never reaches his level."""
    entry_open = LEVEL + 50.0
    bars = [
        _bar(BASE, 10, o=entry_open, h=entry_open + 5, low=entry_open - 2, c=entry_open),
        _bar(BASE, 20, o=entry_open, h=entry_open + 5, low=entry_open - 2, c=entry_open),
    ]
    trigger = _trigger(post_ts=POST_TS, level=LEVEL, direction="long")
    study = run_trigger_study(
        [trigger], bars, fill_window_hours=1, cost_points=0.0, entry_mode="post_price"
    )
    row = study.results[0]
    assert row.filled
    assert row.entry_ts == _ts(BASE, 10)
    assert study.n_filled == 1


def test_post_price_unfilled_when_no_bar_in_window():
    """Unfilled only when no bar exists within fill_window_hours after post_ts."""
    bars = [
        _bar(BASE, 90, o=LEVEL + 10, h=LEVEL + 15, low=LEVEL + 5, c=LEVEL + 10),
    ]
    trigger = _trigger(post_ts=POST_TS, level=LEVEL, direction="long")
    study = run_trigger_study(
        [trigger], bars, fill_window_hours=1, cost_points=0.0, entry_mode="post_price"
    )
    row = study.results[0]
    assert not row.filled
    assert row.slippage is None
    assert study.n_filled == 0


def test_slippage_sign_convention():
    assert _compute_slippage(direction="long", entry_px=5010.0, level=5000.0) > 0
    assert _compute_slippage(direction="long", entry_px=4990.0, level=5000.0) < 0
    assert _compute_slippage(direction="short", entry_px=4990.0, level=5000.0) > 0


def test_post_price_long_target_exit_reuses_exit_logic():
    entry_open = LEVEL + 3.0
    target = entry_open + 20.0
    bars = [
        _bar(BASE, 5, o=entry_open, h=entry_open, low=entry_open, c=entry_open),
        _bar(BASE, 6, o=entry_open, h=target, low=entry_open, c=target),
    ]
    trigger = _trigger(post_ts=POST_TS, level=LEVEL, direction="long")
    study = run_trigger_study(
        [trigger],
        bars,
        target_points=20,
        stop_points=10,
        max_hold_hours=48,
        cost_points=1.0,
        entry_mode="post_price",
    )
    row = study.results[0]
    assert row.filled
    assert row.exit_reason == "target"
    assert row.points_net == pytest.approx(19.0)
    assert row.slippage == pytest.approx(3.0)


def test_post_price_long_stop_exit():
    entry_open = LEVEL + 2.0
    stop = entry_open - 10.0
    bars = [
        _bar(BASE, 5, o=entry_open, h=entry_open, low=entry_open, c=entry_open),
        _bar(BASE, 6, o=entry_open, h=entry_open, low=stop, c=stop),
    ]
    trigger = _trigger(post_ts=POST_TS, level=LEVEL, direction="long")
    study = run_trigger_study(
        [trigger],
        bars,
        target_points=20,
        stop_points=10,
        max_hold_hours=48,
        cost_points=0.0,
        entry_mode="post_price",
    )
    row = study.results[0]
    assert row.exit_reason == "stop"
    assert row.points_net == pytest.approx(-10.0)


def test_both_modes_avg_slippage_over_filled_post_price():
    """avg_slippage_points aggregates over filled post_price trades only."""
    bars = [
        _bar(BASE, 5, o=LEVEL + 4.0, h=LEVEL + 6, low=LEVEL + 2, c=LEVEL + 4),
        _bar(BASE, 10, o=LEVEL + 2.0, h=LEVEL + 4, low=LEVEL + 2, c=LEVEL + 2),
    ]
    triggers = [
        _trigger(post_id="1", post_ts=POST_TS, level=LEVEL, direction="long"),
        _trigger(post_id="2", post_ts=_ts(BASE, 5), level=LEVEL, direction="long"),
    ]
    study_post = run_trigger_study(triggers, bars, cost_points=0.0, entry_mode="post_price")
    assert study_post.n_filled == 2
    assert study_post.avg_slippage_points == pytest.approx(3.0)
    assert study_post.median_slippage_points == pytest.approx(3.0)

    study_his = run_trigger_study(triggers, bars, cost_points=0.0, entry_mode="his_level")
    assert study_his.n_filled == 0
    assert study_his.avg_slippage_points == pytest.approx(0.0)


def test_invalid_entry_mode_raises():
    with pytest.raises(ValueError, match="entry_mode"):
        run_trigger_study([], [], entry_mode="invalid")


def test_invalid_exit_style_raises():
    with pytest.raises(ValueError, match="exit_style"):
        run_trigger_study([], [], exit_style="invalid")


def test_scaleout_tranche_a_exits_at_day1_rth_close():
    level = 5000.0
    day1_close = 5010.0
    post_ts = _rth_ts(MONDAY, 9, 45)
    fill_bar = _rth_bar(MONDAY, 10, 0, o=level, h=level + 1, low=level - 1, c=level)
    bars = _minimal_rth_session(MONDAY, fill_bar=fill_bar, close_px=day1_close)
    trigger = _trigger(post_ts=post_ts, level=level, direction="long")
    study = run_trigger_study(
        [trigger],
        bars,
        exit_style="scaleout",
        stop_points=100.0,
        trail_points=100.0,
        max_hold_days=3,
        cost_points=0.0,
        entry_mode="his_level",
    )
    row = study.results[0]
    assert row.exit_reason == "scaleout"
    assert row.tranche_a_px == pytest.approx(day1_close)


def test_scaleout_tranche_b_trails_long():
    level = 5000.0
    day1_close = 5005.0
    post_ts = _rth_ts(MONDAY, 9, 45)
    fill_bar = _rth_bar(MONDAY, 10, 0, o=level, h=level + 1, low=level - 1, c=level)
    run_high = 5100.0
    trail_points = 30.0
    trail_stop = run_high - trail_points
    bars = _minimal_rth_session(
        MONDAY,
        fill_bar=fill_bar,
        close_px=day1_close,
        extra_bars=[
            _rth_bar(TUESDAY, 10, 0, o=5050.0, h=run_high, low=5050.0, c=5090.0),
            _rth_bar(TUESDAY, 10, 1, o=5090.0, h=5090.0, low=trail_stop, c=trail_stop),
        ],
    )
    trigger = _trigger(post_ts=post_ts, level=level, direction="long")
    study = run_trigger_study(
        [trigger],
        bars,
        exit_style="scaleout",
        stop_points=100.0,
        trail_points=trail_points,
        max_hold_days=3,
        cost_points=0.0,
        entry_mode="his_level",
    )
    row = study.results[0]
    assert row.tranche_b_reason == "trail"
    assert row.tranche_b_px == pytest.approx(trail_stop)


def test_scaleout_tranche_b_initial_stop_long():
    level = 5000.0
    stop_points = 10.0
    stop_px = level - stop_points
    post_ts = _rth_ts(MONDAY, 9, 45)
    fill_bar = _rth_bar(MONDAY, 10, 0, o=level, h=level + 1, low=level - 1, c=level)
    stop_bar = _rth_bar(MONDAY, 10, 1, o=level, h=level, low=stop_px, c=stop_px)
    bars = [fill_bar, stop_bar, _session_close_bar(MONDAY, 5010.0)]
    trigger = _trigger(post_ts=post_ts, level=level, direction="long")
    study = run_trigger_study(
        [trigger],
        bars,
        exit_style="scaleout",
        stop_points=stop_points,
        trail_points=30.0,
        max_hold_days=3,
        cost_points=0.0,
        entry_mode="his_level",
    )
    row = study.results[0]
    assert row.tranche_b_reason == "stop"
    assert row.tranche_b_px == pytest.approx(stop_px)


def test_scaleout_tranche_b_max_hold_long():
    level = 5000.0
    post_ts = _rth_ts(MONDAY, 9, 45)
    fill_bar = _rth_bar(MONDAY, 10, 0, o=level, h=level + 1, low=level - 1, c=level)
    bars = [
        fill_bar,
        _session_close_bar(MONDAY, 5002.0),
        _session_close_bar(TUESDAY, 5003.0),
        _session_close_bar(WEDNESDAY, 5008.0),
    ]
    trigger = _trigger(post_ts=post_ts, level=level, direction="long")
    study = run_trigger_study(
        [trigger],
        bars,
        exit_style="scaleout",
        stop_points=100.0,
        trail_points=100.0,
        max_hold_days=3,
        cost_points=0.0,
        entry_mode="his_level",
    )
    row = study.results[0]
    assert row.tranche_b_reason == "max_hold"
    assert row.tranche_b_px == pytest.approx(5008.0)


def test_scaleout_blended_points_long():
    level = 5000.0
    tranche_a_px = 5010.0
    tranche_b_px = 5020.0
    cost = 1.0
    post_ts = _rth_ts(MONDAY, 9, 45)
    fill_bar = _rth_bar(MONDAY, 10, 0, o=level, h=level + 1, low=level - 1, c=level)
    bars = [
        fill_bar,
        _session_close_bar(MONDAY, tranche_a_px),
        _session_close_bar(TUESDAY, tranche_b_px),
    ]
    trigger = _trigger(post_ts=post_ts, level=level, direction="long")
    study = run_trigger_study(
        [trigger],
        bars,
        exit_style="scaleout",
        stop_points=100.0,
        trail_points=100.0,
        max_hold_days=2,
        cost_points=cost,
        entry_mode="his_level",
    )
    row = study.results[0]
    expected = 0.5 * (tranche_a_px - level) + 0.5 * (tranche_b_px - level) - cost
    assert row.points_net == pytest.approx(expected)


def test_scaleout_blended_points_short():
    level = 5000.0
    tranche_a_px = 4990.0
    tranche_b_px = 4980.0
    cost = 1.0
    post_ts = _rth_ts(MONDAY, 9, 45)
    fill_bar = _rth_bar(MONDAY, 10, 0, o=level + 1, h=level + 1, low=level - 1, c=level)
    bars = [
        fill_bar,
        _session_close_bar(MONDAY, tranche_a_px),
        _session_close_bar(TUESDAY, tranche_b_px),
    ]
    trigger = _trigger(post_ts=post_ts, level=level, direction="short")
    study = run_trigger_study(
        [trigger],
        bars,
        exit_style="scaleout",
        stop_points=100.0,
        trail_points=100.0,
        max_hold_days=2,
        cost_points=cost,
        entry_mode="his_level",
    )
    row = study.results[0]
    expected = 0.5 * (level - tranche_a_px) + 0.5 * (level - tranche_b_px) - cost
    assert row.points_net == pytest.approx(expected)


def test_scaleout_entry_after_rth_close_uses_next_session_for_tranche_a():
    level = 5000.0
    post_ts = _rth_ts(MONDAY, 16, 30)
    fill_bar = {
        "t": _rth_ts(MONDAY, 17, 0),
        "o": level,
        "h": level + 1,
        "l": level - 1,
        "c": level,
        "v": 100.0,
    }
    tuesday_close = 5015.0
    bars = [
        fill_bar,
        _session_close_bar(TUESDAY, tuesday_close),
    ]
    trigger = _trigger(post_ts=post_ts, level=level, direction="long")
    study = run_trigger_study(
        [trigger],
        bars,
        exit_style="scaleout",
        stop_points=100.0,
        trail_points=100.0,
        max_hold_days=3,
        cost_points=0.0,
        entry_mode="his_level",
    )
    row = study.results[0]
    assert row.tranche_a_px == pytest.approx(tuesday_close)


def test_scaleout_tranche_b_trails_short():
    level = 5000.0
    post_ts = _rth_ts(MONDAY, 9, 45)
    fill_bar = _rth_bar(MONDAY, 10, 0, o=level, h=level + 1, low=level - 1, c=level)
    run_low = 4900.0
    trail_points = 30.0
    trail_stop = run_low + trail_points
    bars = _minimal_rth_session(
        MONDAY,
        fill_bar=fill_bar,
        close_px=4995.0,
        extra_bars=[
            _rth_bar(TUESDAY, 10, 0, o=4950.0, h=4950.0, low=run_low, c=4910.0),
            _rth_bar(TUESDAY, 10, 1, o=4910.0, h=trail_stop, low=4910.0, c=trail_stop),
        ],
    )
    trigger = _trigger(post_ts=post_ts, level=level, direction="short")
    study = run_trigger_study(
        [trigger],
        bars,
        exit_style="scaleout",
        stop_points=100.0,
        trail_points=trail_points,
        max_hold_days=3,
        cost_points=0.0,
        entry_mode="his_level",
    )
    row = study.results[0]
    assert row.tranche_b_reason == "trail"
    assert row.tranche_b_px == pytest.approx(trail_stop)
