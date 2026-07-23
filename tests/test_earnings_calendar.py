"""Live earnings-calendar data layer: table reads, Finnhub client, refresh."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from db.earnings_calendar import (
    earnings_dates_for,
    last_earnings_date,
    next_earnings_date,
    prune_withdrawn_estimates,
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


def _set_fetched_at(db: Path, symbol: str, date: str, fetched_at: str) -> None:
    from db.base import connect

    with connect(db) as conn:
        conn.execute(
            "UPDATE earnings_calendar SET fetched_at = ? WHERE symbol = ? AND date = ?",
            (fetched_at, symbol, date),
        )
        conn.commit()


def test_prune_deletes_withdrawn_future_estimate(tmp_path: Path) -> None:
    """The GOOGL case: a stale vendor estimate survives after the real (edgar)
    report on a nearby-but-different date — prune kills it once the vendor stops
    listing it."""
    db = tmp_path / "ec.db"
    upsert_earnings(
        [
            {"symbol": "GOOGL", "date": "2026-07-28", "source": "finnhub"},
            {"symbol": "GOOGL", "date": "2026-07-22", "source": "edgar"},
        ],
        db_path=db,
    )
    _set_fetched_at(db, "GOOGL", "2026-07-28", "2026-07-05T18:28:36+00:00")
    n = prune_withdrawn_estimates(
        ["GOOGL"],
        {("GOOGL", "2026-07-22")},  # today's fetches no longer list 07-28
        start="2026-03-25",
        end="2026-09-06",
        today="2026-07-23",
        db_path=db,
    )
    assert n == 1
    assert earnings_dates_for("GOOGL", db_path=db) == ["2026-07-22"]


def test_prune_keeps_confirmed_and_edgar_rows(tmp_path: Path) -> None:
    """The ABBV case: an early edgar 8-K plus a vendor date for the REAL report
    later the same month — the vendor still lists it, so it must survive."""
    db = tmp_path / "ec.db"
    upsert_earnings(
        [
            {"symbol": "ABBV", "date": "2026-07-06", "source": "edgar"},
            {"symbol": "ABBV", "date": "2026-07-31", "source": "finnhub"},
        ],
        db_path=db,
    )
    n = prune_withdrawn_estimates(
        ["ABBV"],
        {("ABBV", "2026-07-31")},
        start="2026-03-25",
        end="2026-09-06",
        today="2026-07-23",
        db_path=db,
    )
    assert n == 0
    assert earnings_dates_for("ABBV", db_path=db) == ["2026-07-06", "2026-07-31"]


def test_prune_keeps_past_vendor_row_confirmed_through_its_date(tmp_path: Path) -> None:
    """A vendor row last confirmed ON its event date is a real past event — the
    vendor's rolling window dropping it later is not a withdrawal. A past row the
    vendor abandoned BEFORE the date arrived is a phantom and dies."""
    db = tmp_path / "ec.db"
    upsert_earnings(
        [
            {"symbol": "DAL", "date": "2026-07-10", "source": "finnhub"},  # real, kept
            {"symbol": "BIIB", "date": "2026-07-16", "source": "finnhub"},  # phantom
        ],
        db_path=db,
    )
    _set_fetched_at(db, "DAL", "2026-07-10", "2026-07-10T18:00:00+00:00")
    _set_fetched_at(db, "BIIB", "2026-07-16", "2026-06-26T22:40:30+00:00")
    n = prune_withdrawn_estimates(
        ["DAL", "BIIB"],
        set(),  # neither is in today's fetch (both dates are past)
        start="2026-03-25",
        end="2026-09-06",
        today="2026-07-23",
        db_path=db,
    )
    assert n == 1
    assert earnings_dates_for("DAL", db_path=db) == ["2026-07-10"]
    assert earnings_dates_for("BIIB", db_path=db) == []


def test_prune_scopes_to_universe_and_window(tmp_path: Path) -> None:
    db = tmp_path / "ec.db"
    upsert_earnings(
        [
            {"symbol": "AAPL", "date": "2026-07-28", "source": "finnhub"},  # not in universe
            {"symbol": "NVDA", "date": "2026-11-25", "source": "finnhub"},  # beyond window
        ],
        db_path=db,
    )
    _set_fetched_at(db, "AAPL", "2026-07-28", "2026-07-01T00:00:00+00:00")
    _set_fetched_at(db, "NVDA", "2026-11-25", "2026-07-01T00:00:00+00:00")
    n = prune_withdrawn_estimates(
        ["NVDA"],
        set(),
        start="2026-03-25",
        end="2026-09-06",
        today="2026-07-23",
        db_path=db,
    )
    assert n == 0
    assert earnings_dates_for("AAPL", db_path=db) == ["2026-07-28"]
    assert earnings_dates_for("NVDA", db_path=db) == ["2026-11-25"]


def test_prune_never_touches_day_of_or_event_boundary_rows(tmp_path: Path) -> None:
    """Review finding: Alpha Vantage is forward-only — it stops asserting a date
    the moment it becomes today, so the last stamp on a REAL event is often the
    day-before refresh. Day-of rows are never pruned; past rows survive unless
    abandoned >= 3 days before the event."""
    db = tmp_path / "ec.db"
    upsert_earnings(
        [
            {"symbol": "ACME", "date": "2026-07-23", "source": "finnhub"},  # day-of
            {"symbol": "BOLT", "date": "2026-07-22", "source": "finnhub"},  # stamp D-1
            {"symbol": "CRUX", "date": "2026-07-21", "source": "finnhub"},  # stamp D-2
        ],
        db_path=db,
    )
    _set_fetched_at(db, "ACME", "2026-07-23", "2026-07-01T00:00:00+00:00")
    _set_fetched_at(db, "BOLT", "2026-07-22", "2026-07-21T05:00:00+00:00")
    _set_fetched_at(db, "CRUX", "2026-07-21", "2026-07-19T05:00:00+00:00")
    n = prune_withdrawn_estimates(
        ["ACME", "BOLT", "CRUX"],
        set(),
        start="2026-03-25",
        end="2026-09-06",
        today="2026-07-23",
        db_path=db,
    )
    assert n == 0
    assert earnings_dates_for("ACME", db_path=db) == ["2026-07-23"]
    assert earnings_dates_for("BOLT", db_path=db) == ["2026-07-22"]
    assert earnings_dates_for("CRUX", db_path=db) == ["2026-07-21"]


def test_prune_normalizes_confirmed_pairs(tmp_path: Path) -> None:
    """A future fetcher returning lowercase/padded pairs must still protect its
    just-confirmed rows."""
    db = tmp_path / "ec.db"
    upsert_earnings([{"symbol": "GOOGL", "date": "2026-08-05", "source": "finnhub"}], db_path=db)
    _set_fetched_at(db, "GOOGL", "2026-08-05", "2026-07-01T00:00:00+00:00")
    n = prune_withdrawn_estimates(
        ["GOOGL"],
        {("googl ", " 2026-08-05")},
        start="2026-03-25",
        end="2026-09-06",
        today="2026-07-23",
        db_path=db,
    )
    assert n == 0
    assert earnings_dates_for("GOOGL", db_path=db) == ["2026-08-05"]


def test_prune_aborts_on_implausible_blast_radius(tmp_path: Path) -> None:
    """A broken confirmed set (truncated vendor response that still parsed) must
    not wipe the calendar: past the cap the prune keeps everything and warns."""
    db = tmp_path / "ec.db"
    syms = [f"S{i:03d}" for i in range(30)]
    upsert_earnings(
        [{"symbol": s, "date": "2026-08-05", "source": "finnhub"} for s in syms],
        db_path=db,
    )
    for s in syms:
        _set_fetched_at(db, s, "2026-08-05", "2026-07-01T00:00:00+00:00")
    n = prune_withdrawn_estimates(
        syms,
        set(),  # nothing confirmed — 30/30 would be "withdrawn"
        start="2026-03-25",
        end="2026-09-06",
        today="2026-07-23",
        db_path=db,
    )
    assert n == 0
    for s in syms:
        assert earnings_dates_for(s, db_path=db) == ["2026-08-05"]


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


def _patch_async_fetchers(
    monkeypatch,
    *,
    av_rows: list | None,
    finnhub_rows: list | Exception,
    edgar_rows: list,
) -> None:
    """Wire arefresh's three fetchers. av_rows=None marks AV unconfigured."""
    from signals import earnings_refresh

    monkeypatch.setattr(
        earnings_refresh, "alphavantage_is_configured", lambda: av_rows is not None
    )

    async def _av(**_k):
        return av_rows or []

    async def _fh(_start, _end, **_k):
        if isinstance(finnhub_rows, Exception):
            raise finnhub_rows
        return [r for r in finnhub_rows if _start <= r["date"] <= _end]

    async def _edgar(_symbols, **_k):
        return edgar_rows

    monkeypatch.setattr(earnings_refresh, "av_afetch_earnings_calendar", _av)
    monkeypatch.setattr(earnings_refresh, "finnhub_is_configured", lambda: True)
    monkeypatch.setattr(earnings_refresh, "afetch_earnings_calendar", _fh)
    monkeypatch.setattr(earnings_refresh, "afetch_edgar_earnings", _edgar)


@pytest.mark.asyncio
async def test_arefresh_prunes_withdrawn_estimates(tmp_path: Path, monkeypatch) -> None:
    from datetime import UTC as _UTC

    from signals import earnings_refresh

    db = tmp_path / "ec.db"
    upsert_earnings([{"symbol": "GOOGL", "date": "2026-07-28"}], db_path=db)
    _set_fetched_at(db, "GOOGL", "2026-07-28", "2026-07-05T18:28:36+00:00")
    _patch_async_fetchers(
        monkeypatch,
        av_rows=[{"symbol": "GOOGL", "date": "2026-10-27", "hour": ""}],
        finnhub_rows=[{"symbol": "GOOGL", "date": "2026-08-05", "hour": ""}],
        edgar_rows=[{"symbol": "GOOGL", "date": "2026-07-22", "hour": "amc", "source": "edgar"}],
    )
    await earnings_refresh.arefresh_earnings_calendar(
        ["GOOGL"], now=datetime(2026, 7, 23, 14, 0, tzinfo=_UTC), db_path=db
    )
    # 07-28 withdrawn (pruned); edgar 07-22, finnhub's fresh 08-05 and AV's
    # far-future 10-27 all remain.
    assert earnings_dates_for("GOOGL", db_path=db) == [
        "2026-07-22",
        "2026-08-05",
        "2026-10-27",
    ]


@pytest.mark.asyncio
async def test_arefresh_skips_prune_when_a_vendor_fails(
    tmp_path: Path, monkeypatch
) -> None:
    from datetime import UTC as _UTC

    from signals import earnings_refresh

    db = tmp_path / "ec.db"
    upsert_earnings([{"symbol": "GOOGL", "date": "2026-07-28"}], db_path=db)
    _set_fetched_at(db, "GOOGL", "2026-07-28", "2026-07-05T18:28:36+00:00")
    now = datetime(2026, 7, 23, 14, 0, tzinfo=_UTC)

    # AV configured but returned [] (throttled) — vendor picture incomplete.
    _patch_async_fetchers(monkeypatch, av_rows=[], finnhub_rows=[], edgar_rows=[])
    await earnings_refresh.arefresh_earnings_calendar(["GOOGL"], now=now, db_path=db)
    assert earnings_dates_for("GOOGL", db_path=db) == ["2026-07-28"]

    # Finnhub chunk fetch raised — same: keep everything.
    _patch_async_fetchers(
        monkeypatch, av_rows=None, finnhub_rows=RuntimeError("boom"), edgar_rows=[]
    )
    await earnings_refresh.arefresh_earnings_calendar(["GOOGL"], now=now, db_path=db)
    assert earnings_dates_for("GOOGL", db_path=db) == ["2026-07-28"]

    # Review finding: Finnhub configured and returning [] WITHOUT raising (an
    # empty-but-200 body — dead key, malformed payload) must also gate the prune;
    # a whole-market multi-month window is never actually empty.
    _patch_async_fetchers(monkeypatch, av_rows=None, finnhub_rows=[], edgar_rows=[])
    await earnings_refresh.arefresh_earnings_calendar(["GOOGL"], now=now, db_path=db)
    assert earnings_dates_for("GOOGL", db_path=db) == ["2026-07-28"]


def test_refresh_noop_when_unconfigured(tmp_path: Path, monkeypatch) -> None:
    from signals import earnings_refresh

    db = tmp_path / "ec.db"
    monkeypatch.setattr(earnings_refresh, "alphavantage_is_configured", lambda: False)
    monkeypatch.setattr(earnings_refresh, "finnhub_is_configured", lambda: False)
    assert earnings_refresh.refresh_earnings_calendar(["AAPL"], db_path=db) == 0
