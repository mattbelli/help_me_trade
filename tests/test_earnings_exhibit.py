"""EDGAR Item 2.02 earnings-release reader — network-free.

The cases are the traps measured against live EDGAR on 2026-07-30: the atom
summary carries the item list (so filtering costs no request), the `<updated>`
offset is the trustworthy timestamp, the header block names the EX-99.1, and a
release's numbers only survive extraction if tables are kept row-wise.
"""

from __future__ import annotations

from pathlib import Path

from signals.earnings_exhibit import (
    exhibit_text,
    parse_current_feed,
    parse_filing_header,
    session_of,
)

_FEED = """<?xml version="1.0" encoding="ISO-8859-1" ?>
<feed xmlns="http://www.w3.org/2005/Atom">
<entry>
<title>8-K - Columbia Financial, Inc./MD/ (0002115119) (Filer)</title>
<link rel="alternate" type="text/html"
 href="https://www.sec.gov/Archives/edgar/data/2115119/000211511926000026/0002115119-26-000026-index.htm"/>
<summary type="html"> &lt;b&gt;Filed:&lt;/b&gt; 2026-07-30
&lt;br&gt;Item 2.02: Results of Operations and Financial Condition
&lt;br&gt;Item 9.01: Financial Statements and Exhibits
</summary>
<updated>2026-07-30T17:24:56-04:00</updated>
</entry>
<entry>
<title>8-K - DT Cloud Star Acquisition Corp (0002017950) (Filer)</title>
<link rel="alternate" type="text/html"
 href="https://www.sec.gov/Archives/edgar/data/2017950/000149315226035502/0001493152-26-035502-index.htm"/>
<summary type="html"> &lt;b&gt;Filed:&lt;/b&gt; 2026-07-30
&lt;br&gt;Item 8.01: Other Events
</summary>
<updated>2026-07-30T17:28:14-04:00</updated>
</entry>
</feed>
"""


def test_feed_keeps_only_item_202_and_reads_the_offset_timestamp() -> None:
    """The summary lists the items, so earnings filings are separated from the
    other ~56% of 8-Ks without fetching a single filing index."""
    got = parse_current_feed(_FEED)
    assert len(got) == 1
    entry = got[0]
    assert entry["cik"] == 2115119
    assert entry["accession"] == "0002115119-26-000026"
    assert entry["folder"] == "000211511926000026"
    assert entry["company"] == "Columbia Financial, Inc./MD/"
    assert entry["items"] == "2.02,9.01"
    # the offset is present and is what makes the stamp usable
    assert entry["accepted_et"] == "2026-07-30T17:24:56-04:00"


def test_session_needs_an_offset_and_honours_dst() -> None:
    assert session_of("2026-07-30T16:06:23-04:00") == "amc"  # AMZN, tape-confirmed
    assert session_of("2026-02-25T16:31:25-05:00") == "amc"  # EST, not a fixed offset
    assert session_of("2026-07-14T06:30:38-04:00") == "bmo"
    assert session_of("2026-07-30T13:00:00-04:00") == "dmh"
    # A naive stamp names no session — the submissions JSON's `Z` is why.
    assert session_of("2026-07-30T16:06:23") is None
    assert session_of("nonsense") is None


_HEADER = """<html><body><pre>
&lt;ACCEPTANCE-DATETIME&gt;20260730160623
ACCESSION NUMBER:		0001018724-26-000024
&lt;TYPE&gt;8-K
&lt;SEQUENCE&gt;1
&lt;FILENAME&gt;amzn-20260730.htm
&lt;TYPE&gt;EX-99.1
&lt;SEQUENCE&gt;2
&lt;FILENAME&gt;amzn-20260630xex991.htm
&lt;TYPE&gt;GRAPHIC
&lt;FILENAME&gt;amazon_logo.jpg
</pre></body></html>"""


def test_header_yields_both_the_native_stamp_and_the_exhibit() -> None:
    """One request answers both questions, which is why it is the request made:
    the native ACCEPTANCE-DATETIME is Eastern and authoritative, and the same
    block names the EX-99.1 among a dozen other documents."""
    accepted, exhibit = parse_filing_header(_HEADER)
    assert accepted == "20260730160623"
    assert exhibit == "amzn-20260630xex991.htm"


def test_header_without_an_exhibit_is_not_an_error() -> None:
    accepted, exhibit = parse_filing_header(
        "&lt;ACCEPTANCE-DATETIME&gt;20260730160623 &lt;TYPE&gt;8-K "
        "&lt;FILENAME&gt;only-the-8k.htm"
    )
    assert accepted == "20260730160623"
    assert exhibit is None


def test_extraction_keeps_numbers_attached_to_their_labels() -> None:
    """A tag-stripper turns a release into a column of orphaned numbers — the
    labels live in sibling cells. Rows must survive as rows."""
    html = """
    <html><body>
      <p>AMAZON.COM ANNOUNCES SECOND QUARTER RESULTS</p>
      <p>AWS segment sales increased 37% year-over-year to $33.1 billion.</p>
      <table>
        <tr><th>Segment</th><th>Q2 2026</th><th>Q2 2025</th></tr>
        <tr><td>North America</td><td>116,200</td><td>100,100</td></tr>
        <tr><td>AWS</td><td>33,100</td><td>24,200</td></tr>
      </table>
      <script>ignored()</script><style>.x{}</style>
    </body></html>"""
    text = exhibit_text(html)
    assert "AWS segment sales increased 37%" in text
    assert "North America | 116,200 | 100,100" in text
    assert "Segment | Q2 2026 | Q2 2025" in text
    assert "ignored()" not in text


def test_symbol_for_cik_prefers_the_common_share(tmp_path: Path) -> None:
    """Length-only used to pick BML-PG over BAC (preferreds are often shorter)."""
    from db.base import connect
    from research.fundamentals import _ensure_schema, _upsert_company
    from signals.earnings_exhibit import _symbol_for_cik

    db = tmp_path / "companies.db"
    conn = connect(db)
    try:
        _ensure_schema(conn)
        for symbol in ("BAC-PB", "BML-PG", "BAC", "BAC-PK"):
            _upsert_company(conn, symbol=symbol, cik="70858", sic=6021, sic_desc="Banks")
        conn.commit()
    finally:
        conn.close()
    assert _symbol_for_cik(70858, companies_db=db) == "BAC"
    assert _symbol_for_cik(999, companies_db=db) is None


def test_ingest_skips_a_known_accession_without_http(tmp_path: Path) -> None:
    from db.base import connect
    from signals.earnings_exhibit import _ingest_entry, ensure_release_schema

    db = tmp_path / "earnings.db"
    conn = connect(db)
    try:
        ensure_release_schema(conn)
        conn.execute(
            """
            INSERT INTO earnings_releases
                (accession, cik, symbol, company, filed_date, accepted_et,
                 session, items, exhibit, body, chars, fetched_at)
            VALUES ('0001018724-26-000024', 1018724, 'AMZN', 'Amazon',
                    '2026-07-30', '2026-07-30T16:06:23-04:00', 'amc',
                    '2.02', 'ex.htm', 'body', 4, '2026-07-31T00:00:00Z')
            """
        )
        conn.commit()

        class Boom:
            def get(self, *_a, **_k):
                raise AssertionError("known accession must not re-fetch")

        outcome = _ingest_entry(
            conn,
            Boom(),
            {
                "accession": "0001018724-26-000024",
                "cik": 1018724,
                "folder": "000101872426000024",
                "company": "Amazon",
                "accepted_et": "2026-07-30T16:06:23-04:00",
                "items": "2.02",
            },
            listed_only=True,
        )
        assert outcome == "known"
    finally:
        conn.close()


def test_backfill_candidates_skip_names_we_already_have(tmp_path: Path) -> None:
    """Calendar and exhibits are different DBs. Estimates are not a fetch reason."""
    from datetime import date

    from db.base import connect
    from db.earnings_calendar import init_earnings_calendar_tables, upsert_earnings
    from signals.earnings_exhibit import (
        _symbols_missing_releases,
        ensure_release_schema,
    )

    calendar_db = tmp_path / "app.db"
    releases_db = tmp_path / "earnings.db"
    init_earnings_calendar_tables(calendar_db)
    upsert_earnings(
        [
            {"symbol": "AMZN", "date": "2026-07-30", "source": "edgar"},
            {"symbol": "AAPL", "date": "2026-07-31", "source": "edgar"},
            {
                "symbol": "MSFT",
                "date": "2026-08-01",
                "source": "edgar_estimate",
                "estimated": 1,
            },
        ],
        db_path=calendar_db,
    )
    conn = connect(releases_db)
    try:
        ensure_release_schema(conn)
        conn.execute(
            """
            INSERT INTO earnings_releases
                (accession, cik, symbol, company, filed_date, accepted_et,
                 session, items, exhibit, body, chars, fetched_at)
            VALUES ('0000320193-26-000001', 320193, 'AAPL', 'Apple',
                    '2026-07-31', '2026-07-31T16:30:00-04:00', 'amc',
                    '2.02', 'ex.htm', 'body', 4, '2026-07-31T21:00:00Z')
            """
        )
        conn.commit()
    finally:
        conn.close()

    missing = _symbols_missing_releases(
        days=21,
        calendar_db=calendar_db,
        releases_db=releases_db,
        as_of=date(2026, 8, 13),
    )
    assert missing == ["AMZN"]


def test_release_lookup_matches_any_share_class(tmp_path: Path) -> None:
    """Alphabet stores under GOOG (shortest class); a GOOGL ask must still hit.

    Measured 2026-08-15: the mobile chat asked for GOOGL's release and read back
    "no stored release" for a company the store could legitimately hold."""
    from db.base import connect
    from research.fundamentals import _ensure_schema, _upsert_company
    from signals.earnings_exhibit import ensure_release_schema, latest_releases

    companies_db = tmp_path / "companies.db"
    conn = connect(companies_db)
    try:
        _ensure_schema(conn)
        for symbol in ("GOOG", "GOOGL"):
            _upsert_company(conn, symbol=symbol, cik="1652044", sic=7372, sic_desc="Services")
        conn.commit()
    finally:
        conn.close()

    releases_db = tmp_path / "earnings.db"
    conn = connect(releases_db)
    try:
        ensure_release_schema(conn)
        conn.execute(
            """
            INSERT INTO earnings_releases
                (accession, cik, symbol, company, filed_date, accepted_et,
                 session, items, exhibit, body, chars, fetched_at)
            VALUES ('0001652044-26-000066', 1652044, 'GOOG', 'Alphabet',
                    '2026-07-22', '2026-07-22T16:11:00-04:00', 'amc',
                    '2.02', 'ex.htm', 'body', 4, '2026-08-15T00:00:00Z')
            """
        )
        conn.commit()
    finally:
        conn.close()

    hit = latest_releases("GOOGL", db_path=releases_db, companies_db=companies_db)
    assert len(hit) == 1 and hit[0]["symbol"] == "GOOG"
    assert latest_releases("MSFT", db_path=releases_db, companies_db=companies_db) == []


def test_missing_check_is_per_company_not_per_class(tmp_path: Path) -> None:
    """A release stored under one share class covers the whole company, and an
    uncovered company yields ONE candidate, not one per class."""
    from datetime import date

    from db.base import connect
    from db.earnings_calendar import init_earnings_calendar_tables, upsert_earnings
    from research.fundamentals import _ensure_schema, _upsert_company
    from signals.earnings_exhibit import (
        _symbols_missing_releases,
        ensure_release_schema,
    )

    companies_db = tmp_path / "companies.db"
    conn = connect(companies_db)
    try:
        _ensure_schema(conn)
        for symbol in ("GOOG", "GOOGL"):
            _upsert_company(conn, symbol=symbol, cik="1652044", sic=7372, sic_desc="Services")
        for symbol in ("ZZA", "ZZB"):
            _upsert_company(conn, symbol=symbol, cik="42", sic=7372, sic_desc="Services")
        conn.commit()
    finally:
        conn.close()

    calendar_db = tmp_path / "app.db"
    init_earnings_calendar_tables(calendar_db)
    upsert_earnings(
        [
            {"symbol": s, "date": "2026-07-22", "source": "edgar"}
            for s in ("GOOG", "GOOGL", "ZZA", "ZZB")
        ],
        db_path=calendar_db,
    )

    releases_db = tmp_path / "earnings.db"
    conn = connect(releases_db)
    try:
        ensure_release_schema(conn)
        conn.execute(
            """
            INSERT INTO earnings_releases
                (accession, cik, symbol, company, filed_date, accepted_et,
                 session, items, exhibit, body, chars, fetched_at)
            VALUES ('0001652044-26-000066', 1652044, 'GOOG', 'Alphabet',
                    '2026-07-22', '2026-07-22T16:11:00-04:00', 'amc',
                    '2.02', 'ex.htm', 'body', 4, '2026-08-15T00:00:00Z')
            """
        )
        conn.commit()
    finally:
        conn.close()

    missing = _symbols_missing_releases(
        days=30,
        calendar_db=calendar_db,
        releases_db=releases_db,
        companies_db=companies_db,
        as_of=date(2026, 8, 1),
    )
    assert missing == ["ZZA"]
