"""Fusion Phase C4 — minute-bar grading for the reason lane (the Grading Law).

Event-driven intraday plan origins (``reason_v1``) grade on MINUTE bars so a
same-day target followed by an overnight gap through the stop sequences
correctly — a game daily bars physically cannot referee. Missing minute data
falls back to the daily sweep with ``grading_resolution`` flagged. All fetches
are monkeypatched: no network.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

import pytest

from config.idea_engine import GRADING_TIEBREAK
from db.ai_tables import init_ai_tables
from db.idea_outcomes import (
    init_idea_outcomes_tables,
    list_open,
    list_recent_resolved,
    register_idea,
)
from research.idea_tracker import sweep_open_ideas

_ET = ZoneInfo("America/New_York")

# 10:41 ET on 2026-01-06 (a Tuesday) — an event-driven intraday mint, PRE-close,
# so first_evaluable would be the same session and only the intraday SEQUENCE
# distinguishes the minute grade from the daily one.
_AS_OF = "2026-01-06T15:41:00+00:00"


@pytest.fixture
def idea_db(tmp_path):
    db = tmp_path / "ideas.db"
    init_ai_tables(db)
    init_idea_outcomes_tables(db)
    return db


def _et(year: int, month: int, day: int, hour: int, minute: int) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=_ET)


def _mbar(dt: datetime, *, low: float, high: float, close: float | None = None) -> SimpleNamespace:
    """A minute bar with a UTC 'Z' timestamp carrying HH:MM (Alpaca's shape)."""
    return SimpleNamespace(
        time=dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        open=(low + high) / 2,
        low=low,
        high=high,
        close=close if close is not None else (low + high) / 2,
        volume=100_000,
    )


def _dbar(day: date, *, low: float, high: float, close: float | None = None) -> SimpleNamespace:
    """A daily bar (date-only 'time') for the daily-fallback paths."""
    return SimpleNamespace(
        time=day.isoformat(),
        open=(low + high) / 2,
        low=low,
        high=high,
        close=close if close is not None else (low + high) / 2,
        volume=1_000_000,
    )


def _register_reason(
    db,
    *,
    symbol: str = "AAPL",
    entry_low: str = "100",
    entry_high: str = "105",
    stop: str = "95",
    targets: list[str] | None = None,
    horizon: str = "2d",
    as_of: str = _AS_OF,
    direction: str = "long",
) -> int:
    idea_id = register_idea(
        origin="reason_v1",
        signal_family="fusion",
        symbol=symbol,
        direction=direction,
        entry_zone={"low": entry_low, "high": entry_high},
        stop=stop,
        targets=targets or ["110"],
        grading_basis="plan",
        plan_horizon=horizon,
        as_of=as_of,
        db_path=db,
    )
    assert isinstance(idea_id, int)
    return idea_id


def _no_daily(symbol, since):  # SPY benchmark fetch → empty, never the network
    return []


# --------------------------------------------------------------------------- #
# 1. The motivating case: same-day target on minute bars → won, resolved today.
# --------------------------------------------------------------------------- #
def test_minute_intraday_target_hit_same_day_is_won(idea_db) -> None:
    _register_reason(idea_db)
    bars = [
        _mbar(_et(2026, 1, 6, 10, 45), low=101, high=104),  # zone touch → trigger
        _mbar(_et(2026, 1, 6, 11, 30), low=104, high=106),  # drift, no stop/target
        _mbar(_et(2026, 1, 6, 14, 0), low=108, high=111),   # target 110 → WON same day
    ]
    with (
        patch("research.idea_tracker.fetch_minute_bars", return_value=bars),
        patch("research.idea_tracker.fetch_daily_bars_since", side_effect=_no_daily),
    ):
        counts = sweep_open_ideas(now=datetime(2026, 1, 6, 20, 0, 0), db_path=idea_db)

    assert counts["won"] == 1
    row = list_recent_resolved(db_path=idea_db)[0]
    assert row["status"] == "won"
    assert row["grading_resolution"] == "minute"
    assert row["exit_reason"] == "target"
    assert row["resolution_price"] == "110"
    # Resolved on the SAME session it was minted — the whole point (a daily bar
    # cannot order the morning trigger against the afternoon target).
    assert row["triggered_at"] == "2026-01-06"
    assert row["resolved_at"] == "2026-01-06"
    assert not list_open(db_path=idea_db)


# --------------------------------------------------------------------------- #
# 2. Gap-through-stop: no target day 1, day-2 open gaps below the stop → lost.
# --------------------------------------------------------------------------- #
def test_minute_overnight_gap_through_stop_is_lost(idea_db) -> None:
    _register_reason(idea_db, horizon="3d")
    bars = [
        _mbar(_et(2026, 1, 6, 10, 45), low=101, high=104),  # day-1 trigger
        _mbar(_et(2026, 1, 6, 15, 30), low=103, high=106),  # day-1 drift, no tgt/stop
        _mbar(_et(2026, 1, 7, 9, 30), low=90, high=93),     # day-2 OPEN gaps below stop 95
    ]
    with (
        patch("research.idea_tracker.fetch_minute_bars", return_value=bars),
        patch("research.idea_tracker.fetch_daily_bars_since", side_effect=_no_daily),
    ):
        counts = sweep_open_ideas(now=datetime(2026, 1, 7, 20, 0, 0), db_path=idea_db)

    assert counts["lost"] == 1
    row = list_recent_resolved(db_path=idea_db)[0]
    assert row["status"] == "lost"
    assert row["grading_resolution"] == "minute"
    assert row["exit_reason"] == "stop"
    assert row["resolved_at"] == "2026-01-07"  # graded at the day-2 open bar
    assert row["resolution_price"] == "90"     # the long stop-out low


# --------------------------------------------------------------------------- #
# 3. Same-minute stop+target straddle → stop wins (pessimistic tie-break).
# --------------------------------------------------------------------------- #
def test_minute_same_bar_stop_and_target_stop_wins(idea_db) -> None:
    assert GRADING_TIEBREAK == "stop"
    _register_reason(idea_db, stop="98", targets=["110"])
    bars = [
        _mbar(_et(2026, 1, 6, 10, 45), low=101, high=103),  # trigger
        _mbar(_et(2026, 1, 6, 13, 0), low=97, high=112),    # stop 98 AND target 110
    ]
    with (
        patch("research.idea_tracker.fetch_minute_bars", return_value=bars),
        patch("research.idea_tracker.fetch_daily_bars_since", side_effect=_no_daily),
    ):
        counts = sweep_open_ideas(now=datetime(2026, 1, 6, 20, 0, 0), db_path=idea_db)

    assert counts["lost"] == 1
    row = list_recent_resolved(db_path=idea_db)[0]
    assert row["status"] == "lost"
    assert row["exit_reason"] == "stop"
    assert row["grading_resolution"] == "minute"
    assert row["resolution_price"] == "97"  # bar low — the long stop-out


# --------------------------------------------------------------------------- #
# 4. Never-filled plan → the daily path's exact no-fill vocabulary, minute-stamped.
# --------------------------------------------------------------------------- #
def test_minute_never_filled_expiry_is_no_fill(idea_db) -> None:
    _register_reason(idea_db, horizon="2d")  # expiry_date = 2026-01-08
    bars = [
        _mbar(_et(2026, 1, 6, 10, 45), low=120, high=122),  # well above the zone
        _mbar(_et(2026, 1, 6, 14, 0), low=121, high=123),   # never touched
    ]
    with (
        patch("research.idea_tracker.fetch_minute_bars", return_value=bars),
        patch("research.idea_tracker.fetch_daily_bars_since", side_effect=_no_daily),
    ):
        counts = sweep_open_ideas(now=datetime(2026, 1, 9, 16, 0, 0), db_path=idea_db)

    assert counts["expired"] == 1
    row = list_recent_resolved(db_path=idea_db)[0]
    assert row["status"] == "expired"
    assert row["fill_status"] == "no_fill"
    assert row["exit_reason"] == "time"
    assert row["grading_resolution"] == "minute"
    assert row["resolved_at"] == "2026-01-08"  # horizon end (as_of + 2 trading days)
    # No fill = no position = no return: benchmark legs stay NULL, never phantom.
    assert row["ret_pct"] is None
    assert row["spy_ret_pct"] is None
    assert row["alpha_pct"] is None


# --------------------------------------------------------------------------- #
# 5. Minute fetch None → daily fallback grades it, stamped grading_resolution='daily'.
# --------------------------------------------------------------------------- #
def test_minute_fetch_none_falls_back_to_daily(idea_db) -> None:
    _register_reason(idea_db, horizon="5d")
    start = date(2026, 1, 6)
    daily = [
        _dbar(start + timedelta(days=1), low=102, high=103),  # trigger 2026-01-07
        _dbar(start + timedelta(days=2), low=104, high=111),  # win @110 2026-01-08
    ]
    with (
        patch("research.idea_tracker.fetch_minute_bars", return_value=None),
        patch("research.idea_tracker.fetch_daily_bars_since", return_value=daily),
    ):
        sweep_open_ideas(now=datetime(2026, 1, 8, 12, 0, 0), db_path=idea_db)
        counts = sweep_open_ideas(now=datetime(2026, 1, 9, 12, 0, 0), db_path=idea_db)

    assert counts["won"] == 1
    row = list_recent_resolved(db_path=idea_db)[0]
    assert row["status"] == "won"
    # The fallback flag law: a minute-graded origin resolved on the daily path
    # reads 'daily', never 'minute'.
    assert row["grading_resolution"] == "daily"


# --------------------------------------------------------------------------- #
# 6. A non-minute origin with minute data available still grades on the DAILY path.
# --------------------------------------------------------------------------- #
def test_non_minute_origin_never_enters_minute_path(idea_db) -> None:
    register_idea(
        origin="convergence",
        signal_family="flow_conv",
        symbol="AAPL",
        direction="long",
        entry_zone={"low": "100", "high": "105"},
        stop="95",
        targets=["110"],
        as_of=_AS_OF,
        db_path=idea_db,
    )
    start = date(2026, 1, 6)
    daily = [
        _dbar(start + timedelta(days=1), low=102, high=103),
        _dbar(start + timedelta(days=2), low=104, high=111),
    ]
    minute_mock = MagicMock()  # would raise if walked; asserts it is never called
    with (
        patch("research.idea_tracker.fetch_minute_bars", minute_mock),
        patch("research.idea_tracker.fetch_daily_bars_since", return_value=daily),
    ):
        sweep_open_ideas(now=datetime(2026, 1, 8, 12, 0, 0), db_path=idea_db)
        counts = sweep_open_ideas(now=datetime(2026, 1, 9, 12, 0, 0), db_path=idea_db)

    assert counts["won"] == 1
    assert minute_mock.called is False  # the minute branch was never entered
    row = list_recent_resolved(db_path=idea_db)[0]
    assert row["status"] == "won"
    assert row["grading_resolution"] == "daily"  # untouched by the minute branch


# --------------------------------------------------------------------------- #
# 7. MFE/MAE populated from minute bars on the minute path.
# --------------------------------------------------------------------------- #
def test_minute_excursions_from_minute_bars(idea_db) -> None:
    _register_reason(idea_db, horizon="2d")  # entry mid 102.5, expiry 2026-01-08
    bars = [
        _mbar(_et(2026, 1, 6, 10, 45), low=102, high=103),  # trigger
        _mbar(_et(2026, 1, 6, 11, 30), low=103, high=108),  # favorable → MFE from 108
        _mbar(_et(2026, 1, 6, 13, 0), low=99, high=104),    # adverse → MAE from 99
    ]
    with (
        patch("research.idea_tracker.fetch_minute_bars", return_value=bars),
        patch("research.idea_tracker.fetch_daily_bars_since", side_effect=_no_daily),
    ):
        counts = sweep_open_ideas(now=datetime(2026, 1, 9, 16, 0, 0), db_path=idea_db)

    assert counts["expired"] == 1
    row = list_recent_resolved(db_path=idea_db)[0]
    assert row["status"] == "expired"
    assert row["fill_status"] == "filled"  # triggered then timed out
    assert row["grading_resolution"] == "minute"
    assert row["mfe_pct"] == pytest.approx((108 - 102.5) / 102.5 * 100, rel=1e-4)
    assert row["mae_pct"] == pytest.approx((102.5 - 99) / 102.5 * 100, rel=1e-4)


# --------------------------------------------------------------------------- #
# 8. Extended-hours prints never grade: Alpaca minute bars include pre/post-
#    market, and a thin 4:15am wick through the stop is not a tradeable
#    stop-out — only RTH bars referee the plan.
# --------------------------------------------------------------------------- #
def test_minute_extended_hours_bars_never_grade(idea_db) -> None:
    _register_reason(idea_db, horizon="3d")
    bars = [
        _mbar(_et(2026, 1, 6, 10, 45), low=101, high=104),  # RTH trigger
        _mbar(_et(2026, 1, 7, 4, 15), low=90, high=93),     # pre-market wick through stop 95
        _mbar(_et(2026, 1, 7, 9, 30), low=96, high=99),     # RTH open back above the stop
    ]
    with (
        patch("research.idea_tracker.fetch_minute_bars", return_value=bars),
        patch("research.idea_tracker.fetch_daily_bars_since", side_effect=_no_daily),
    ):
        counts = sweep_open_ideas(now=datetime(2026, 1, 7, 20, 0, 0), db_path=idea_db)

    assert counts["lost"] == 0
    (row,) = list_open(db_path=idea_db)
    assert row["status"] == "triggered"  # still open — the wick graded nothing
    # Excursions read RTH bars only: MAE from the 96 open low, never the 90 wick.
    assert row["mae_pct"] == pytest.approx((102.5 - 96) / 102.5 * 100, rel=1e-4)
