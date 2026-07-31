"""EDGAR XBRL corpus reads — the traps measured against real filing data.

Every case here is a bug this module actually shipped with, or that the bulk
corpus exposed: alias truncation, form-based period classification, the fiscal
year-end balance repeated as a quarterly comparative, and same-day amendments
colliding on a key that omitted the accession number.

Fixtures are loaded through the real ingest path (`fact_rows` / `upsert_facts`)
rather than hand-built row tuples, so the tests exercise the parser the bulk
loader actually uses.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from db.base import connect
from research.edgar_bulk import (
    accn_to_int,
    accn_to_str,
    concept_interner,
    ensure_bulk_schema,
    fact_rows,
    upsert_facts,
)
from research.fundamentals import (
    _ensure_schema,
    _period_bucket,
    _upsert_company,
    company_concepts,
    concept_series,
    latest_financials,
    search_concepts,
)


@pytest.fixture
def cache_db(tmp_path: Path) -> Path:
    return tmp_path / "fundamentals.db"


@pytest.fixture
def facts_db(tmp_path: Path) -> Path:
    return tmp_path / "edgar_facts.db"


def _entry(start, end, val, *, filed, form="10-K", fp="FY", fy=2026, accn=None) -> dict:
    out = {
        "end": end,
        "val": val,
        "filed": filed,
        "form": form,
        "fp": fp,
        "fy": fy,
        "accn": accn or f"0001045810-{filed[2:4]}-{abs(hash((end, val, form))) % 1000000:06d}",
    }
    if start:
        out["start"] = start
    return out


def _load(cache_db: Path, facts_db: Path, symbol: str, cik: str, concepts: dict) -> None:
    """Register the company and ingest a companyfacts-shaped payload."""
    conn = connect(cache_db)
    try:
        _ensure_schema(conn)
        _upsert_company(conn, symbol=symbol, cik=cik, sic=3674, sic_desc="Semis")
        conn.commit()
    finally:
        conn.close()

    conn = connect(facts_db)
    try:
        ensure_bulk_schema(conn)
        concept_id, flush = concept_interner(conn)
        rows = fact_rows({"cik": int(cik), "facts": {"us-gaap": concepts}}, concept_id)
        flush()
        upsert_facts(conn, rows)
        conn.commit()
    finally:
        conn.close()


def test_accession_round_trips_through_its_integer_form() -> None:
    assert accn_to_int("0001193125-10-016098") == 119312510016098
    assert accn_to_str(119312510016098) == "0001193125-10-016098"
    assert accn_to_int("not-an-accession") is None


def test_alias_tags_are_merged_not_truncated_at_the_switch(
    cache_db: Path, facts_db: Path
) -> None:
    """NVDA reports revenue under RevenueFromContractWithCustomer… only through
    FY2022, then Revenues. Stopping at the first non-empty tag reported FY2022 as
    the latest annual revenue — off by three years and ~$189B."""
    _load(
        cache_db,
        facts_db,
        "NVDA",
        "1045810",
        {
            "RevenueFromContractWithCustomerExcludingAssessedTax": {
                "units": {"USD": [_entry("2021-02-01", "2022-01-30", 26_914e6, filed="2022-03-18")]}
            },
            "Revenues": {
                "units": {"USD": [_entry("2025-01-27", "2026-01-25", 215_938e6, filed="2026-02-25")]}
            },
        },
    )
    annual = latest_financials("NVDA", cache_db=cache_db, facts_db=facts_db)["metrics"]["revenue"][
        "annual"
    ]
    assert {a["period_end"] for a in annual} == {"2022-01-30", "2026-01-25"}


def test_period_bucket_reads_duration_not_form() -> None:
    """A 10-Q carries year-to-date and trailing-12-month durations beside the
    quarter; trusting `form`/`fp` filed a $58B 'quarterly' NVDA net income."""
    assert _period_bucket("2025-01-27", "2026-01-25", "FY") == "annual"
    assert _period_bucket("2026-01-26", "2026-04-26", "Q1") == "quarterly"
    # a 12-month duration reported inside a 10-Q is annual, whatever the form says
    assert _period_bucket("2025-04-28", "2026-04-26", "Q1") == "annual"
    # 6- and 9-month year-to-date figures belong to neither series
    assert _period_bucket("2025-10-27", "2026-04-26", "Q1") is None
    # balance-sheet instants have no start — fall back to the fiscal-period label
    assert _period_bucket("", "2026-01-25", "FY") == "annual"
    assert _period_bucket("", "2026-04-26", "Q1") == "quarterly"


def test_fiscal_year_end_balance_is_not_stolen_by_a_quarterly_comparative(
    cache_db: Path, facts_db: Path
) -> None:
    """The FY2026 equity instant is filed twice: once in the 10-K as `FY`, then
    repeated in the next 10-Q as a `Q1` comparative. Taking the later-filed copy
    keeps the same value but the wrong label, stranding the annual series."""
    _load(
        cache_db,
        facts_db,
        "NVDA",
        "1045810",
        {
            "StockholdersEquity": {
                "units": {
                    "USD": [
                        _entry(None, "2026-01-25", 157_293e6, filed="2026-02-25"),
                        _entry(
                            None, "2026-01-25", 157_293e6, filed="2026-05-20",
                            form="10-Q", fp="Q1", fy=2027,
                        ),
                    ]
                }
            }
        },
    )
    annual = latest_financials("NVDA", cache_db=cache_db, facts_db=facts_db)["metrics"]["equity"][
        "annual"
    ]
    assert [a["period_end"] for a in annual] == ["2026-01-25"]
    assert annual[0]["val"] == pytest.approx(157_293e6)


def test_as_of_is_point_in_time_and_restatements_supersede(
    cache_db: Path, facts_db: Path
) -> None:
    """`filed` is the whole point: as_of must show what had been published then,
    and without it the most recent filing of a period wins."""
    _load(
        cache_db,
        facts_db,
        "ACME",
        "99",
        {
            "Revenues": {
                "units": {
                    "USD": [
                        _entry("2025-01-01", "2025-12-31", 100.0, filed="2026-02-01"),
                        # same period, filed again later — the amendment restates it
                        _entry(
                            "2025-01-01", "2025-12-31", 90.0,
                            filed="2026-08-01", form="10-K/A",
                        ),
                    ]
                }
            }
        },
    )
    latest = latest_financials("ACME", cache_db=cache_db, facts_db=facts_db)["metrics"]["revenue"][
        "annual"
    ]
    assert latest[0]["val"] == 90.0  # restatement wins

    pit = latest_financials(
        "ACME", as_of="2026-03-01", cache_db=cache_db, facts_db=facts_db
    )["metrics"]
    assert pit["revenue"]["annual"][0]["val"] == 100.0  # the restatement wasn't public yet

    stale = latest_financials(
        "ACME", as_of="2026-01-01", cache_db=cache_db, facts_db=facts_db
    )
    assert stale["metrics"] == {}


def test_same_day_amendment_does_not_overwrite_the_original(
    cache_db: Path, facts_db: Path
) -> None:
    """0.18% of real facts share (cik, concept, unit, period, form, filed) with a
    DIFFERENT value — a 10-Q/A restating the day it was filed, or an issuer filing
    several 424B2s. Without `accn` in the key they silently overwrite each other."""
    _load(
        cache_db,
        facts_db,
        "ACME",
        "7332",
        {
            "AccountsPayableAndAccruedLiabilitiesCurrent": {
                "units": {
                    "USD": [
                        _entry(
                            None, "2011-12-31", 576_527_000, filed="2013-02-19",
                            form="10-Q/A", fp="Q4", accn="0000007332-13-000004",
                        ),
                        _entry(
                            None, "2011-12-31", 575_327_000, filed="2013-02-19",
                            form="10-Q/A", fp="Q4", accn="0000007332-13-000005",
                        ),
                    ]
                }
            }
        },
    )
    conn = connect(facts_db)
    try:
        kept = conn.execute("SELECT COUNT(*) FROM facts WHERE cik = 7332").fetchone()[0]
    finally:
        conn.close()
    assert kept == 2


def test_any_concept_is_reachable_by_name_not_just_the_canonical_twelve(
    cache_db: Path, facts_db: Path
) -> None:
    """The twelve-metric view is a convenience; a filer reports ~250 concepts and
    the interesting question is usually about one of the other 240."""
    _load(
        cache_db,
        facts_db,
        "ACME",
        "99",
        {
            "OperatingLeaseLiabilityNoncurrent": {
                "label": "Operating Lease, Liability, Noncurrent",
                "description": "Present value of lessee's discounted obligation.",
                "units": {"USD": [_entry(None, "2026-03-31", 12_500.0, filed="2026-05-01")]},
            }
        },
    )
    series = concept_series(
        "ACME", "OperatingLeaseLiabilityNoncurrent", cache_db=cache_db, facts_db=facts_db
    )
    assert series["label"] == "Operating Lease, Liability, Noncurrent"
    assert series["unit"] == "USD"
    assert series["annual"][0]["val"] == 12_500.0

    # and it is discoverable without already knowing the tag name
    hits = search_concepts("lease", facts_db=facts_db)
    assert "OperatingLeaseLiabilityNoncurrent" in {h["concept"] for h in hits}

    reported = company_concepts("ACME", cache_db=cache_db, facts_db=facts_db)
    assert reported[0]["concept"] == "OperatingLeaseLiabilityNoncurrent"


def test_abandoned_series_is_flagged_stale_per_bucket(
    cache_db: Path, facts_db: Path
) -> None:
    """JPM stopped tagging QUARTERLY `Revenues` in 2014 while its ANNUAL series
    stayed current, so the reader served a 2014 number as the latest quarter. A
    metric-level flag hides exactly this case — it has to be per bucket."""
    _load(
        cache_db,
        facts_db,
        "JPM",
        "19617",
        {
            "Revenues": {
                "units": {
                    "USD": [
                        _entry("2014-07-01", "2014-09-30", 24_246e6, filed="2014-11-03",
                               form="10-Q", fp="Q3", fy=2014),
                        _entry("2025-01-01", "2025-12-31", 180_000e6, filed="2026-02-13"),
                    ]
                }
            }
        },
    )
    revenue = latest_financials("JPM", cache_db=cache_db, facts_db=facts_db)["metrics"]["revenue"]
    assert revenue["stale"] == ["quarterly"]
    assert revenue["quarterly_last_filed"] == "2014-11-03"
    # the annual series is current and must NOT be flagged
    assert revenue["annual"][0]["period_end"] == "2025-12-31"


def test_unknown_symbol_returns_none(cache_db: Path, facts_db: Path) -> None:
    assert latest_financials("NOSUCH", cache_db=cache_db, facts_db=facts_db) is None
    assert concept_series("NOSUCH", "Revenues", cache_db=cache_db, facts_db=facts_db) is None


@pytest.mark.asyncio
async def test_fundamentals_resource_requires_symbol_and_reports_gaps(tmp_path: Path) -> None:
    from ai.perception.resources import query

    missing = await query("fundamentals", {}, db_path=tmp_path / "x.db")
    assert "symbol" in str(missing["rows"][0].get("error"))

    # the alias agents actually guess resolves to the same resource
    aliased = await query("financials", {"symbol": "NOSUCH"}, db_path=tmp_path / "x.db")
    assert aliased["resource"] == "fundamentals"
