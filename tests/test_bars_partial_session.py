"""The daily-bar store holds COMPLETED sessions only.

Pins the 2026-07-30 incident: a mid-session fetch returns a partial bar for the
open day (a couple of hours of volume, an intraday print as the "close"), and
``INSERT OR REPLACE`` froze it as that day's final bar. Nothing revisited it —
``_needs_fetch`` saw the date covered — so 87 of 90 cached symbols carried a
wrong 2026-07-24 close (MU 913.66 vs the true 920.95) for six days, which is
what sent the blind book seats to the web for OHLC we already had.
"""

from __future__ import annotations

from datetime import date

import pytest

from research.bars import _load_cached, get_daily_bars


def _bar(d: str, close: float, volume: float) -> dict:
    return {"d": d, "o": close, "h": close, "l": close, "c": close, "v": volume}


@pytest.fixture
def bars_db(tmp_path):
    return tmp_path / "bars.db"


def test_open_sessions_bar_is_never_stored(monkeypatch, bars_db) -> None:
    """Thursday mid-session: the source offers a partial Thursday bar. It must
    neither be returned nor written — the store would keep it forever."""
    monkeypatch.setattr("research.bars._last_complete_session", lambda now=None: date(2026, 7, 29))
    fetched = [
        _bar("2026-07-28", 100.0, 50_000_000),
        _bar("2026-07-29", 101.0, 55_000_000),
        _bar("2026-07-30", 99.5, 2_600_000),  # partial — 13:00 ET, 5% of a session
    ]
    monkeypatch.setattr("research.bars._fetch_alpaca", lambda sym, start, end: list(fetched))

    out = get_daily_bars("SPY", date(2026, 7, 28), date(2026, 7, 30), db_path=bars_db)

    assert [b["d"] for b in out] == ["2026-07-28", "2026-07-29"]
    stored = _load_cached("SPY", date(2026, 1, 1), date(2026, 12, 31), "alpaca", bars_db)
    assert "2026-07-30" not in [b["d"] for b in stored]


def test_clamped_window_does_not_refetch_forever(monkeypatch, bars_db) -> None:
    """Sunday: the clamp pulls `end` back to Friday's close, so a store already
    covering Friday is complete and no fetch fires. (Unclamped, `last < end`
    made every weekend call re-walk the source.)"""
    monkeypatch.setattr("research.bars._last_complete_session", lambda now=None: date(2026, 7, 24))
    calls: list[tuple] = []

    def _fetch(sym, start, end):
        calls.append((sym, start, end))
        return [_bar("2026-07-23", 100.0, 1.0), _bar("2026-07-24", 101.0, 1.0)]

    monkeypatch.setattr("research.bars._fetch_alpaca", _fetch)

    first = get_daily_bars("SPY", date(2026, 7, 23), date(2026, 7, 26), db_path=bars_db)
    assert len(calls) == 1 and [b["d"] for b in first] == ["2026-07-23", "2026-07-24"]

    second = get_daily_bars("SPY", date(2026, 7, 23), date(2026, 7, 26), db_path=bars_db)
    assert len(calls) == 1  # served from cache — the weekend does not look like a gap
    assert second == first


def test_window_entirely_inside_the_open_session_is_empty(monkeypatch, bars_db) -> None:
    """Asking only for today mid-session yields nothing rather than a partial —
    a live price comes from the quote path, not the daily-bar store."""
    monkeypatch.setattr("research.bars._last_complete_session", lambda now=None: date(2026, 7, 29))
    monkeypatch.setattr(
        "research.bars._fetch_alpaca",
        lambda sym, start, end: pytest.fail("must not fetch an open session"),
    )

    assert get_daily_bars("SPY", date(2026, 7, 30), date(2026, 7, 30), db_path=bars_db) == []


def test_last_complete_session_is_calendar_aware() -> None:
    """The clamp rides trading.market_hours, so weekends and holidays resolve to
    the prior real close rather than a calendar-day subtraction."""
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from research.bars import _last_complete_session

    et = ZoneInfo("America/New_York")
    # Sunday 2026-07-26 → Friday the 24th, not Saturday the 25th.
    assert _last_complete_session(datetime(2026, 7, 26, 12, 0, tzinfo=et)) == date(2026, 7, 24)
    # Thursday 10:00 ET, session open → Wednesday's close is the last complete one.
    assert _last_complete_session(datetime(2026, 7, 30, 10, 0, tzinfo=et)) == date(2026, 7, 29)
    # Thursday 16:30 ET, after the close → Thursday itself is now complete.
    assert _last_complete_session(datetime(2026, 7, 30, 16, 30, tzinfo=et)) == date(2026, 7, 30)
