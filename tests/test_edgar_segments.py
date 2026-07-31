"""DERA segment-level reads — network-free.

The cases are what live DERA data exposed: dimension labels are
`Axis=Member;` (semicolon-terminated, sometimes several joined), periods are
bucketed by the filer's own `qtrs` rather than guessed from duration, dates
arrive as bare YYYYMMDD and MUST be normalised or point-in-time reads sort
wrong, and the `segments` column carries every XBRL axis — not just the
business breakdown a caller asked for.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from db.base import connect
from research.edgar_bulk import accn_to_int, concept_interner
from research.edgar_dera import (
    _ddate_to_iso,
    _taxonomy_of,
    ensure_dera_schema,
    quarters_through,
)
from research.fundamentals import (
    _ensure_schema,
    _split_segment,
    _upsert_company,
    segment_axes,
    segment_series,
)


@pytest.fixture
def cache_db(tmp_path: Path) -> Path:
    return tmp_path / "fundamentals.db"


@pytest.fixture
def facts_db(tmp_path: Path) -> Path:
    return tmp_path / "edgar_facts.db"


def _load(cache_db: Path, facts_db: Path, symbol: str, cik: str, rows: list[dict]) -> None:
    conn = connect(cache_db)
    try:
        _ensure_schema(conn)
        _upsert_company(conn, symbol=symbol, cik=cik, sic=5961, sic_desc="Retail")
        conn.commit()
    finally:
        conn.close()

    conn = connect(facts_db)
    try:
        ensure_dera_schema(conn)
        concept_id, flush = concept_interner(conn)
        seg_ids: dict[str, int] = {}
        out = []
        for r in rows:
            sid = seg_ids.setdefault(r["segment"], len(seg_ids) + 1)
            out.append(
                (
                    int(cik),
                    concept_id("us-gaap", r["tag"]),
                    r["period_end"],
                    r["qtrs"],
                    sid,
                    "USD",
                    "",
                    accn_to_int(r["accn"]),
                    r["filed"],
                    r.get("form", "10-K"),
                    r["val"],
                )
            )
        flush()
        conn.executemany(
            "INSERT OR IGNORE INTO segments (id, label) VALUES (?, ?)",
            [(v, k) for k, v in seg_ids.items()],
        )
        conn.executemany(
            "INSERT OR REPLACE INTO segment_facts (cik, concept_id, period_end, qtrs, "
            "segment_id, unit, coreg, accn, filed, form, val) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            out,
        )
        conn.commit()
    finally:
        conn.close()


def test_dera_dates_are_normalised_to_iso() -> None:
    """DERA writes bare YYYYMMDD. Left alone, `filed <= '2026-03-01'` compares
    against '20260206' and every point-in-time read sorts wrong."""
    assert _ddate_to_iso("20251231") == "2025-12-31"
    assert _ddate_to_iso("") is None
    assert _ddate_to_iso("2025") is None
    assert _ddate_to_iso("not-a-date") is None


def test_taxonomy_splits_standard_tags_from_company_extensions() -> None:
    assert _taxonomy_of("us-gaap/2025") == "us-gaap"
    assert _taxonomy_of("0001018724-26-000004") == "custom"


def test_quarter_range_stops_before_the_current_quarter() -> None:
    """DERA publishes in arrears; the exact lag is not a constant we bake in —
    the range runs to the previous quarter and a 404 handles the rest."""
    quarters = quarters_through(date(2026, 7, 31))
    assert quarters[0] == "2009q1"
    assert quarters[-1] == "2026q2"
    assert "2026q3" not in quarters


def test_segment_label_splits_into_axis_and_member() -> None:
    assert _split_segment("BusinessSegments=AmazonWebServicesSegment;") == (
        "BusinessSegments",
        "AmazonWebServicesSegment",
    )
    assert _split_segment("Geographical=US;") == ("Geographical", "US")
    # several dimensions joined — the first is the axis, nothing is invented
    axis, member = _split_segment("BusinessSegments=AWS;Geographical=US;")
    assert (axis, member) == ("BusinessSegments", "AWS")


def test_segment_series_buckets_by_the_filers_own_qtrs(
    cache_db: Path, facts_db: Path
) -> None:
    """qtrs is stated by the filer: 4 = annual, 1 = a quarter, 0 = a balance-sheet
    instant. Only comparable durations become a series."""
    _load(
        cache_db, facts_db, "AMZN", "1018724",
        [
            {"tag": "RevenueFromContractWithCustomerExcludingAssessedTax",
             "segment": "BusinessSegments=AmazonWebServicesSegment;",
             "period_end": "2025-12-31", "qtrs": 4, "val": 128_720e6,
             "accn": "0001018724-26-000004", "filed": "2026-02-06"},
            {"tag": "RevenueFromContractWithCustomerExcludingAssessedTax",
             "segment": "BusinessSegments=AmazonWebServicesSegment;",
             "period_end": "2024-12-31", "qtrs": 4, "val": 107_560e6,
             "accn": "0001018724-26-000004", "filed": "2026-02-06"},
            {"tag": "RevenueFromContractWithCustomerExcludingAssessedTax",
             "segment": "BusinessSegments=AmazonWebServicesSegment;",
             "period_end": "2025-12-31", "qtrs": 2, "val": 60_000e6,   # half-year
             "accn": "0001018724-26-000004", "filed": "2026-02-06"},
            {"tag": "RevenueFromContractWithCustomerExcludingAssessedTax",
             "segment": "BusinessSegments=NorthAmericaSegment;",
             "period_end": "2025-12-31", "qtrs": 4, "val": 426_310e6,
             "accn": "0001018724-26-000004", "filed": "2026-02-06"},
        ],
    )
    out = segment_series("AMZN", concept="revenue", cache_db=cache_db, facts_db=facts_db)
    assert out["axes"] == ["BusinessSegments"]
    aws = next(s for s in out["series"] if s["member"] == "AmazonWebServicesSegment")
    assert [a["period_end"] for a in aws["annual"]] == ["2025-12-31", "2024-12-31"]
    assert aws["annual"][0]["val"] == pytest.approx(128_720e6)
    # the 6-month duration belongs to neither series
    assert aws["quarterly"] == []
    assert {s["member"] for s in out["series"]} == {
        "AmazonWebServicesSegment", "NorthAmericaSegment"
    }


def test_axis_filter_and_point_in_time(cache_db: Path, facts_db: Path) -> None:
    _load(
        cache_db, facts_db, "AMZN", "1018724",
        [
            {"tag": "Revenues", "segment": "BusinessSegments=AWS;",
             "period_end": "2025-12-31", "qtrs": 4, "val": 100.0,
             "accn": "0001018724-26-000004", "filed": "2026-02-06"},
            {"tag": "Revenues", "segment": "Geographical=US;",
             "period_end": "2025-12-31", "qtrs": 4, "val": 400.0,
             "accn": "0001018724-26-000004", "filed": "2026-02-06"},
        ],
    )
    only_geo = segment_series(
        "AMZN", concept="Revenues", axis="Geographical",
        cache_db=cache_db, facts_db=facts_db,
    )
    assert [s["member"] for s in only_geo["series"]] == ["US"]

    # nothing had been filed by then
    early = segment_series(
        "AMZN", concept="Revenues", as_of="2026-01-01",
        cache_db=cache_db, facts_db=facts_db,
    )
    assert early["series"] == []


def test_reporting_axes_sort_above_incidental_ones(
    cache_db: Path, facts_db: Path
) -> None:
    """DERA's dimension column carries EVERY XBRL axis. Sorting purely by fact
    count buries the business breakdown under equity-component schedules."""
    rows = [
        {"tag": "Revenues", "segment": "EquityComponents=CommonStock;",
         "period_end": f"202{i}-12-31", "qtrs": 4, "val": float(i),
         "accn": f"0001018724-26-00000{i}", "filed": f"202{i}-02-06"}
        for i in range(1, 6)
    ]
    rows.append(
        {"tag": "Revenues", "segment": "BusinessSegments=AWS;",
         "period_end": "2025-12-31", "qtrs": 4, "val": 100.0,
         "accn": "0001018724-26-000009", "filed": "2026-02-06"}
    )
    _load(cache_db, facts_db, "AMZN", "1018724", rows)
    axes = segment_axes("AMZN", cache_db=cache_db, facts_db=facts_db)
    assert axes[0]["axis"] == "BusinessSegments"  # despite having fewer facts
    assert axes[0]["facts"] < axes[1]["facts"]


def test_already_loaded_quarters_are_skipped(facts_db: Path, monkeypatch) -> None:
    """The weekly cron must not re-ingest cached zips: SEC publishes a new set
    ~4x a year, so ~48 runs out of 52 would otherwise rewrite 80M rows against a
    live DB for nothing."""
    from research import edgar_dera

    conn = connect(facts_db)
    try:
        ensure_dera_schema(conn)
        conn.execute(
            "INSERT INTO dera_quarters (quarter, ingested_at, segment_facts) "
            "VALUES ('2026q1', '2026-07-31', 10)"
        )
        conn.commit()
    finally:
        conn.close()

    downloads: list[str] = []
    monkeypatch.setattr(
        edgar_dera, "download_quarter", lambda q, **kw: downloads.append(q) or None
    )
    totals = edgar_dera.backfill(["2026q1"], db_path=facts_db)
    assert totals["already_loaded"] == 1
    assert totals["quarters"] == 0
    assert downloads == []  # not even the existence check runs

    # ...and --force overrides it
    edgar_dera.backfill(["2026q1"], db_path=facts_db, force=True)
    assert downloads == ["2026q1"]


def test_unknown_symbol_returns_none(cache_db: Path, facts_db: Path) -> None:
    assert segment_series("NOSUCH", cache_db=cache_db, facts_db=facts_db) is None
    assert segment_axes("NOSUCH", cache_db=cache_db, facts_db=facts_db) is None
