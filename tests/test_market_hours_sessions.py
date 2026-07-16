"""Session-boundary helpers: is_after_close / last_session_close."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from trading.market_hours import is_after_close, last_session_close

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
