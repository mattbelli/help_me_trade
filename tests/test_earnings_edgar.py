"""SEC EDGAR earnings dates — 8-K Item 2.02 parsing + UTC->ET session labelling."""

from __future__ import annotations

import pytest

from signals.earnings_edgar import (
    afetch_edgar_earnings,
    parse_recent_earnings,
    session_hour,
)

# A filings.recent block, shaped like EDGAR's: column-aligned arrays, newest first.
_RECENT = {
    "form": ["8-K", "10-Q", "8-K", "4", "8-K"],
    "filingDate": ["2026-04-30", "2026-05-01", "2026-01-29", "2026-01-15", "2025-10-30"],
    "items": ["2.02,9.01", "", "2.02,9.01", "", "7.01"],
    "acceptanceDateTime": [
        "2026-04-30T20:30:41.000Z",  # 16:30 ET -> amc
        "2026-05-01T18:04:00.000Z",
        "2026-01-29T21:30:33.000Z",  # 16:30 ET -> amc
        "2026-01-15T14:00:00.000Z",
        "2025-10-30T20:30:35.000Z",
    ],
}


def test_parses_only_8k_item_202() -> None:
    """The 10-Q, the Form 4, and the 8-K carrying only Item 7.01 are all not earnings."""
    got = parse_recent_earnings(_RECENT)
    assert [r["date"] for r in got] == ["2026-04-30", "2026-01-29"]


def test_parsed_dates_carry_session_hour() -> None:
    got = parse_recent_earnings(_RECENT)
    assert got[0] == {"date": "2026-04-30", "hour": "amc"}


def test_limit_caps_events() -> None:
    assert len(parse_recent_earnings(_RECENT, limit=1)) == 1


def test_empty_and_malformed_blocks_are_survivable() -> None:
    assert parse_recent_earnings({}) == []
    # items shorter than form (EDGAR truncation) must not IndexError
    assert parse_recent_earnings({"form": ["8-K"], "filingDate": ["2026-04-30"]}) == []


@pytest.mark.parametrize(
    ("acceptance", "expected"),
    [
        # ⚠️ the stamps are UTC. Converting to ET FIRST is load-bearing — compared raw,
        # every before-open filer reads as intraday. Real observed values:
        ("2026-04-30T20:30:41.000Z", "amc"),  # AAPL  16:30 ET
        ("2026-05-20T20:21:19.000Z", "amc"),  # NVDA  16:21 ET
        ("2026-07-14T10:30:38.000Z", "bmo"),  # JPM   06:30 ET  <- would be "dmh" unconverted
        ("2026-05-21T10:59:00.000Z", "bmo"),  # WMT   06:59 ET
        ("2026-04-30T17:00:00.000Z", "dmh"),  # 13:00 ET, genuinely intraday
        # Boundaries are inclusive of the session: at 09:30 the market IS open.
        ("2026-04-30T13:29:00.000Z", "bmo"),  # 09:29 ET — one minute before the bell
        ("2026-04-30T13:30:00.000Z", "dmh"),  # 09:30 ET — the bell itself
        ("2026-04-30T20:00:00.000Z", "amc"),  # 16:00 ET — the close
        (None, None),
        ("", None),
        ("not-a-timestamp", None),
    ],
)
def test_session_hour_converts_utc_to_eastern(acceptance, expected) -> None:
    assert session_hour(acceptance) == expected


def test_session_hour_handles_dst_boundary() -> None:
    """17:00Z is 13:00 EDT (summer) but 12:00 EST (winter) — both intraday, but the
    conversion must be zone-aware, not a fixed offset."""
    assert session_hour("2026-07-14T20:30:00.000Z") == "amc"  # 16:30 EDT
    assert session_hour("2026-01-14T21:30:00.000Z") == "amc"  # 16:30 EST


@pytest.mark.asyncio
async def test_afetch_returns_empty_for_no_symbols() -> None:
    assert await afetch_edgar_earnings([]) == []
