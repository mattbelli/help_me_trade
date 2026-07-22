"""US equities regular-session market hours.

Holiday awareness comes from trading.market_calendar (full-day closures), and
half-day early closes (13:00 ET) are honored here via session_close_time() —
is_market_open/is_after_close/last_session_close all read the ACTUAL close for
the date, so a half-day afternoon is closed, not phantom-open (board:
market-hours-half-day-early-close).
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from trading.market_calendar import is_early_close, is_trading_day

_ET = ZoneInfo("America/New_York")
_SESSION_OPEN = (9, 30)
_SESSION_CLOSE = (16, 0)
_SESSION_EARLY_CLOSE = (13, 0)


def session_close_time(d: date) -> tuple[int, int]:
    """Regular-session close for ET date `d`: 13:00 on NYSE half-days, else 16:00.

    Callers that need "when does TODAY's session actually end" must use this,
    never the _SESSION_CLOSE constant — the constant is the full-day close only.
    """
    return _SESSION_EARLY_CLOSE if is_early_close(d) else _SESSION_CLOSE


def is_market_open(now: datetime | None = None) -> bool:
    """True during US equities regular session: a trading day, 09:30 ET to the
    day's actual close (16:00, or 13:00 on a half-day)."""
    t = now.astimezone(_ET) if now is not None else datetime.now(_ET)
    if not is_trading_day(t.date()):
        return False
    close_h, close_m = session_close_time(t.date())
    open_minutes = _SESSION_OPEN[0] * 60 + _SESSION_OPEN[1]
    close_minutes = close_h * 60 + close_m
    now_minutes = t.hour * 60 + t.minute
    return open_minutes <= now_minutes < close_minutes


def is_after_close(now: datetime) -> bool:
    """True once the regular session on `now`'s ET date is history.

    Not the complement of is_market_open(): pre-open is False here. Weekends and
    holidays read as after-close — there was no session to still be waiting on.
    Half-days flip at 13:00 ET, the day's actual close.
    """
    t = now.astimezone(_ET)
    if not is_trading_day(t.date()):
        return True
    close_h, close_m = session_close_time(t.date())
    return t.hour * 60 + t.minute >= close_h * 60 + close_m


def last_session_close(now: datetime) -> datetime:
    """Instant of the most recent regular-session close at or before `now` (ET).

    Anything that traded before this instant has been superseded by that close,
    so it can no longer describe the current price. Uses each candidate DAY's
    actual close (13:00 on half-days), not the 16:00 constant.
    """
    t = now.astimezone(_ET)
    d = t.date()
    while True:
        if is_trading_day(d):
            close_h, close_m = session_close_time(d)
            close = datetime(d.year, d.month, d.day, close_h, close_m, tzinfo=_ET)
            if close <= t:
                return close
        d -= timedelta(days=1)
