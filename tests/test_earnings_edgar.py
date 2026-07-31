"""SEC EDGAR earnings dates — 8-K Item 2.02 parsing + session labelling.

⚠️ The session label comes ONLY from an offset-bearing stamp (the atom feed or
the native ACCEPTANCE-DATETIME header). The submissions JSON's `Z` lies.
"""

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
    # ⚠️ These are the untrustworthy `Z` stamps — see session_hour. The dates are
    # exact; no session can be read from this feed.
    "acceptanceDateTime": [
        "2026-04-30T20:30:41.000Z",
        "2026-05-01T18:04:00.000Z",
        "2026-01-29T21:30:33.000Z",
        "2026-01-15T14:00:00.000Z",
        "2025-10-30T20:30:35.000Z",
    ],
}


def test_parses_only_8k_item_202() -> None:
    """The 10-Q, the Form 4, and the 8-K carrying only Item 7.01 are all not earnings."""
    got = parse_recent_earnings(_RECENT)
    assert [r["date"] for r in got] == ["2026-04-30", "2026-01-29"]


def test_parsed_dates_report_no_hour_from_the_submissions_feed() -> None:
    """`filings.recent` is the one source whose timestamp cannot be trusted, so
    the date survives and the hour is honestly absent. This asserted "amc" until
    2026-07-30, which is precisely how AMZN's 16:06 ET release got labelled
    `dmh` — the test enshrined the assumption instead of checking it."""
    got = parse_recent_earnings(_RECENT)
    assert got[0] == {"date": "2026-04-30", "hour": None}


def test_limit_caps_events() -> None:
    assert len(parse_recent_earnings(_RECENT, limit=1)) == 1


def test_empty_and_malformed_blocks_are_survivable() -> None:
    assert parse_recent_earnings({}) == []
    # items shorter than form (EDGAR truncation) must not IndexError
    assert parse_recent_earnings({"form": ["8-K"], "filingDate": ["2026-04-30"]}) == []


@pytest.mark.parametrize(
    ("acceptance", "expected"),
    [
        # Offset-bearing stamps — what the atom feed and the native
        # ACCEPTANCE-DATETIME header give. These are the only trustworthy inputs.
        ("2026-07-30T16:06:23-04:00", "amc"),  # AMZN — the release the tape confirmed
        ("2026-05-20T16:21:19-04:00", "amc"),  # NVDA
        ("2026-07-14T06:30:38-04:00", "bmo"),  # JPM  — before the open
        ("2026-02-25T16:31:25-05:00", "amc"),  # NVDA in EST — offset must be honoured
        ("2026-04-30T13:00:00-04:00", "dmh"),  # genuinely intraday
        # Boundaries are inclusive of the session: at 09:30 the market IS open.
        ("2026-04-30T09:29:00-04:00", "bmo"),  # one minute before the bell
        ("2026-04-30T09:30:00-04:00", "dmh"),  # the bell itself
        ("2026-04-30T16:00:00-04:00", "amc"),  # the close
        (None, None),
        ("", None),
        ("not-a-timestamp", None),
        # ⚠️ A naive stamp names no session.
        ("2026-04-30T16:30:41", None),
    ],
)
def test_session_hour_reads_only_offset_bearing_stamps(acceptance, expected) -> None:
    assert session_hour(acceptance) == expected


@pytest.mark.parametrize(
    "acceptance",
    [
        "2026-07-30T16:06:23.000Z",  # AMZN — value is really 16:06 ET, tape confirmed
        "2026-05-20T20:21:19.000Z",  # NVDA — value really is UTC
        "2026-07-14T06:30:38.000Z",  # JPM  — value is really 06:30 ET
    ],
)
def test_submissions_json_z_is_refused_because_it_lies(acceptance) -> None:
    """`acceptanceDateTime` ends in `Z` but the value is only sometimes UTC —
    8 of 18 earnings 8-Ks measured 2026-07-30 carried an Eastern value with a
    spurious `Z`, and nothing distinguishes the two. Assuming UTC labelled AMZN's
    16:06 ET release `dmh` (12:06); assuming Eastern breaks NVDA the other way.
    Refusing it is the only honest read — take the hour from the atom feed or the
    native ACCEPTANCE-DATETIME header instead.

    Note `datetime.fromisoformat` accepts `Z` happily, so this must be rejected
    by shape or the bug quietly returns."""
    assert session_hour(acceptance) is None


@pytest.mark.asyncio
async def test_afetch_returns_empty_for_no_symbols() -> None:
    assert await afetch_edgar_earnings([]) == []
