"""NYSE trading-day calendar — checked against known historical closures."""

from datetime import date

import pytest

from trading.market_calendar import (
    is_trading_day,
    last_closed_session,
    trading_days,
    trading_days_between,
)

# Known NYSE full-day closures, spanning every holiday type + the asymmetric edge
# cases (observed shifts, Saturday New Year not observed, Juneteenth from 2022,
# ad-hoc closures). Each of these was a real NYSE closure.
CLOSED = [
    date(2010, 1, 1),  # New Year's (Fri)
    date(2010, 1, 18),  # MLK Day
    date(2010, 2, 15),  # Washington's Birthday
    date(2010, 4, 2),  # Good Friday
    date(2010, 5, 31),  # Memorial Day
    date(2010, 7, 5),  # Independence observed (Jul 4 = Sun)
    date(2010, 9, 6),  # Labor Day
    date(2010, 11, 25),  # Thanksgiving
    date(2010, 12, 24),  # Christmas observed (Dec 25 = Sat)
    date(2012, 10, 29),  # Hurricane Sandy
    date(2012, 10, 30),  # Hurricane Sandy
    date(2018, 12, 5),  # National day of mourning — G. H. W. Bush
    date(2020, 7, 3),  # Independence observed (Jul 4 = Sat)
    date(2021, 7, 5),  # Independence observed (Jul 4 = Sun)
    date(2021, 12, 24),  # Christmas observed (Dec 25 = Sat)
    date(2022, 6, 20),  # Juneteenth observed (Jun 19 = Sun)
    date(2022, 12, 26),  # Christmas observed (Dec 25 = Sun)
    date(2023, 1, 2),  # New Year observed (Jan 1 = Sun)
    date(2023, 6, 19),  # Juneteenth
    date(2024, 3, 29),  # Good Friday
    date(2025, 1, 9),  # National day of mourning — Jimmy Carter
    date(2026, 1, 19),  # MLK Day (the earnings-window fat-tail concern)
    date(2026, 7, 3),  # Independence observed (Jul 4 = Sat)
]

# Known OPEN days that a naive rule would get wrong.
OPEN = [
    date(2021, 12, 31),  # New Year Sat 2022 → Dec 31 stays a session
    date(2022, 1, 3),  # New Year Sat 2022 → Mon Jan 3 open (NOT observed)
    date(2019, 6, 19),  # Juneteenth not an NYSE holiday before 2022 (Wed)
    date(2026, 1, 20),  # Tuesday after MLK 2026
    date(2010, 7, 6),  # Tuesday after observed Independence
]


@pytest.mark.parametrize("d", CLOSED)
def test_known_closures_are_not_trading_days(d):
    assert is_trading_day(d) is False


@pytest.mark.parametrize("d", OPEN)
def test_known_open_days_are_trading_days(d):
    assert is_trading_day(d) is True


def test_weekends_are_not_trading_days():
    assert is_trading_day(date(2026, 7, 11)) is False  # Saturday
    assert is_trading_day(date(2026, 7, 12)) is False  # Sunday


def test_ordinary_weekday_is_a_trading_day():
    assert is_trading_day(date(2026, 7, 16)) is True  # a plain Thursday


def test_trading_days_inclusive_skips_weekend():
    days = trading_days(date(2026, 8, 7), date(2026, 8, 10))  # Fri–Mon
    assert days == [date(2026, 8, 7), date(2026, 8, 10)]


def test_trading_days_between_counts_open_interval():
    # Mon→Thu = Tue, Wed, Thu
    assert trading_days_between(date(2026, 8, 10), date(2026, 8, 13)) == 3
    assert trading_days_between(date(2026, 8, 13), date(2026, 8, 13)) == 0


def test_last_closed_session_before_open_is_prior_day():
    from datetime import datetime
    from zoneinfo import ZoneInfo

    et = ZoneInfo("America/New_York")
    # Wednesday 10:00 ET 2026-08-12 — session in progress → Tuesday
    now = datetime(2026, 8, 12, 10, 0, tzinfo=et)
    assert last_closed_session(now) == date(2026, 8, 11)
    # Wednesday 16:05 ET — today's session has closed
    now = datetime(2026, 8, 12, 16, 5, tzinfo=et)
    assert last_closed_session(now) == date(2026, 8, 12)
