"""Macro regime substrate — own-store series + the prereg regime-composition report.

No network anywhere: fetches are monkeypatched; the report runs on seeded history.
"""

from __future__ import annotations

import asyncio
from datetime import date, timedelta
from pathlib import Path

import pytest

from db.macro import (
    coverage,
    events_between,
    latest_value,
    series_history,
    upsert_events,
    upsert_macro,
)
from signals import macro_substrate
from signals.macro_substrate import (
    CBOE_SERIES,
    MACRO_SERIES,
    backfill_macro_substrate,
    event_calendar,
    macro_snapshot,
    refresh_macro_substrate,
    regime_report,
)


@pytest.fixture
def macro_db(tmp_path: Path) -> Path:
    return tmp_path / "macro.db"


def test_store_round_trip_and_coverage(macro_db: Path) -> None:
    n = upsert_macro(
        [("VIXCLS", "2026-07-16", 18.6), ("VIXCLS", "2026-07-17", 19.1)],
        db_path=macro_db,
    )
    assert n == 2
    assert latest_value("VIXCLS", db_path=macro_db) == ("2026-07-17", 19.1)
    assert latest_value("VIXCLS", as_of="2026-07-16", db_path=macro_db) == (
        "2026-07-16",
        18.6,
    )
    assert series_history("VIXCLS", db_path=macro_db) == [
        ("2026-07-16", 18.6),
        ("2026-07-17", 19.1),
    ]
    (row,) = coverage(db_path=macro_db)
    assert row == {
        "series": "VIXCLS",
        "rows": 2,
        "first": "2026-07-16",
        "last": "2026-07-17",
    }


def test_refresh_is_fail_soft_per_series(
    macro_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_fetch(series_id: str, *, limit: int, start: str | None = None) -> list[tuple[str, float]]:
        if series_id == "DGS10":
            raise RuntimeError("boom")
        return [("2026-07-17", 1.0)]

    def fake_cboe(index: str) -> list[tuple[str, str, float]]:
        if index == "VVIX":
            raise RuntimeError("boom")
        return [(index, "2026-07-17", 1.0)]

    monkeypatch.setattr(macro_substrate, "fetch_fred_csv", fake_fetch)
    # Stubbed, not merely counted: without these the refresh reaches out to Cboe
    # and FRED for real, which makes a unit test both online and non-deterministic.
    monkeypatch.setattr(macro_substrate, "_fetch_cboe", fake_cboe)
    monkeypatch.setattr(macro_substrate, "refresh_macro_events", lambda **kw: {})
    written = asyncio.run(refresh_macro_substrate(db_path=macro_db))
    assert written == (len(MACRO_SERIES) - 1) + (len(CBOE_SERIES) - 1)
    assert latest_value("DGS10", db_path=macro_db) is None
    assert latest_value("VVIX", db_path=macro_db) is None
    assert latest_value("VIXCLS", db_path=macro_db) == ("2026-07-17", 1.0)
    assert latest_value("VIX9D", db_path=macro_db) == ("2026-07-17", 1.0)


def test_backfill_reports_zero_for_empty_series(
    macro_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_fetch(series_id: str, *, limit: int, start: str | None = None) -> list[tuple[str, float]]:
        return [] if series_id == "DTWEXBGS" else [("2026-07-17", 2.0)]

    monkeypatch.setattr(macro_substrate, "fetch_fred_csv", fake_fetch)
    written = backfill_macro_substrate(db_path=macro_db)
    assert written["DTWEXBGS"] == 0
    assert all(n == 1 for s, n in written.items() if s != "DTWEXBGS")
    snap = macro_snapshot(db_path=macro_db)
    assert "DTWEXBGS" not in snap
    assert snap["VIXCLS"]["axis"] == "vol"


def test_snapshot_refuses_a_percentile_it_cannot_support(macro_db: Path) -> None:
    """One observation ranks at the 100th percentile of itself — arithmetically
    true, and it reads as "extreme" to anything consuming it. The Cboe series are
    forward-only, so day 1 is exactly this case."""
    upsert_macro([("VIX9D", "2026-08-11", 12.5)], db_path=macro_db)
    thin = macro_snapshot(db_path=macro_db)["VIX9D"]
    assert (thin["history_n"], thin["pctile"], thin["bucket"]) == (1, None, None)
    assert thin["value"] == 12.5  # the value still reports; only the rank is withheld

    # Cross the floor and the rank appears.
    upsert_macro(
        [("VIX9D", f"2026-{m:02d}-{d:02d}", float(d)) for m in (5, 6) for d in range(1, 31)],
        db_path=macro_db,
    )
    ranked = macro_snapshot(db_path=macro_db)["VIX9D"]
    assert ranked["pctile"] is not None and ranked["bucket"] is not None


def test_event_calendar_separates_today_from_upcoming(macro_db: Path) -> None:
    upsert_events(
        [("2026-08-11", "PPI", 46), ("2026-08-12", "CPI", 10),
         ("2026-09-30", "GDP", 53)],  # outside the 10-day horizon
        db_path=macro_db,
    )
    cal = event_calendar("2026-08-11", db_path=macro_db)
    assert cal["today"] == ["PPI"]
    assert cal["upcoming"] == [{"date": "2026-08-12", "release": "CPI", "in_days": 1}]
    assert "FOMC" in cal["not_covered"]  # the gap is stated, not silent


def test_event_refresh_drops_a_release_that_is_not_an_event(
    macro_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """FRED marks "FOMC Press Release" on EVERY calendar day. Whitelisting it would
    tell the agent there is a Fed decision daily — worse than no calendar. The guard
    is on DENSITY, not on the id, so it catches the next one too."""
    class _Resp:
        def raise_for_status(self) -> None: ...
        def json(self) -> dict:
            return {"release_dates": [
                {"date": (date(2026, 1, 1) + timedelta(days=i)).isoformat()}
                for i in range(90)
            ]}

    monkeypatch.setattr(macro_substrate, "fred_is_configured", lambda: True)
    monkeypatch.setattr(macro_substrate, "fred_api_key", lambda: "k")
    monkeypatch.setattr(macro_substrate, "MACRO_EVENT_RELEASES", {101: "Daily artifact"})
    monkeypatch.setattr(macro_substrate.httpx, "get", lambda *a, **k: _Resp())
    written = macro_substrate.refresh_macro_events(
        start="2026-01-01", days=90, db_path=macro_db
    )
    assert written == {"Daily artifact": 0}
    assert events_between("2026-01-01", "2026-12-31", db_path=macro_db) == []


def test_regime_report_ranks_only_against_the_past(macro_db: Path) -> None:
    """The dossier backdates this call per episode. Ranking a 2020 session against
    2026 data stamps it with a bucket computed from its own future — the same
    defect class as the claim-matrix inversion."""
    # A monotonically rising series spanning well past the ranking floor.
    start = date(2026, 1, 1)
    upsert_macro(
        [("VIXCLS", (start + timedelta(days=i)).isoformat(), 10.0 + i)
         for i in range(200)],
        db_path=macro_db,
    )
    mid_day = (start + timedelta(days=120)).isoformat()
    last_day = (start + timedelta(days=199)).isoformat()
    early = regime_report([mid_day], db_path=macro_db)["axes"]["vol"]
    late = regime_report([last_day], db_path=macro_db)["axes"]["vol"]
    e, latest = early["per_date"][mid_day], late["per_date"][last_day]
    # Mid-series on a monotonically rising series is the TOP of what was known
    # then. Full-history ranking would have put it near the middle instead.
    assert e["pctile"] == 100.0 and e["bucket"] == "high"
    assert e["ranked_against"] < latest["ranked_against"]


def test_regime_report_withholds_a_rank_it_cannot_support(macro_db: Path) -> None:
    """The floor guards this surface too — it used to guard only macro_snapshot,
    so one payload could carry axes.vol pctile=100 above series.VIXCLS pctile=null."""
    upsert_macro([("VIXCLS", "2026-08-10", 15.46)], db_path=macro_db)
    vol = regime_report(["2026-08-10"], db_path=macro_db)["axes"]["vol"]
    observed = vol["per_date"]["2026-08-10"]
    assert observed["value"] == 15.46  # value still stated
    assert observed["pctile"] is None and observed["bucket"] is None
    assert vol["composition"]["unranked"] == 1


def test_regime_report_discloses_reference_depth(macro_db: Path) -> None:
    _seed_axis_history(macro_db)
    report = regime_report(["2026-07-16"], db_path=macro_db)
    hist = report["axes"]["vol"]["history"]
    assert hist == {"n": _SEED_N, "first": "2026-05-18", "last": "2026-07-16"}


_SEED_N = 60  # _PCTILE_MIN_OBS — below it regime_report withholds the rank


def _seed_axis_history(db: Path) -> None:
    # _SEED_N sessions per axis ending 2026-07-16; the last date is the extreme
    # so the final session's percentile is deterministic. The count is the floor,
    # not a round number: one row fewer and every percentile below is None.
    start = date(2026, 7, 16) - timedelta(days=_SEED_N - 1)
    rows: list[tuple[str, str, float]] = []
    for i in range(1, _SEED_N + 1):
        d = (start + timedelta(days=i - 1)).isoformat()
        # Endpoints match the original 10-session seed so the assertions below
        # still describe the same series; only the sample count changed.
        rows.append(("VIXCLS", d, 15.0 + 10.0 * i / _SEED_N))  # rises to 25.0
        rows.append(("BAMLH0A0HYM2", d, 3.0 + 1.0 * i / _SEED_N))  # rises to 4.0
        rows.append(("DTWEXBGS", d, 120.0 - 10.0 * i / _SEED_N))  # falls to 110.0
        rows.append(("DGS2", d, 4.0))
        rows.append(("DGS10", d, 4.0 + 0.5 * i / _SEED_N))  # spread rises to +0.5
    upsert_macro(rows, db_path=db)


def test_regime_report_composition_and_percentiles(macro_db: Path) -> None:
    _seed_axis_history(macro_db)
    report = regime_report(["2026-07-16"], db_path=macro_db)
    vol = report["axes"]["vol"]
    assert vol["per_date"]["2026-07-16"]["value"] == 25.0
    assert vol["per_date"]["2026-07-16"]["pctile"] == 100.0
    assert vol["per_date"]["2026-07-16"]["bucket"] == "high"
    assert vol["composition"] == {
        "low": 0, "mid": 0, "high": 1, "unranked": 0, "missing": 0,
    }
    dollar = report["axes"]["dollar"]
    assert dollar["per_date"]["2026-07-16"]["value"] == 110.0
    assert dollar["per_date"]["2026-07-16"]["bucket"] == "low"
    curve = report["axes"]["curve"]
    assert curve["per_date"]["2026-07-16"]["value"] == pytest.approx(0.5)
    assert curve["per_date"]["2026-07-16"]["bucket"] == "high"


def test_regime_report_curve_requires_date_joined_legs(macro_db: Path) -> None:
    # DGS10 has a date DGS2 lacks — that date must not enter the spread history.
    upsert_macro(
        [
            ("DGS2", "2026-07-15", 4.0),
            ("DGS10", "2026-07-15", 4.4),
            ("DGS10", "2026-07-16", 5.0),
        ],
        db_path=macro_db,
    )
    report = regime_report(["2026-07-16"], db_path=macro_db)
    per = report["axes"]["curve"]["per_date"]["2026-07-16"]
    # As-of falls back to the joined 07-15 spread; the unjoined 07-16 leg is unused.
    assert per["date_used"] == "2026-07-15"
    assert per["value"] == pytest.approx(0.4)


def test_regime_report_staleness_counts_missing(macro_db: Path) -> None:
    upsert_macro([("VIXCLS", "2026-07-01", 20.0)], db_path=macro_db)
    report = regime_report(["2026-07-08", "2026-07-09"], db_path=macro_db)
    vol = report["axes"]["vol"]
    # 7 calendar days is inside REPORT_STALE_DAYS; 8 is out.
    assert vol["per_date"]["2026-07-08"]["value"] == 20.0
    assert vol["per_date"]["2026-07-09"]["value"] is None
    assert vol["composition"]["missing"] == 1
