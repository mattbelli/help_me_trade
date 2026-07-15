"""US equities regular-session market hours (v1 — weekdays only, no holiday calendar)."""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

_ET = ZoneInfo("America/New_York")
_SESSION_OPEN = (9, 30)
_SESSION_CLOSE = (16, 0)


def is_market_open(now: datetime | None = None) -> bool:
    """True during US equities regular session: Mon–Fri 09:30–16:00 ET."""
    # TODO: Alpaca /v2/clock for holidays
    t = now.astimezone(_ET) if now is not None else datetime.now(_ET)
    if t.weekday() >= 5:
        return False
    open_minutes = _SESSION_OPEN[0] * 60 + _SESSION_OPEN[1]
    close_minutes = _SESSION_CLOSE[0] * 60 + _SESSION_CLOSE[1]
    now_minutes = t.hour * 60 + t.minute
    return open_minutes <= now_minutes < close_minutes


def is_after_close(now: datetime) -> bool:
    """True once the regular session on `now`'s ET date is history.

    Not the complement of is_market_open(): pre-open is False here. Weekends
    read as after-close — there was no session to still be waiting on.
    """
    # TODO: Alpaca /v2/clock for holidays
    t = now.astimezone(_ET)
    if t.weekday() >= 5:
        return True
    close_minutes = _SESSION_CLOSE[0] * 60 + _SESSION_CLOSE[1]
    return t.hour * 60 + t.minute >= close_minutes


def last_session_close(now: datetime) -> datetime:
    """Instant of the most recent regular-session close at or before `now` (ET).

    Anything that traded before this instant has been superseded by that close,
    so it can no longer describe the current price.
    """
    # TODO: Alpaca /v2/clock for holidays
    t = now.astimezone(_ET)
    close = t.replace(
        hour=_SESSION_CLOSE[0], minute=_SESSION_CLOSE[1], second=0, microsecond=0
    )
    if close > t:
        close -= timedelta(days=1)
    while close.weekday() >= 5:
        close -= timedelta(days=1)
    return close
