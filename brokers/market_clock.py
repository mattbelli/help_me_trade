"""Neutral US-equities market-session clock.

Moved out of ``api/main.py`` (the "nothing new lands in main.py" ratchet — moving
OUT is encouraged) so the market-data stream can gate ticks by session without
importing the API layer. ``api/main.py`` re-imports :func:`market_session` under
its old private name ``_market_session`` so its callers don't churn.
"""

from __future__ import annotations

from datetime import datetime

from trading.market_hours import _ET


def market_session() -> str:
    """Return the current US equities market session label based on ET clock.

    pre:       04:00–09:30 ET (weekday)
    regular:   09:30–16:00 ET (weekday)
    post:      16:00–20:00 ET (weekday)
    overnight: the 24/5 session — Sun 20:00 ET → Fri 20:00 ET, outside pre/regular/post.
               Many liquid names trade overnight and Schwab's `extended` block is live then.
    closed:    Fri 20:00 ET → Sun 20:00 ET (weekend).
    """
    now = datetime.now(_ET)
    wd = now.weekday()  # Mon=0 .. Sun=6
    minutes = now.hour * 60 + now.minute
    if wd <= 4:  # weekday RTH/pre/post windows
        if 4 * 60 <= minutes < 9 * 60 + 30:
            return "pre"
        if 9 * 60 + 30 <= minutes < 16 * 60:
            return "regular"
        if 16 * 60 <= minutes < 20 * 60:
            return "post"
    # Weekend closure: Fri after 20:00, all Saturday, Sunday before 20:00.
    if (wd == 4 and minutes >= 20 * 60) or wd == 5 or (wd == 6 and minutes < 20 * 60):
        return "closed"
    return "overnight"
