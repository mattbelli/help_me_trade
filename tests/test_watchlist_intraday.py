"""Session/RTH filtering for the watchlist intraday close series."""

from __future__ import annotations

from brokers.alpaca import StockBar


def _bar(time: str, close: float) -> StockBar:
    return StockBar(time=time, open=close, high=close, low=close, close=close, volume=0.0)


def test_session_intraday_closes_filters_to_latest_rth_session() -> None:
    from api.main import _session_intraday_closes

    bars = [
        # Prior ET date (2026-06-22) — dropped.
        _bar("2026-06-22T19:55:00Z", 1.0),  # 15:55 ET, prior day
        # Latest ET date (2026-06-23) pre-market — dropped (before 09:30 ET).
        _bar("2026-06-23T12:00:00Z", 2.0),  # 08:00 ET
        # Latest ET date RTH — kept, chronological.
        _bar("2026-06-23T13:30:00Z", 5.0),  # 09:30 ET (open boundary, inclusive)
        _bar("2026-06-23T19:30:00Z", 6.0),  # 15:30 ET
        # Latest ET date post-close — dropped (>= 16:00 ET).
        _bar("2026-06-23T20:00:00Z", 7.0),  # 16:00 ET (close boundary, exclusive)
        _bar("2026-06-23T22:00:00Z", 8.0),  # 18:00 ET after-hours
    ]

    assert _session_intraday_closes(bars) == [5.0, 6.0]


def test_session_intraday_closes_empty() -> None:
    from api.main import _session_intraday_closes

    assert _session_intraday_closes([]) == []
