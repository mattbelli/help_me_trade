"""Live earnings-calendar data layer: table reads, Finnhub client, refresh."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from db.earnings_calendar import (
    earnings_dates_for,
    last_earnings_date,
    next_earnings_date,
    upsert_earnings,
)


def _seed(db: Path) -> None:
    upsert_earnings(
        [
            {"symbol": "AAPL", "date": "2026-01-30", "hour": "amc"},
            {"symbol": "AAPL", "date": "2026-05-01", "hour": "amc"},
            {"symbol": "aapl", "date": "2026-07-31"},  # lowercase normalizes
            {"symbol": "MSFT", "date": "2026-04-24"},
            {"symbol": "", "date": "2026-01-01"},  # skipped (no symbol)
        ],
        db_path=db,
    )


def test_upsert_and_dates_for(tmp_path: Path) -> None:
    db = tmp_path / "ec.db"
    _seed(db)
    assert earnings_dates_for("AAPL", db_path=db) == [
        "2026-01-30",
        "2026-05-01",
        "2026-07-31",
    ]
    assert earnings_dates_for("MSFT", db_path=db) == ["2026-04-24"]
    assert earnings_dates_for("NVDA", db_path=db) == []


def test_next_and_last_earnings_date(tmp_path: Path) -> None:
    db = tmp_path / "ec.db"
    _seed(db)
    # On 2026-03-01: prior = Jan 30, next = May 1
    assert last_earnings_date("AAPL", "2026-03-01", db_path=db) == "2026-01-30"
    assert next_earnings_date("AAPL", "2026-03-01", db_path=db) == "2026-05-01"
    # On an earnings day itself: last is inclusive, next is strictly after
    assert last_earnings_date("AAPL", "2026-05-01", db_path=db) == "2026-05-01"
    assert next_earnings_date("AAPL", "2026-05-01", db_path=db) == "2026-07-31"
    # Before any earnings
    assert last_earnings_date("AAPL", "2026-01-01", db_path=db) is None
    assert next_earnings_date("AAPL", "2026-01-01", db_path=db) == "2026-01-30"


def test_upsert_is_idempotent_and_updates_hour(tmp_path: Path) -> None:
    db = tmp_path / "ec.db"
    upsert_earnings([{"symbol": "AAPL", "date": "2026-01-30", "hour": "bmo"}], db_path=db)
    upsert_earnings([{"symbol": "AAPL", "date": "2026-01-30", "hour": "amc"}], db_path=db)
    assert earnings_dates_for("AAPL", db_path=db) == ["2026-01-30"]  # no dup


def test_finnhub_parse():
    from signals import finnhub

    rows = finnhub._parse_calendar(
        {
            "earningsCalendar": [
                {"symbol": "AAPL", "date": "2026-01-30", "hour": "amc"},
                {"symbol": "msft", "date": "2026-04-24"},
                {"symbol": "", "date": "2026-01-01"},  # dropped
                {"date": "2026-01-01"},  # dropped
            ]
        }
    )
    assert rows == [
        {"symbol": "AAPL", "date": "2026-01-30", "hour": "amc"},
        {"symbol": "MSFT", "date": "2026-04-24", "hour": ""},
    ]


def test_finnhub_missing_key_raises(monkeypatch):
    from signals import finnhub

    monkeypatch.setattr(finnhub, "finnhub_api_key", lambda: "")
    with pytest.raises(finnhub.FinnhubError):
        finnhub.fetch_earnings_calendar("2026-01-01", "2026-02-01")


def test_refresh_filters_to_universe_and_upserts(tmp_path: Path, monkeypatch) -> None:
    from signals import earnings_refresh

    db = tmp_path / "ec.db"
    fake = [
        {"symbol": "AAPL", "date": "2026-01-30", "hour": "amc"},
        {"symbol": "NVDA", "date": "2026-02-25", "hour": "amc"},
        {"symbol": "ZZZZ", "date": "2026-02-01", "hour": ""},  # not in universe
    ]
    monkeypatch.setattr(earnings_refresh, "alphavantage_is_configured", lambda: False)
    monkeypatch.setattr(earnings_refresh, "finnhub_is_configured", lambda: True)

    # The fetch is now chunked (≤30-day windows) to dodge Finnhub's row cap, so the
    # mock must be window-aware — each event only appears in the chunk covering its date.
    def _fake_fetch(from_date, to_date, **_k):
        return [r for r in fake if from_date <= r["date"] <= to_date]

    monkeypatch.setattr(earnings_refresh, "fetch_earnings_calendar", _fake_fetch)

    n = earnings_refresh.refresh_earnings_calendar(
        ["AAPL", "NVDA"], now=datetime(2026, 2, 1, tzinfo=UTC), db_path=db
    )
    assert n == 2
    assert earnings_dates_for("AAPL", db_path=db) == ["2026-01-30"]
    assert earnings_dates_for("NVDA", db_path=db) == ["2026-02-25"]
    assert earnings_dates_for("ZZZZ", db_path=db) == []


def test_refresh_noop_when_unconfigured(tmp_path: Path, monkeypatch) -> None:
    from signals import earnings_refresh

    db = tmp_path / "ec.db"
    monkeypatch.setattr(earnings_refresh, "alphavantage_is_configured", lambda: False)
    monkeypatch.setattr(earnings_refresh, "finnhub_is_configured", lambda: False)
    assert earnings_refresh.refresh_earnings_calendar(["AAPL"], db_path=db) == 0
