"""Session-boundary helpers: is_after_close / last_session_close."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from trading.market_hours import is_after_close, is_market_open, last_session_close

ET = ZoneInfo("America/New_York")


def _et(y: int, m: int, d: int, hh: int, mm: int = 0) -> datetime:
    return datetime(y, m, d, hh, mm, tzinfo=ET)


# 2026-07-10 Fri, 07-11 Sat, 07-12 Sun, 07-13 Mon, 07-14 Tue


def test_is_after_close_weekday_boundary() -> None:
    assert not is_after_close(_et(2026, 7, 14, 15, 59))
    assert is_after_close(_et(2026, 7, 14, 16, 0))
    assert is_after_close(_et(2026, 7, 14, 20, 14))


def test_is_after_close_is_not_the_inverse_of_open() -> None:
    """Pre-open is NOT after-close — the session is still ahead."""
    assert not is_after_close(_et(2026, 7, 14, 4, 0))
    assert not is_after_close(_et(2026, 7, 14, 9, 29))


def test_is_after_close_weekend() -> None:
    assert is_after_close(_et(2026, 7, 11, 10, 0))
    assert is_after_close(_et(2026, 7, 12, 22, 0))


def test_last_session_close_same_day_after_close() -> None:
    """The NTSK case: 20:14 ET Tue → today's 16:00 close."""
    assert last_session_close(_et(2026, 7, 14, 20, 14)) == _et(2026, 7, 14, 16)


def test_last_session_close_intraday_is_previous_day() -> None:
    assert last_session_close(_et(2026, 7, 14, 10, 0)) == _et(2026, 7, 13, 16)


def test_last_session_close_walks_back_over_weekend() -> None:
    assert last_session_close(_et(2026, 7, 13, 8, 0)) == _et(2026, 7, 10, 16)
    assert last_session_close(_et(2026, 7, 12, 22, 0)) == _et(2026, 7, 10, 16)
    assert last_session_close(_et(2026, 7, 11, 2, 0)) == _et(2026, 7, 10, 16)


def test_last_session_close_accepts_utc_input() -> None:
    """Callers pass datetime.now(UTC); the ET conversion is the helper's job."""
    utc = _et(2026, 7, 14, 20, 14).astimezone(ZoneInfo("UTC"))
    assert last_session_close(utc) == _et(2026, 7, 14, 16)


# Holiday awareness — 2026-01-19 is MLK Day (a Monday); 01-20 Tue is a session.


def test_is_market_open_false_on_holiday_during_session_hours() -> None:
    assert not is_market_open(_et(2026, 1, 19, 11, 0))  # MLK Day, would-be mid-session
    assert is_market_open(_et(2026, 1, 20, 11, 0))  # next session, open


def test_is_after_close_true_on_holiday() -> None:
    assert is_after_close(_et(2026, 1, 19, 11, 0))  # no session to wait on


def test_last_session_close_walks_back_over_holiday() -> None:
    # Tue 2026-01-20 pre-open → skips MLK Mon 01-19 back to Fri 01-16 close.
    assert last_session_close(_et(2026, 1, 20, 8, 0)) == _et(2026, 1, 16, 16)


# Half-day early closes — 2026-11-27 (Friday after Thanksgiving) closes 13:00 ET.
# The board card market-hours-half-day-early-close: a phantom-open afternoon let
# the earnings exit fire post-close and queue a day-TIF order into the print day.


def test_session_close_time_half_day_vs_full_day() -> None:
    from datetime import date

    from trading.market_hours import session_close_time

    assert session_close_time(date(2026, 11, 27)) == (13, 0)
    assert session_close_time(date(2026, 12, 24)) == (13, 0)  # Christmas Eve, a Thursday
    assert session_close_time(date(2026, 11, 30)) == (16, 0)  # the following Monday


def test_is_market_open_half_day_afternoon_is_closed() -> None:
    assert is_market_open(_et(2026, 11, 27, 12, 59))
    assert not is_market_open(_et(2026, 11, 27, 13, 0))
    assert not is_market_open(_et(2026, 11, 27, 14, 30))  # the phantom-open window


def test_is_after_close_flips_at_1300_on_half_day() -> None:
    assert not is_after_close(_et(2026, 11, 27, 12, 59))
    assert is_after_close(_et(2026, 11, 27, 13, 0))


def test_last_session_close_on_half_day_is_1300() -> None:
    assert last_session_close(_et(2026, 11, 27, 14, 0)) == _et(2026, 11, 27, 13)
    # Monday pre-open walks back to Friday's EARLY close, not a phantom 16:00.
    assert last_session_close(_et(2026, 11, 30, 8, 0)) == _et(2026, 11, 27, 13)


def test_market_session_half_day_afternoon_is_post_not_regular() -> None:
    """market_clock must stay in lockstep with is_market_open — a "regular"
    label 13:00-16:00 on a half-day strands equity price alerts (the ext-quote
    backstop only engages outside "regular")."""
    from brokers.market_clock import market_session

    assert market_session(_et(2026, 11, 27, 12, 59)) == "regular"
    assert market_session(_et(2026, 11, 27, 13, 0)) == "post"
    assert market_session(_et(2026, 11, 27, 14, 30)) == "post"
    # Full days keep the 16:00 boundary.
    assert market_session(_et(2026, 11, 30, 14, 30)) == "regular"
    assert market_session(_et(2026, 11, 30, 16, 0)) == "post"
