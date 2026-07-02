"""Globex (CME futures) session-boundary primitive.

The session for date D runs 18:00 ET (D-1) -> 17:00 ET (D) with a daily
17:00-18:00 ET maintenance break. Boundary bugs here silently corrupt both
futures RVOL and futures session-node levels, so pin the edges hard.
"""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

from research.intraday_profile import (
    GLOBEX_N_BUCKETS,
    globex_bucket_index,
    globex_session_date,
    segment_globex_sessions,
)

_NY = ZoneInfo("America/New_York")


def _et(y, mo, d, h, mi) -> datetime:
    return datetime(y, mo, d, h, mi, tzinfo=_NY)


# --- globex_session_date -----------------------------------------------------

def test_session_open_1800_rolls_to_next_day():
    # 18:00 ET Mon Jun 29 opens the Tuesday Jun 30 session.
    assert globex_session_date(_et(2026, 6, 29, 18, 0)) == date(2026, 6, 30)


def test_evening_belongs_to_next_session():
    assert globex_session_date(_et(2026, 6, 29, 23, 0)) == date(2026, 6, 30)


def test_morning_belongs_to_same_day_session():
    # 02:00 ET Tue is still the Tuesday session (opened 18:00 Mon).
    assert globex_session_date(_et(2026, 6, 30, 2, 0)) == date(2026, 6, 30)
    assert globex_session_date(_et(2026, 6, 30, 9, 30)) == date(2026, 6, 30)
    assert globex_session_date(_et(2026, 6, 30, 16, 55)) == date(2026, 6, 30)


def test_maintenance_break_has_no_session():
    assert globex_session_date(_et(2026, 6, 30, 17, 0)) is None  # break starts 17:00
    assert globex_session_date(_et(2026, 6, 30, 17, 30)) is None
    assert globex_session_date(_et(2026, 6, 30, 17, 59)) is None
    # 18:00 is the NEXT session, not the break.
    assert globex_session_date(_et(2026, 6, 30, 18, 0)) == date(2026, 7, 1)


# --- globex_bucket_index -----------------------------------------------------

def test_bucket_open_is_zero():
    assert globex_bucket_index(_et(2026, 6, 29, 18, 0)) == 0


def test_bucket_last_before_close():
    # 16:55 ET = final 5-min bucket; 17:00 = break = None.
    assert globex_bucket_index(_et(2026, 6, 30, 16, 55)) == GLOBEX_N_BUCKETS - 1
    assert globex_bucket_index(_et(2026, 6, 30, 17, 0)) is None
    assert globex_bucket_index(_et(2026, 6, 30, 17, 30)) is None


def test_bucket_rth_open_offset():
    # 09:30 ET is 15.5h into a session that opened 18:00 -> bucket 186.
    assert globex_bucket_index(_et(2026, 6, 30, 9, 30)) == 186


def test_bucket_count_covers_23h():
    assert GLOBEX_N_BUCKETS == 276  # 23h * 12 buckets/h


# --- segment_globex_sessions -------------------------------------------------

def _bar(iso_utc: str) -> dict:
    # June = EDT (UTC-4); only "t" matters for segmentation.
    return {"t": iso_utc, "o": 1.0, "h": 1.0, "l": 1.0, "c": 1.0, "v": 1.0}


def test_segment_groups_overnight_with_next_day():
    bars = [
        _bar("2026-06-29T22:00:00Z"),  # 18:00 EDT Mon -> Tue session
        _bar("2026-06-30T06:00:00Z"),  # 02:00 EDT Tue -> Tue session
        _bar("2026-06-30T13:30:00Z"),  # 09:30 EDT Tue -> Tue session
        _bar("2026-06-30T21:30:00Z"),  # 17:30 EDT Tue -> break, dropped
        _bar("2026-06-30T22:00:00Z"),  # 18:00 EDT Tue -> Wed session
    ]
    sessions = segment_globex_sessions(bars)
    assert set(sessions) == {date(2026, 6, 30), date(2026, 7, 1)}
    assert len(sessions[date(2026, 6, 30)]) == 3  # break bar excluded
    assert len(sessions[date(2026, 7, 1)]) == 1
