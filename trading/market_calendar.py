"""US equities (NYSE) trading-day calendar — pure-Python, keyless, offline.

The single source of truth for "does the US stock market hold a regular session on
this ET calendar date". Weekends, the ten annual NYSE holidays (with their observed
shifts), plus the handful of ad-hoc closures that cannot be computed (national days
of mourning, disasters). No network and no brokerage account — deliberately, so no
capability is gated on a vendor login (same reason EDGAR was chosen over Schwab).

Scope: FULL-day closures only. Early-close half-days (the 13:00 ET closes around
Independence Day, the day after Thanksgiving, and Christmas Eve) are still trading
days here — correct for counting trading days, but NOT harmless: is_market_open()
reads True through the closed 13:00–16:00 tail, which defeats the earnings exit's
"fill at this session's close" guard. On a half-day the bmo lead_td_close exit
(signals/exit_evaluators.py) fires at 15:45 — after the real close — and a day-TIF
order queues to the next session's open, which for a bmo reporter is AFTER its print.
That is a genuine (rare, ~3 days/yr, PRE-EXISTING) hold-through-print path. Closing it
needs a session-relative exit window, not just this calendar — tracked separately
(board: market-hours-half-day-early-close). Half-day close *times* are the missing piece.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from functools import lru_cache

# Ad-hoc, non-computable NYSE full-day closures (national mourning, disasters).
# Append as they occur — the algorithm below covers only the recurring holidays.
_AD_HOC_CLOSURES: frozenset[date] = frozenset(
    {
        date(2012, 10, 29),  # Hurricane Sandy
        date(2012, 10, 30),  # Hurricane Sandy
        date(2018, 12, 5),  # National day of mourning — George H. W. Bush
        date(2025, 1, 9),  # National day of mourning — Jimmy Carter
    }
)

# First year each holiday became an NYSE market closure.
_MLK_FIRST_YEAR = 1998
_JUNETEENTH_FIRST_YEAR = 2022


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    """The n-th `weekday` (0=Mon) of a month, e.g. 3rd Monday of January."""
    first = date(year, month, 1)
    offset = (weekday - first.weekday()) % 7
    return first + timedelta(days=offset + 7 * (n - 1))


def _last_weekday(year: int, month: int, weekday: int) -> date:
    """The last `weekday` (0=Mon) of a month, e.g. last Monday of May."""
    nxt = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
    last = nxt - timedelta(days=1)
    return last - timedelta(days=(last.weekday() - weekday) % 7)


def _easter(year: int) -> date:
    """Gregorian Easter Sunday (anonymous Computus)."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    m = (32 + 2 * e + 2 * i - h - k) % 7
    n = (a + 11 * h + 22 * m) // 451
    month = (h + m - 7 * n + 114) // 31
    day = ((h + m - 7 * n + 114) % 31) + 1
    return date(year, month, day)


def _observed_fixed(d: date) -> date:
    """Observed date for a fixed-date holiday: Sat→prior Fri, Sun→following Mon."""
    if d.weekday() == 5:
        return d - timedelta(days=1)
    if d.weekday() == 6:
        return d + timedelta(days=1)
    return d


@lru_cache(maxsize=None)
def _nyse_holidays(year: int) -> frozenset[date]:
    """The observed NYSE full-closure dates in `year` (excluding weekends)."""
    days: set[date] = set()

    # New Year's Day. NYSE does NOT close the preceding Friday when Jan 1 falls on a
    # Saturday — only the Sunday→Monday shift applies (the one asymmetric holiday).
    nyd = date(year, 1, 1)
    if nyd.weekday() == 6:
        days.add(nyd + timedelta(days=1))
    elif nyd.weekday() < 5:
        days.add(nyd)

    if year >= _MLK_FIRST_YEAR:
        days.add(_nth_weekday(year, 1, 0, 3))  # MLK Day — 3rd Monday of January
    days.add(_nth_weekday(year, 2, 0, 3))  # Washington's Birthday — 3rd Monday of Feb
    days.add(_easter(year) - timedelta(days=2))  # Good Friday
    days.add(_last_weekday(year, 5, 0))  # Memorial Day — last Monday of May
    if year >= _JUNETEENTH_FIRST_YEAR:
        days.add(_observed_fixed(date(year, 6, 19)))  # Juneteenth
    days.add(_observed_fixed(date(year, 7, 4)))  # Independence Day
    days.add(_nth_weekday(year, 9, 0, 1))  # Labor Day — 1st Monday of September
    days.add(_nth_weekday(year, 11, 3, 4))  # Thanksgiving — 4th Thursday of November
    days.add(_observed_fixed(date(year, 12, 25)))  # Christmas

    return frozenset(days)


def is_trading_day(d: date) -> bool:
    """True if the NYSE holds a regular session on ET calendar date `d`.

    Weekends, recurring holidays (observed), and known ad-hoc closures are False.
    """
    if d.weekday() >= 5:
        return False
    if d in _AD_HOC_CLOSURES:
        return False
    return d not in _nyse_holidays(d.year)


def monthly_opex_date(year: int, month: int) -> date:
    """The monthly equity/index OpEx session: 3rd Friday, or the preceding
    trading day when that Friday is a holiday (exchange rule for expirations)."""
    d = _nth_weekday(year, month, 4, 3)
    while not is_trading_day(d):
        d -= timedelta(days=1)
    return d


def is_monthly_opex(d: date) -> bool:
    """True if `d` is the monthly OpEx session (see monthly_opex_date)."""
    return d == monthly_opex_date(d.year, d.month)


def is_early_close(d: date) -> bool:
    """True if `d` is an NYSE half-day (13:00 ET close).

    The recurring three: July 3 (only when it trades — July 4 on a Saturday makes
    July 3 the observed full holiday, which is_trading_day already excludes), the
    day after Thanksgiving, and Christmas Eve when it falls Mon-Thu (a Friday
    Dec 24 is the observed full holiday for a Saturday Christmas). This names the
    DATES only; session times stay out of scope here — is_market_open() still
    reads True through a half-day's closed afternoon (board:
    market-hours-half-day-early-close). Consumers like the index-pressure close
    window use this to avoid recording a 15:30-16:00 window that didn't exist.
    """
    if not is_trading_day(d):
        return False
    if d.month == 7 and d.day == 3:
        return True
    if d == _nth_weekday(d.year, 11, 3, 4) + timedelta(days=1):
        return True
    return d.month == 12 and d.day == 24


def trading_days(start: date, end: date) -> list[date]:
    """Inclusive [start, end] NYSE sessions."""
    out: list[date] = []
    d = start
    while d <= end:
        if is_trading_day(d):
            out.append(d)
        d += timedelta(days=1)
    return out


def trading_days_between(start: date, end: date) -> int:
    """Count of sessions in (start, end] — artifact age vs today.

    ``trading_days_between(Mon, Thu) == 3`` (Tue, Wed, Thu). Equal dates → 0.
    """
    if end <= start:
        return 0
    return len(trading_days(start + timedelta(days=1), end))


def last_closed_session(now_et: datetime | None = None) -> date:
    """Most recent RTH session that has already closed.

    A session is closed after 16:05 ET on a trading day (5 min past the last
    15:55 bar). Before that, yesterday's session. Early-close days still close
    well before 16:05.
    """
    from zoneinfo import ZoneInfo

    et = ZoneInfo("America/New_York")
    now = now_et or datetime.now(et)
    d = now.date()
    closed = now.hour > 16 or (now.hour == 16 and now.minute >= 5)
    if not (is_trading_day(d) and closed):
        d -= timedelta(days=1)
    while not is_trading_day(d):
        d -= timedelta(days=1)
    return d
