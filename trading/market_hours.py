"""US equities regular-session market hours.

Holiday awareness comes from trading.market_calendar (full-day closures only; see
its docstring for the half-day non-goal).
"""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from trading.market_calendar import is_trading_day

_ET = ZoneInfo("America/New_York")
_SESSION_OPEN = (9, 30)
_SESSION_CLOSE = (16, 0)


def is_market_open(now: datetime | None = None) -> bool:
    """True during US equities regular session: a trading day, 09:30–16:00 ET."""
    t = now.astimezone(_ET) if now is not None else datetime.now(_ET)
    if not is_trading_day(t.date()):
        return False
    open_minutes = _SESSION_OPEN[0] * 60 + _SESSION_OPEN[1]
    close_minutes = _SESSION_CLOSE[0] * 60 + _SESSION_CLOSE[1]
    now_minutes = t.hour * 60 + t.minute
    return open_minutes <= now_minutes < close_minutes


def is_after_close(now: datetime) -> bool:
    """True once the regular session on `now`'s ET date is history.

    Not the complement of is_market_open(): pre-open is False here. Weekends and
    holidays read as after-close — there was no session to still be waiting on.
    """
    t = now.astimezone(_ET)
    if not is_trading_day(t.date()):
        return True
    close_minutes = _SESSION_CLOSE[0] * 60 + _SESSION_CLOSE[1]
    return t.hour * 60 + t.minute >= close_minutes


def last_session_close(now: datetime) -> datetime:
    """Instant of the most recent regular-session close at or before `now` (ET).

    Anything that traded before this instant has been superseded by that close,
    so it can no longer describe the current price.
    """
    t = now.astimezone(_ET)
    close = t.replace(
        hour=_SESSION_CLOSE[0], minute=_SESSION_CLOSE[1], second=0, microsecond=0
    )
    if close > t:
        close -= timedelta(days=1)
    while not is_trading_day(close.date()):
        close -= timedelta(days=1)
    return close
