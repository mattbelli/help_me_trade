"""Hermetic tests for the FINRA short interest fetcher (no network)."""

from __future__ import annotations

from signals.finra import _parse_rows, _to_float

CSV = '''"accountingYearMonthNumber","symbolCode","issueName","issuerServicesGroupExchangeCode","marketClassCode","currentShortPositionQuantity","previousShortPositionQuantity","stockSplitFlag","averageDailyVolumeQuantity","daysToCoverQuantity","revisionFlag","changePercent","changePreviousNumber","settlementDate"
"20260515","AAPL","Apple Inc. Common Stock","R","NNM","138782718","134675274",,"50565316","2.74",,"3.05","4107444","2026-05-15"
"20260615","AAPL","Apple Inc. Common Stock","R","NNM","144248476","155886024",,"52343843","2.76",,"-7.47","-11637548","2026-06-15"
'''


def test_parse_rows_and_latest_selection() -> None:
    rows = _parse_rows(CSV)
    assert len(rows) == 2
    latest = max(rows, key=lambda r: r["settlementDate"])
    assert latest["settlementDate"] == "2026-06-15"
    assert _to_float(latest["currentShortPositionQuantity"]) == 144248476.0
    assert _to_float(latest["daysToCoverQuantity"]) == 2.76
    assert _to_float(latest["changePercent"]) == -7.47


def test_to_float_handles_blanks() -> None:
    assert _to_float("") is None
    assert _to_float(None) is None
    assert _to_float("not-a-number") is None
