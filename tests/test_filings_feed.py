"""Hermetic tests for the filings feed — parsers + store, no network."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from db.filings import (
    has_uid,
    init_filings_tables,
    insert_events,
    list_recent,
    remember_uid,
)
from signals.filings import edgar as edgar_mod
from signals.filings.congress import map_trade
from signals.filings.edgar import (
    _ATOM_URL,
    format_usd,
    parse_atom_entries,
    parse_form4,
    poll_form4_once,
)

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


def test_remember_uid_is_seen_but_not_a_feed_row(tmp_path) -> None:
    db = tmp_path / "filings.db"
    remember_uid("form4:skip-me", db_path=db)
    assert has_uid("form4:skip-me", db_path=db)
    assert list_recent(db_path=db) == []


def test_form4_atom_url_uses_owner_only() -> None:
    url = _ATOM_URL.format(form="4", owner="only", count=100)
    assert "owner=only" in url
    assert "type=4" in url
    d13 = _ATOM_URL.format(form="SC+13D", owner="include", count=40)
    assert "owner=include" in d13
    assert "SC+13D" in d13


@pytest.fixture(autouse=True)
def _fast_edgar(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(edgar_mod, "_REQUEST_INTERVAL_SEC", 0.0)
    monkeypatch.setattr(edgar_mod, "_PASS_BUDGET_SEC", 90.0)
    edgar_mod._not_feedworthy.clear()
    edgar_mod._last_request_at = 0.0


class _FakeClient:
    def __init__(self, routes: list[tuple[Any, Any]]) -> None:
        self.routes = routes
        self.urls: list[str] = []

    def __enter__(self) -> _FakeClient:
        return self

    def __exit__(self, *_a: object) -> None:
        return None

    def get(self, url: str) -> httpx.Response:
        self.urls.append(url)
        for pred, payload in self.routes:
            if pred(url):
                if callable(payload):
                    payload = payload(url)
                if isinstance(payload, httpx.Response):
                    return payload
                status, body = payload
                content = body if isinstance(body, bytes) else str(body).encode()
                return httpx.Response(
                    status, content=content, request=httpx.Request("GET", url)
                )
        raise AssertionError(f"unexpected EDGAR url: {url}")


def _index_json(xml_name: str = "form4.xml") -> bytes:
    return json.dumps({"directory": {"item": [{"name": xml_name}]}}).encode()


def test_poll_form4_asks_owner_only_and_emits_a_buy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _FakeClient(
        [
            (lambda u: "type=4" in u and "owner=only" in u, (200, ATOM)),
            (lambda u: u.endswith("/index.json"), (200, _index_json())),
            (lambda u: u.endswith("/form4.xml"), (200, FORM4)),
        ]
    )
    monkeypatch.setattr(edgar_mod, "_client", lambda: client)

    events = poll_form4_once(seen=lambda _uid: False, min_usd=100_000)
    assert len(events) == 1
    assert events[0]["symbol"] == "ACME"
    assert events[0]["value_usd"] == 10000 * 25.50
    assert any("owner=only" in u for u in client.urls)
    assert all("owner=include" not in u for u in client.urls)


def test_poll_form4_remembers_a_sell_so_restart_does_not_refetch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sell_only = FORM4.replace(
        "<transactionCode>P</transactionCode>",
        "<transactionCode>A</transactionCode>",
    )
    remembered: list[str] = []
    client = _FakeClient(
        [
            (lambda u: "output=atom" in u, (200, ATOM)),
            (lambda u: u.endswith("/index.json"), (200, _index_json())),
            (lambda u: u.endswith("/form4.xml"), (200, sell_only)),
        ]
    )
    monkeypatch.setattr(edgar_mod, "_client", lambda: client)

    events = poll_form4_once(
        seen=lambda _uid: False,
        remember=remembered.append,
        min_usd=100_000,
    )
    assert events == []
    assert remembered == ["form4:0001111111-26-000001"]

    client.urls.clear()
    events = poll_form4_once(
        seen=lambda _uid: False,
        remember=remembered.append,
        min_usd=100_000,
    )
    assert events == []
    assert not any(u.endswith(".xml") or u.endswith("/index.json") for u in client.urls)


def test_get_retries_once_on_429(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(edgar_mod.time, "sleep", lambda _s: None)
    hits = {"n": 0}

    class _Flaky:
        def get(self, url: str) -> httpx.Response:
            hits["n"] += 1
            if hits["n"] == 1:
                return httpx.Response(429, request=httpx.Request("GET", url))
            return httpx.Response(
                200, content=b"ok", request=httpx.Request("GET", url)
            )

    resp = edgar_mod._get(_Flaky(), "https://www.sec.gov/ping")
    assert resp.status_code == 200
    assert hits["n"] == 2


def test_poll_form4_stops_when_pass_budget_expires(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(edgar_mod, "_PASS_BUDGET_SEC", 0.0)
    xml_hits = {"n": 0}

    def _xml(_url: str) -> tuple[int, str]:
        xml_hits["n"] += 1
        return (200, FORM4)

    client = _FakeClient(
        [
            (lambda u: "output=atom" in u, (200, ATOM)),
            (lambda u: u.endswith("/index.json") or u.endswith(".xml"), _xml),
        ]
    )
    monkeypatch.setattr(edgar_mod, "_client", lambda: client)
    events = poll_form4_once(seen=lambda _uid: False)
    assert events == []
    assert xml_hits["n"] == 0


def test_poll_form4_raises_on_html_atom(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _FakeClient(
        [(lambda u: "output=atom" in u, (200, "Due to high volume of requests"))]
    )
    monkeypatch.setattr(edgar_mod, "_client", lambda: client)
    with pytest.raises(RuntimeError, match="not XML"):
        poll_form4_once(seen=lambda _uid: False)


@pytest.mark.asyncio
async def test_filings_edgar_once_keeps_form4_when_13d_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    form4 = [
        {
            "uid": "form4:keep",
            "kind": "insider_buy",
            "symbol": "ACME",
            "filed_at": "2026-08-14T12:00:00-04:00",
            "value_label": "$255K",
        }
    ]
    stored: list[dict] = []
    monkeypatch.setattr(
        "signals.filings.edgar.poll_form4_once", lambda **_kw: form4
    )

    def _boom(**_kw: object) -> list:
        raise RuntimeError("13d down")

    monkeypatch.setattr("signals.filings.edgar.poll_13d_once", _boom)
    monkeypatch.setattr(
        "db.filings.insert_events",
        lambda events, **_kw: stored.extend(events) or len(events),
    )

    from api.main import _filings_edgar_once

    await _filings_edgar_once()
    assert stored == form4


@pytest.mark.asyncio
async def test_filings_edgar_once_raises_when_both_arms_fail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _boom(**_kw: object) -> list:
        raise RuntimeError("edgar down")

    monkeypatch.setattr("signals.filings.edgar.poll_form4_once", _boom)
    monkeypatch.setattr("signals.filings.edgar.poll_13d_once", _boom)
    monkeypatch.setattr("db.filings.insert_events", lambda events, **_kw: len(events))

    from api.main import _filings_edgar_once

    with pytest.raises(RuntimeError, match="both Form 4 and 13D"):
        await _filings_edgar_once()
