"""Neutral US-equities market-session clock.

Moved out of ``api/main.py`` (the "nothing new lands in main.py" ratchet — moving
OUT is encouraged) so the market-data stream can gate ticks by session without
importing the API layer. ``api/main.py`` re-imports :func:`market_session` under
its old private name ``_market_session`` so its callers don't churn.
"""

from __future__ import annotations

from datetime import datetime

from trading.market_hours import _ET, session_close_time


def market_session(now: datetime | None = None) -> str:
    """Return the current US equities market session label based on ET clock.

    pre:       04:00–09:30 ET (weekday)
    regular:   09:30 ET to the day's actual close (16:00, or 13:00 on a half-day)
    post:      the close → 20:00 ET (weekday)
    overnight: the 24/5 session — Sun 20:00 ET → Fri 20:00 ET, outside pre/regular/post.
               Many liquid names trade overnight and Schwab's `extended` block is live then.
    closed:    Fri 20:00 ET → Sun 20:00 ET (weekend).

    Half-day awareness must stay in lockstep with is_market_open(): a "regular"
    label after the real close strands equity price alerts — the stream gate is
    is_market_open() and the ext-quote backstop only engages outside "regular".
    """
    t = now.astimezone(_ET) if now is not None else datetime.now(_ET)
    wd = t.weekday()  # Mon=0 .. Sun=6
    minutes = t.hour * 60 + t.minute
    if wd <= 4:  # weekday RTH/pre/post windows
        close_h, close_m = session_close_time(t.date())
        close_minutes = close_h * 60 + close_m
        if 4 * 60 <= minutes < 9 * 60 + 30:
            return "pre"
        if 9 * 60 + 30 <= minutes < close_minutes:
            return "regular"
        if close_minutes <= minutes < 20 * 60:
            return "post"
    # Weekend closure: Fri after 20:00, all Saturday, Sunday before 20:00.
    if (wd == 4 and minutes >= 20 * 60) or wd == 5 or (wd == 6 and minutes < 20 * 60):
        return "closed"
    return "overnight"
