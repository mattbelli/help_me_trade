"""Hermetic tests for the filings feed — parsers + store, no network."""

from __future__ import annotations

from db.filings import has_uid, init_filings_tables, insert_events, list_recent
from signals.filings.congress import map_trade
from signals.filings.edgar import format_usd, parse_atom_entries, parse_form4

ATOM = """<?xml version="1.0" encoding="ISO-8859-1" ?>
<feed xmlns="http://www.w3.org/2005/Atom">
<title>Latest Filings</title>
<entry>
<title>4 - DOE JANE (0001111111) (Reporting)</title>
<link rel="alternate" type="text/html" href="https://www.sec.gov/Archives/edgar/data/1111111/000111111126000001/0001111111-26-000001-index.htm"/>
<updated>2026-07-10T20:17:24-04:00</updated>
<id>urn:tag:sec.gov,2008:accession-number=0001111111-26-000001</id>
</entry>
<entry>
<title>4 - ACME CORP (0002222222) (Issuer)</title>
<link rel="alternate" type="text/html" href="https://www.sec.gov/Archives/edgar/data/2222222/000111111126000001/0001111111-26-000001-index.htm"/>
<updated>2026-07-10T20:17:24-04:00</updated>
<id>urn:tag:sec.gov,2008:accession-number=0001111111-26-000001</id>
</entry>
<entry>
<title>4/A - OLD FILING CO (0003333333) (Issuer)</title>
<link rel="alternate" type="text/html" href="https://www.sec.gov/Archives/edgar/data/3333333/x/0003333333-26-000009-index.htm"/>
<updated>2026-07-10T19:00:00-04:00</updated>
<id>urn:tag:sec.gov,2008:accession-number=0003333333-26-000009</id>
</entry>
</feed>
"""

FORM4 = """<?xml version="1.0"?>
<ownershipDocument>
  <periodOfReport>2026-07-09</periodOfReport>
  <issuer>
    <issuerCik>0002222222</issuerCik>
    <issuerName>ACME CORP</issuerName>
    <issuerTradingSymbol>ACME</issuerTradingSymbol>
  </issuer>
  <reportingOwner>
    <reportingOwnerId><rptOwnerName>DOE JANE</rptOwnerName></reportingOwnerId>
    <reportingOwnerRelationship>
      <isDirector>0</isDirector>
      <isOfficer>1</isOfficer>
      <officerTitle>Chief Executive Officer</officerTitle>
    </reportingOwnerRelationship>
  </reportingOwner>
  <nonDerivativeTable>
    <nonDerivativeTransaction>
      <transactionCoding><transactionCode>P</transactionCode></transactionCoding>
      <transactionAmounts>
        <transactionShares><value>10000</value></transactionShares>
        <transactionPricePerShare><value>25.50</value></transactionPricePerShare>
        <transactionAcquiredDisposedCode><value>A</value></transactionAcquiredDisposedCode>
      </transactionAmounts>
    </nonDerivativeTransaction>
    <nonDerivativeTransaction>
      <transactionCoding><transactionCode>S</transactionCode></transactionCoding>
      <transactionAmounts>
        <transactionShares><value>99999</value></transactionShares>
        <transactionPricePerShare><value>25.00</value></transactionPricePerShare>
        <transactionAcquiredDisposedCode><value>D</value></transactionAcquiredDisposedCode>
      </transactionAmounts>
    </nonDerivativeTransaction>
  </nonDerivativeTable>
</ownershipDocument>
"""


def test_parse_atom_groups_by_accession_and_keeps_form_type() -> None:
    recs = parse_atom_entries(ATOM)
    by_acc = {r["accession"]: r for r in recs}
    assert len(recs) == 2
    plain = by_acc["0001111111-26-000001"]
    assert plain["form"] == "4"
    assert plain["issuer"] == "ACME CORP"
    assert plain["cik"] == 2222222
    assert plain["url"].endswith("-index.htm")
    assert by_acc["0003333333-26-000009"]["form"] == "4/A"


def test_parse_form4_sums_open_market_buys_only() -> None:
    parsed = parse_form4(FORM4)
    assert parsed["symbol"] == "ACME"
    assert parsed["person"] == "DOE JANE"
    assert parsed["role"] == "Chief Executive Officer"
    assert parsed["buy_value"] == 10000 * 25.50  # the S/D row is excluded
    assert parsed["buy_shares"] == 10000
    assert parsed["traded_at"] == "2026-07-09"


def test_format_usd() -> None:
    assert format_usd(255_000) == "$255K"
    assert format_usd(7_150_000) == "$7.2M"


def test_congress_map_trade() -> None:
    raw = {
        "symbol": "NVDA",
        "representative": "Jane Politician",
        "transactionDate": "2026-06-30",
        "disclosureDate": "2026-07-02",
        "type": "P",
        "amount": "$15,001 - $50,000",
        "owner": "Spouse",
        "assetDescription": "NVIDIA Corp Common Stock (NVDA) [ST]",
        "party": "Republican",
        "state": "TX",
        "chamber": "house",
        "ptrLink": "https://example.gov/ptr.pdf",
    }
    ev = map_trade(raw)
    assert ev is not None
    assert ev["kind"] == "congress_trade"
    assert ev["side"] == "buy"
    assert ev["value_usd"] == 15001.0
    assert ev["value_label"] == "$15,001–$50,000"
    assert ev["role"] == "republican house TX"
    assert ev["filed_at"] == "2026-07-02"

    assert map_trade({**raw, "symbol": "NONE"}) is None
    assert map_trade({**raw, "type": "E (Exchange)"}) is None


def test_store_dedup_and_kind_filter(tmp_path) -> None:
    db = tmp_path / "filings.db"
    init_filings_tables(db)
    events = [
        {
            "uid": "form4:1",
            "kind": "insider_buy",
            "symbol": "ACME",
            "filed_at": "2026-07-10T20:17:24-04:00",
            "value_label": "$255K",
        },
        {
            "uid": "congress:1",
            "kind": "congress_trade",
            "symbol": "NVDA",
            "filed_at": "2026-07-02",
            "value_label": "$15,001–$50,000",
        },
        {"uid": "", "kind": "insider_buy", "filed_at": "2026-07-10"},  # invalid
        {"uid": "x", "kind": "unknown_kind", "filed_at": "2026-07-10"},  # invalid
    ]
    assert insert_events(events, db_path=db) == 2
    assert insert_events(events, db_path=db) == 0  # dedup by uid
    assert has_uid("form4:1", db_path=db)
    assert not has_uid("form4:2", db_path=db)

    rows = list_recent(db_path=db)
    assert [r["kind"] for r in rows] == ["insider_buy", "congress_trade"]
    only_congress = list_recent(kinds=["congress_trade"], db_path=db)
    assert len(only_congress) == 1
    assert only_congress[0]["detail"] == {}
