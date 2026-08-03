"""Extended price levels — prior completed month + trailing 52 weeks."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest

from brokers.alpaca import StockBar
from signals.price_levels import compute_extended_levels


def _bar(day: date, high: float, low: float) -> StockBar:
    return StockBar(
        time=f"{day.isoformat()}T21:00:00Z",
        open=low + 1,
        high=high,
        low=low,
        close=low + 1,
        volume=1_000_000.0,
    )


def _today_ny() -> date:
    # Mirrors the module's own anchor so the tests never straddle a UTC/ET date flip.
    return datetime.now(UTC).astimezone().date()


def _series(days: int, *, high: float = 100.0, low: float = 90.0) -> list[StockBar]:
    """`days` calendar days of flat bars ending yesterday."""
    end = _today_ny() - timedelta(days=1)
    return [_bar(end - timedelta(days=n), high, low) for n in range(days, 0, -1)]


def test_no_bars_yields_no_levels() -> None:
    assert compute_extended_levels([]) == []


def test_prior_month_is_the_previous_completed_calendar_month() -> None:
    """PMH/PML must read the prior month, never the partial current one — the same
    'prior COMPLETED period' contract PWH/PWL carry."""
    today = _today_ny()
    first_of_month = today.replace(day=1)
    last_prior = first_of_month - timedelta(days=1)
    month_start = last_prior.replace(day=1)

    bars = []
    # Prior month: a distinctive high/low the current month must not overwrite.
    day = month_start
    while day <= last_prior:
        bars.append(_bar(day, 150.0, 120.0))
        day += timedelta(days=1)
    bars.append(_bar(month_start, 175.0, 111.0))  # the prior month's true extremes
    # Current (partial) month: wilder values that must be EXCLUDED.
    day = first_of_month
    while day <= today:
        bars.append(_bar(day, 900.0, 5.0))
        day += timedelta(days=1)

    by_name = {lvl["name"]: lvl for lvl in compute_extended_levels(bars)}

    assert by_name["PMH"]["price"] == 175.0, "current month's 900 leaked into PMH"
    assert by_name["PML"]["price"] == 111.0, "current month's 5 leaked into PML"
    assert by_name["PMH"]["kind"] == "prior_month_high"
    assert by_name["PML"]["kind"] == "prior_month_low"


def test_month_gap_does_not_weld_two_months_together() -> None:
    """History that skips the immediately-prior month must report the most recent
    month it HAS, not a range spanning the gap."""
    today = _today_ny()
    month_start = today.replace(day=1)
    # Walk back to a month ~4 months ago and put a lone bar there.
    old = month_start
    for _ in range(4):
        old = (old - timedelta(days=1)).replace(day=1)
    bars = [_bar(old, 500.0, 400.0), _bar(old + timedelta(days=1), 510.0, 390.0)]

    by_name = {lvl["name"]: lvl for lvl in compute_extended_levels(bars)}

    assert by_name["PMH"]["price"] == 510.0
    assert by_name["PML"]["price"] == 390.0


def test_52_week_window_includes_today_and_excludes_older_history() -> None:
    """52WH/52WL are TRAILING and include the still-forming bar — a breakout today
    IS the 52-week high. Anything past 365 days must drop out."""
    today = _today_ny()
    bars = [
        _bar(today - timedelta(days=400), 999.0, 1.0),  # outside the window
        *_series(360),
        _bar(today, 250.0, 60.0),  # today, still forming
    ]

    by_name = {lvl["name"]: lvl for lvl in compute_extended_levels(bars)}

    assert by_name["52WH"]["price"] == 250.0, "today's forming bar must count"
    assert by_name["52WL"]["price"] == 60.0
    assert by_name["52WH"]["kind"] == "fifty_two_week_high"
    assert 999.0 not in {lvl["price"] for lvl in by_name.values()}


def test_short_history_omits_52_week_rather_than_mislabelling_it() -> None:
    """A '52-week high' computed off 3 months is a 3-month high wearing the wrong
    name. Omission is the honest failure; the label is what the reader trusts."""
    bars = _series(90, high=100.0, low=90.0)

    names = {lvl["name"] for lvl in compute_extended_levels(bars)}

    assert "52WH" not in names and "52WL" not in names
    assert "PMH" in names, "the month level still resolves off short history"


def test_unparseable_timestamps_are_skipped_not_fatal() -> None:
    bars = [
        StockBar(time="not-a-date", open=1, high=2, low=0, close=1, volume=1.0),
        *_series(40),
    ]

    assert compute_extended_levels(bars), "one bad bar must not sink the block"


@pytest.mark.parametrize("count", [1, 5])
def test_tiny_history_never_raises(count: int) -> None:
    compute_extended_levels(_series(count))
