"""Request-window construction for the OI top-up — the part that talks to a paid API."""

from __future__ import annotations

from datetime import date

from tools.oi_refresh import _windows


def test_single_window_feed_asks_once() -> None:
    assert _windows(date(2026, 7, 17), "2026-07-31T18:00:00.000000000Z", per_day=False) == [
        ("2026-07-17", "2026-07-31T18:00:00.000000000Z", "2026-07-17_2026-07-31")
    ]


def test_per_day_feed_splits_and_ends_at_the_dataset_edge() -> None:
    # A 14-day OPRA window 504'd at the gateway; per-day requests are the fix, and the
    # LAST one must run to the dataset's exact end or today's records wait a day.
    w = _windows(date(2026, 7, 29), "2026-07-31T18:00:00.000000000Z", per_day=True)
    assert [x[2] for x in w] == ["2026-07-29", "2026-07-30", "2026-07-31"]
    assert w[0][:2] == ("2026-07-29", "2026-07-30")
    assert w[-1][:2] == ("2026-07-31", "2026-07-31T18:00:00.000000000Z")


def test_midnight_dataset_end_drops_the_empty_window() -> None:
    # OPRA's available end is exactly midnight until the pre-open publish lands. As
    # strings '2026-08-01' < '2026-08-01T00:00:00Z', so a string compare kept a window
    # of zero length and the API answered 422 (measured 2026-08-01).
    w = _windows(date(2026, 7, 31), "2026-08-01T00:00:00.000000000Z", per_day=True)
    assert [x[2] for x in w] == ["2026-07-31"]
    assert w[-1][:2] == ("2026-07-31", "2026-08-01")


def test_nothing_new_yields_no_windows() -> None:
    assert _windows(date(2026, 8, 1), "2026-08-01T00:00:00.000000000Z", per_day=True) == []
    assert _windows(date(2026, 8, 1), "2026-08-01T00:00:00.000000000Z", per_day=False) == []


def test_holiday_window_is_skipped_not_fatal(caplog) -> None:
    # Labor Day 2026-09-07: no OPRA session, so the parent symbols resolve to nothing
    # and the API 422s. That one day aborted every run and froze the store for 19 days.
    import logging
    import sqlite3
    from types import SimpleNamespace

    from tools.oi_refresh import refresh

    def get_cost(*, start, **_):
        if start == "2026-09-07":
            raise RuntimeError("422 symbology_invalid_request\nNone of the symbols could be resolved")
        return 0.0

    meta = SimpleNamespace(
        get_dataset_range=lambda dataset: {"end": "2026-09-08T12:00:00.000000000Z"},
        get_cost=get_cost,
    )
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE oi_daily (date TEXT, source TEXT)")
    conn.execute("INSERT INTO oi_daily VALUES ('2026-09-06', 'opra')")
    with caplog.at_level(logging.INFO, logger="oi_refresh"):
        refresh(SimpleNamespace(metadata=meta), conn, "opra",
                start_override=None, max_cost=None, dry_run=True)
    assert "2026-09-07: no symbols resolve" in caplog.text
    assert "2 window(s)" in caplog.text
