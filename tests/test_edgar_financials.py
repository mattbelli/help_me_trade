"""EDGAR companyfacts financials — the traps measured against real NVDA data.

Every case here is a bug this module actually shipped with and that live EDGAR
data exposed: alias truncation, form-based period classification, and the
fiscal-year-end balance repeated as a quarterly comparative.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from db.base import connect
from research.fundamentals import (
    _ensure_schema,
    _parse_company_facts,
    _period_bucket,
    _upsert_company,
    _upsert_financials,
    latest_financials,
)


@pytest.fixture
def cache_db(tmp_path: Path) -> Path:
    return tmp_path / "fundamentals.db"


def _facts(concepts: dict) -> dict:
    return {"facts": {"us-gaap": concepts}}


def _entry(start, end, val, *, filed, form="10-K", fp="FY", fy=2026) -> dict:
    out = {"end": end, "val": val, "filed": filed, "form": form, "fp": fp, "fy": fy}
    if start:
        out["start"] = start
    return out


def test_alias_tags_are_merged_not_truncated_at_the_switch() -> None:
    """NVDA reports revenue under RevenueFromContractWithCustomer… only through
    FY2022, then Revenues. Stopping at the first non-empty tag reported FY2022 as
    the latest annual revenue — off by three years and ~$189B."""
    payload = _facts(
        {
            "RevenueFromContractWithCustomerExcludingAssessedTax": {
                "units": {"USD": [_entry("2021-02-01", "2022-01-30", 26_914e6, filed="2022-03-18")]}
            },
            "Revenues": {
                "units": {"USD": [_entry("2025-01-27", "2026-01-25", 215_938e6, filed="2026-02-25")]}
            },
        }
    )
    rows = _parse_company_facts(payload)
    ends = {r[4] for r in rows if r[0] == "revenue"}
    assert ends == {"2022-01-30", "2026-01-25"}


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
    cache_db: Path,
) -> None:
    """The FY2026 equity instant is filed twice: once in the 10-K as `FY`, then
    repeated in the next 10-Q as a `Q1` comparative. Taking the later-filed copy
    keeps the same value but the wrong label, stranding the annual series."""
    conn = connect(cache_db)
    try:
        _ensure_schema(conn)
        _upsert_company(conn, symbol="NVDA", cik="1045810", sic=3674, sic_desc="Semis")
        _upsert_financials(
            conn,
            "1045810",
            [
                ("equity", "StockholdersEquity", "USD", "", "2026-01-25", 2026, "FY", "10-K",
                 "2026-02-25", 157_293e6),
                ("equity", "StockholdersEquity", "USD", "", "2026-01-25", 2027, "Q1", "10-Q",
                 "2026-05-20", 157_293e6),
            ],
        )
        conn.commit()
    finally:
        conn.close()

    out = latest_financials("NVDA", cache_db=cache_db)
    annual = out["metrics"]["equity"]["annual"]
    assert [a["period_end"] for a in annual] == ["2026-01-25"]
    assert annual[0]["val"] == pytest.approx(157_293e6)


def test_as_of_is_point_in_time_and_restatements_supersede(cache_db: Path) -> None:
    """`filed` is the whole point: as_of must show what had been published then,
    and without it the most recent filing of a period wins."""
    conn = connect(cache_db)
    try:
        _ensure_schema(conn)
        _upsert_company(conn, symbol="ACME", cik="99", sic=3674, sic_desc="Semis")
        _upsert_financials(
            conn,
            "99",
            [
                # same period, filed twice — the later one restates it
                ("revenue", "Revenues", "USD", "2025-01-01", "2025-12-31", 2025, "FY", "10-K",
                 "2026-02-01", 100.0),
                ("revenue", "Revenues", "USD", "2025-01-01", "2025-12-31", 2025, "FY", "10-K/A",
                 "2026-08-01", 90.0),
            ],
        )
        conn.commit()
    finally:
        conn.close()

    latest = latest_financials("ACME", cache_db=cache_db)["metrics"]["revenue"]["annual"]
    assert latest[0]["val"] == 90.0  # restatement wins

    pit = latest_financials("ACME", as_of="2026-03-01", cache_db=cache_db)["metrics"]
    assert pit["revenue"]["annual"][0]["val"] == 100.0  # the restatement wasn't public yet

    assert latest_financials("ACME", as_of="2026-01-01", cache_db=cache_db)["metrics"] == {}


def test_unknown_symbol_returns_none(cache_db: Path) -> None:
    assert latest_financials("NOSUCH", cache_db=cache_db) is None


@pytest.mark.asyncio
async def test_fundamentals_resource_requires_symbol_and_reports_gaps(tmp_path: Path) -> None:
    from ai.perception.resources import query

    missing = await query("fundamentals", {}, db_path=tmp_path / "x.db")
    assert "symbol" in str(missing["rows"][0].get("error"))

    # the alias agents actually guess resolves to the same resource
    aliased = await query("financials", {"symbol": "NOSUCH"}, db_path=tmp_path / "x.db")
    assert aliased["resource"] == "fundamentals"
