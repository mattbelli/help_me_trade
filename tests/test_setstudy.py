"""Tests for research.setstudy — synthetic, in-memory, no network/DB."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from research.intraday_bars import IntradayBar
from research.setstudy import (
    analyze_call,
    append_log_rows,
    build_setstudy_report,
)


def _ts(base: datetime, minutes: int = 0) -> str:
    return (base + timedelta(minutes=minutes)).isoformat()


def _bar(
    base: datetime,
    minutes: int,
    *,
    o: float,
    h: float,
    low: float,
    c: float,
    v: float = 100.0,
) -> IntradayBar:
    return {"t": _ts(base, minutes), "o": o, "h": h, "l": low, "c": c, "v": v}


def _call(
    *,
    post_id: str = "1",
    post_ts: str,
    level: float,
    direction: str = "long",
    entry_type: str = "realtime_entry",
) -> dict:
    return {
        "post_id": post_id,
        "post_ts": post_ts,
        "level": level,
        "target": None,
        "direction": direction,
        "forward": True,
        "entry_type": entry_type,
        "confidence": "high",
        "raw_text": "test",
    }


BASE = datetime(2025, 6, 2, 14, 0, tzinfo=UTC)
POST_TS = _ts(BASE, 0)
LEVEL = 5000.0


def test_analyze_call_long_rises_and_touches_level():
    """Long call: price rises after post → positive MFE and direction_worked; level touch detected."""
    bars = [
        _bar(BASE, 1, o=5005, h=5010, low=5004, c=5008),
        _bar(BASE, 2, o=5008, h=5030, low=4995, c=5025),  # touches 5000, +25 MFE
        _bar(BASE, 3, o=5025, h=5035, low=5020, c=5030),
    ]
    result = analyze_call(
        _call(post_ts=POST_TS, level=LEVEL, direction="long"),
        bars,
        move_threshold_points=20,
    )
    assert result["post_price"] == 5005.0
    assert result["touched_level"] is True
    assert result["mfe_points"] > 0
    assert result["mae_points"] == pytest.approx(10.0)
    assert result["direction_worked"] is True


def test_analyze_call_short_falls():
    """Short call: falling price → favorable MFE and adverse MAE with correct signs."""
    bars = [
        _bar(BASE, 1, o=5005, h=5006, low=5004, c=5005),
        _bar(BASE, 2, o=5005, h=5008, low=4980, c=4985),
        _bar(BASE, 3, o=4985, h=4990, low=4975, c=4980),
    ]
    result = analyze_call(
        _call(post_ts=POST_TS, level=LEVEL, direction="short"),
        bars,
        move_threshold_points=20,
    )
    assert result["post_price"] == 5005.0
    assert result["mfe_points"] > 0
    assert result["mae_points"] == pytest.approx(3.0)
    assert result["direction_worked"] is True


def test_no_look_ahead_before_post():
    """Pre-post spikes must not affect touched_level, MFE, or MAE."""
    bars = [
        _bar(BASE, -30, o=LEVEL, h=LEVEL + 30, low=LEVEL - 5, c=LEVEL + 10),
        _bar(BASE, 1, o=5005, h=5006, low=5004, c=5005),
        _bar(BASE, 2, o=5005, h=5007, low=5003, c=5006),
    ]
    result = analyze_call(
        _call(post_ts=POST_TS, level=LEVEL, direction="long"),
        bars,
        move_threshold_points=20,
    )
    assert result["touched_level"] is False
    assert result["mfe_points"] == pytest.approx(2.0)
    assert result["mae_points"] == pytest.approx(2.0)
    assert result["direction_worked"] is None


def test_direction_worked_race_outcomes():
    """Threshold race: favorable first → True, adverse first → False, neither → None."""
    favorable_first = [
        _bar(BASE, 1, o=5005, h=5030, low=5004, c=5025),
    ]
    adverse_first = [
        _bar(BASE, 1, o=5005, h=5006, low=4975, c=4980),
    ]
    neither = [
        _bar(BASE, 1, o=5005, h=5010, low=5000, c=5008),
    ]
    call = _call(post_ts=POST_TS, level=LEVEL, direction="long")
    assert analyze_call(call, favorable_first, move_threshold_points=20)["direction_worked"] is True
    assert analyze_call(call, adverse_first, move_threshold_points=20)["direction_worked"] is False
    assert analyze_call(call, neither, move_threshold_points=20)["direction_worked"] is None


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
    v: float = 1000.0,
) -> IntradayBar:
    return {
        "t": _rth_ts(ny_date, hour, minute),
        "o": o,
        "h": h,
        "l": low,
        "c": c,
        "v": v,
    }


def test_build_setstudy_report_two_calls_skips_pivot():
    """Report includes directional calls only, session profile, and summary counts."""
    ny_date = date(2025, 6, 2)
    post_long = _rth_ts(ny_date, 10, 0)
    post_short = _rth_ts(ny_date, 11, 0)
    post_pivot = _rth_ts(ny_date, 12, 0)

    triggers = [
        _call(post_id="long1", post_ts=post_long, level=5000, direction="long"),
        _call(post_id="short1", post_ts=post_short, level=5100, direction="short"),
        {
            **_call(post_id="pivot1", post_ts=post_pivot, level=5050, direction="pivot"),
            "entry_type": "pivot_marker",
        },
    ]

    bars = [
        _rth_bar(ny_date, 9, 30, o=4990, h=5010, low=4985, c=5005, v=5000),
        _rth_bar(ny_date, 9, 31, o=5005, h=5020, low=5000, c=5015, v=4000),
        _rth_bar(ny_date, 10, 1, o=5015, h=5040, low=5010, c=5035, v=3000),
        _rth_bar(ny_date, 11, 1, o=5035, h=5040, low=5005, c=5010, v=3000),
        _rth_bar(ny_date, 15, 59, o=5010, h=5015, low=5005, c=5012, v=2000),
    ]

    report = build_setstudy_report(ny_date, triggers, bars, move_threshold_points=20)
    assert report["n_calls"] == 2
    assert len(report["calls"]) == 2
    assert report["session"]["high"] == pytest.approx(5040.0)
    assert report["session"]["low"] == pytest.approx(4985.0)
    assert report["session"]["poc"] is not None
    assert report["summary"]["n_direction_worked"] >= 0
    assert report["summary"]["long_worked"] + report["summary"]["short_worked"] <= 2


def test_near_node_when_level_at_poc(tmp_path: Path):
    """A call level matching session POC is flagged near_node."""
    ny_date = date(2025, 6, 2)
    bars = [
        _rth_bar(ny_date, 9, 30, o=5000, h=5005, low=4995, c=5002, v=10000),
        _rth_bar(ny_date, 9, 31, o=5002, h=5008, low=4998, c=5004, v=100),
        _rth_bar(ny_date, 10, 1, o=5004, h=5010, low=5000, c=5008, v=100),
        _rth_bar(ny_date, 15, 59, o=5008, h=5010, low=5005, c=5009, v=100),
    ]
    profile_report = build_setstudy_report(ny_date, [], bars)
    poc = profile_report["session"]["poc"]
    assert poc is not None

    triggers = [
        _call(
            post_id="at_poc",
            post_ts=_rth_ts(ny_date, 10, 5),
            level=float(poc),
            direction="long",
        )
    ]
    report = build_setstudy_report(ny_date, triggers, bars)
    assert report["calls"][0]["near_node"] is True


def test_append_log_rows_dedupes_by_post_id(tmp_path: Path):
    log_path = tmp_path / "setstudy_log.csv"
    report = {
        "date": "2025-06-02",
        "calls": [
            {
                "post_id": "abc",
                "post_ts": POST_TS,
                "direction": "long",
                "level": 5000.0,
                "entry_type": "realtime_entry",
                "post_price": 5005.0,
                "touched_level": True,
                "mfe_points": 10.0,
                "mae_points": 2.0,
                "direction_worked": True,
                "near_node": False,
            }
        ],
    }
    assert append_log_rows(report, log_path) == 1
    assert append_log_rows(report, log_path) == 0
    text = log_path.read_text(encoding="utf-8")
    assert text.count("abc") == 1
