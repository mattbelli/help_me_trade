"""Macro regime substrate — own-store series + the prereg regime-composition report.

No network anywhere: fetches are monkeypatched; the report runs on seeded history.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from db.macro import coverage, latest_value, series_history, upsert_macro
from signals import macro_substrate
from signals.macro_substrate import (
    MACRO_SERIES,
    backfill_macro_substrate,
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

    monkeypatch.setattr(macro_substrate, "fetch_fred_csv", fake_fetch)
    written = asyncio.run(refresh_macro_substrate(db_path=macro_db))
    assert written == len(MACRO_SERIES) - 1
    assert latest_value("DGS10", db_path=macro_db) is None
    assert latest_value("VIXCLS", db_path=macro_db) == ("2026-07-17", 1.0)


def test_backfill_reports_zero_for_empty_series(
    macro_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_fetch(series_id: str, *, limit: int, start: str | None = None) -> list[tuple[str, float]]:
        return [] if series_id == "DTWEXBGS" else [("2026-07-17", 2.0)]

    monkeypatch.setattr(macro_substrate, "fetch_fred_csv", fake_fetch)
    monkeypatch.setattr(macro_substrate, "fred_is_configured", lambda: False)
    written = backfill_macro_substrate(db_path=macro_db)
    assert written["DTWEXBGS"] == 0
    assert all(n == 1 for s, n in written.items() if s != "DTWEXBGS")
    snap = macro_snapshot(db_path=macro_db)
    assert "DTWEXBGS" not in snap
    assert snap["VIXCLS"]["axis"] == "vol"


def test_backfill_deepens_credit_series_with_free_key(
    macro_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_csv(series_id: str, *, limit: int, start: str | None = None) -> list[tuple[str, float]]:
        return [("2026-07-17", 3.5)]  # the shallow keyless window

    def fake_keyed(series_id: str, *, api_key: str, limit: int) -> list[tuple[str, float]]:
        assert series_id in macro_substrate._KEYED_DEEP_BACKFILL
        return [("2008-11-21", 21.82), ("2026-07-17", 3.5)]

    monkeypatch.setattr(macro_substrate, "fetch_fred_csv", fake_csv)
    monkeypatch.setattr(macro_substrate, "fetch_fred", fake_keyed)
    monkeypatch.setattr(macro_substrate, "fred_is_configured", lambda: True)
    monkeypatch.setattr(macro_substrate, "fred_api_key", lambda: "k")
    written = backfill_macro_substrate(db_path=macro_db)
    assert written["BAMLH0A0HYM2"] == 3  # 1 keyless + 2 keyed (upsert dedups rows)
    hist = series_history("BAMLH0A0HYM2", db_path=macro_db)
    assert hist[0] == ("2008-11-21", 21.82)


def _seed_axis_history(db: Path) -> None:
    # 10 sessions of history per axis; the last date is the extreme so the
    # percentile of the final session is deterministic.
    rows: list[tuple[str, str, float]] = []
    for i in range(1, 11):
        d = f"2026-07-{i + 6:02d}"  # 07-07 .. 07-16
        rows.append(("VIXCLS", d, 15.0 + i))  # rises to 25.0
        rows.append(("BAMLH0A0HYM2", d, 3.0 + 0.1 * i))
        rows.append(("DTWEXBGS", d, 120.0 - i))  # falls to 110.0
        rows.append(("DGS2", d, 4.0))
        rows.append(("DGS10", d, 4.0 + 0.05 * i))  # spread rises to +0.5
    upsert_macro(rows, db_path=db)


def test_regime_report_composition_and_percentiles(macro_db: Path) -> None:
    _seed_axis_history(macro_db)
    report = regime_report(["2026-07-16"], db_path=macro_db)
    vol = report["axes"]["vol"]
    assert vol["per_date"]["2026-07-16"]["value"] == 25.0
    assert vol["per_date"]["2026-07-16"]["pctile"] == 100.0
    assert vol["per_date"]["2026-07-16"]["bucket"] == "high"
    assert vol["composition"] == {"low": 0, "mid": 0, "high": 1, "missing": 0}
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
