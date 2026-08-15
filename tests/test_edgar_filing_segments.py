"""Per-filing XBRL segment parser — DERA-parity is the whole contract.

The parser feeds the same `segment_facts` table DERA fills a quarter later, so
every convention here (label serialization, unit collapse, month-end rounding)
is pinned to what DERA measurably does — a drift means the same fact stored
twice under two keys the day DERA republishes the filing.
"""

from __future__ import annotations

import sqlite3
from datetime import date

from research.edgar_filing_segments import (
    _month_end,
    _parse_form_idx,
    _quarter_labels,
    parse_instance,
)

_INSTANCE = b"""<?xml version="1.0"?>
<xbrl xmlns="http://www.xbrl.org/2003/instance"
      xmlns:xbrldi="http://xbrl.org/2006/xbrldi"
      xmlns:us-gaap="http://fasb.org/us-gaap/2025"
      xmlns:acme="http://www.acme.com/20260627">
  <unit id="usd"><measure>iso4217:USD</measure></unit>
  <unit id="eps"><divide>
    <unitNumerator><measure>iso4217:USD</measure></unitNumerator>
    <unitDenominator><measure>xbrli:shares</measure></unitDenominator>
  </divide></unit>
  <unit id="perunit"><divide>
    <unitNumerator><measure>iso4217:USD</measure></unitNumerator>
    <unitDenominator><measure>acme:Unit</measure></unitDenominator>
  </divide></unit>
  <context id="seg">
    <entity>
      <identifier scheme="http://www.sec.gov/CIK">0000123456</identifier>
      <segment>
        <xbrldi:explicitMember dimension="srt:ConsolidationItemsAxis">us-gaap:OperatingSegmentsMember</xbrldi:explicitMember>
        <xbrldi:explicitMember dimension="us-gaap:StatementBusinessSegmentsAxis">acme:CloudMember</xbrldi:explicitMember>
      </segment>
    </entity>
    <period><startDate>2026-03-29</startDate><endDate>2026-06-27</endDate></period>
  </context>
  <context id="geo">
    <entity>
      <identifier scheme="http://www.sec.gov/CIK">0000123456</identifier>
      <segment>
        <xbrldi:explicitMember dimension="srt:StatementGeographicalAxis">country:US</xbrldi:explicitMember>
      </segment>
    </entity>
    <period><instant>2026-06-27</instant></period>
  </context>
  <context id="typed">
    <entity>
      <identifier scheme="http://www.sec.gov/CIK">0000123456</identifier>
      <segment>
        <xbrldi:typedMember dimension="acme:LoanAxis"><acme:Loan>7</acme:Loan></xbrldi:typedMember>
      </segment>
    </entity>
    <period><instant>2026-06-27</instant></period>
  </context>
  <context id="coreg">
    <entity>
      <identifier scheme="http://www.sec.gov/CIK">0000999999</identifier>
      <segment>
        <xbrldi:explicitMember dimension="us-gaap:StatementBusinessSegmentsAxis">acme:CloudMember</xbrldi:explicitMember>
      </segment>
    </entity>
    <period><instant>2026-06-27</instant></period>
  </context>
  <context id="plain">
    <entity><identifier scheme="http://www.sec.gov/CIK">0000123456</identifier></entity>
    <period><instant>2026-06-27</instant></period>
  </context>
  <us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax contextRef="seg" unitRef="usd">100</us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax>
  <us-gaap:EarningsPerShareDiluted contextRef="seg" unitRef="eps">1.25</us-gaap:EarningsPerShareDiluted>
  <acme:WidgetsPerThing contextRef="geo" unitRef="perunit">3.5</acme:WidgetsPerThing>
  <us-gaap:Assets contextRef="plain" unitRef="usd">555</us-gaap:Assets>
  <us-gaap:Assets contextRef="coreg" unitRef="usd">7</us-gaap:Assets>
  <us-gaap:SegmentTextBlock contextRef="seg">prose, no unit</us-gaap:SegmentTextBlock>
</xbrl>
"""


def test_parser_reproduces_dera_conventions() -> None:
    rows, skips = parse_instance(_INSTANCE, filer_cik=123456)
    by_tag = {r[1]: r for r in rows}

    # Pairs sort alphabetically with a trailing ';', affixes stripped — the
    # exact string DERA interned for the same filing.
    revenue = by_tag["RevenueFromContractWithCustomerExcludingAssessedTax"]
    assert revenue[4] == "BusinessSegments=Cloud;ConsolidationItems=OperatingSegments;"
    # 52/53-week period end rounds to the month end; a 90-day duration is qtrs=1.
    assert revenue[0] == "us-gaap" and revenue[2] == "2026-06-30" and revenue[3] == 1
    assert revenue[5] == "USD" and revenue[6] == 100.0

    # Currency-per-share collapses to the currency; other ratios keep the slash.
    assert by_tag["EarningsPerShareDiluted"][5] == "USD"
    widgets = by_tag["WidgetsPerThing"]
    assert widgets[5] == "USD/Unit"
    # Company-extension namespaces collapse to `custom`; instants are qtrs=0.
    assert widgets[0] == "custom" and widgets[3] == 0
    assert widgets[4] == "Geographical=US;"

    # Undimensioned, typed-dimension, co-registrant and unit-less facts all stay
    # out — the first is already in the corpus, the rest are DERA's to fill.
    assert "Assets" not in by_tag and "SegmentTextBlock" not in by_tag
    assert skips == {"typed": 1, "coreg": 1}


def test_month_end_rounds_both_directions() -> None:
    assert _month_end("2025-12-27") == "2025-12-31"  # AMD's FY end, forward
    assert _month_end("2026-01-03") == "2025-12-31"  # Darling's FY end, back
    assert _month_end("2026-06-30") == "2026-06-30"  # already a month end


def test_days_long_durations_round_to_zero_like_dera() -> None:
    from research.edgar_filing_segments import _qtrs

    assert _qtrs("2026-06-20", "2026-06-27") == 0  # a SPAC's issuance window
    assert _qtrs("2026-04-01", "2026-06-27") == 1
    assert _qtrs("2025-12-29", "2026-12-27") == 4  # 52/53-week fiscal year
    assert _qtrs(None, "2026-06-30") == 0  # instant


def test_form_idx_keeps_only_annual_and_quarterly_reports() -> None:
    idx = (
        "Form Type   Company Name   CIK   Date Filed   File Name\n"
        "---------------------------------------------------------\n"
        "10-Q        Alpha Beta Gamma Inc   123456   2026-07-23   edgar/data/123456/0001234560-26-000071.txt\n"
        "10-K/A      Solo Co   99   2026-07-24   edgar/data/99/0000000990-26-000001.txt\n"
        "8-K         Noise Corp   77   2026-07-23   edgar/data/77/0000000770-26-000002.txt\n"
    )
    parsed = _parse_form_idx(idx)
    assert parsed == [
        ("10-Q", 123456, "2026-07-23", "0001234560-26-000071"),
        ("10-K/A", 99, "2026-07-24", "0000000990-26-000001"),
    ]


def test_window_starts_where_dera_coverage_ends() -> None:
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE dera_quarters (quarter TEXT PRIMARY KEY, "
        "ingested_at TEXT, segment_facts INTEGER)"
    )
    conn.execute("INSERT INTO dera_quarters VALUES ('2026q1', 'x', 1)")
    assert _quarter_labels(conn, date(2026, 8, 15)) == ["2026q2", "2026q3"]
