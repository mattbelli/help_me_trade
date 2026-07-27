"""Roadmap 0.2 — the gap check must fail loudly, and must not cry wolf.

The check exists because a 260-session backfill lost one day silently. A gap
detector that reports a false all-clear is worse than none, so the tests are
weighted toward the ways it could wrongly stay quiet.
"""

from __future__ import annotations

import pytest

from db.flow_corpus import init_db, upsert_bars
from tools.flow_corpus_gaps import expected_sessions, truncated


@pytest.fixture
def dbp(tmp_path):
    return tmp_path / "flow_corpus.db"


def _bars(dbp, dates, symbol="AAPL"):
    init_db(dbp)
    upsert_bars([(symbol, d, 1.0, 1.0, 1.0, 1.0, 10, "split", "test")
                 for d in dates], db_path=dbp)


def test_calendar_comes_from_equity_bars(dbp):
    """Derived, not hardcoded: the days the corpus owes prints for are the days
    the equity market traded. A hardcoded holiday list would drift."""
    _bars(dbp, ["2025-09-02", "2025-09-03", "2025-09-04"])
    assert expected_sessions("2025-09-01", "2025-09-05", dbp) == {
        "2025-09-02", "2025-09-03", "2025-09-04"}


def test_calendar_end_is_exclusive(dbp):
    _bars(dbp, ["2025-09-02", "2025-09-03"])
    assert expected_sessions("2025-09-01", "2025-09-03", dbp) == {"2025-09-02"}


def test_holidays_never_appear_as_expected(dbp):
    """Labor Day has no bars, so it is not a gap — and the zero-byte marker the
    backfill leaves for it is simply extra rather than a truncated pull."""
    _bars(dbp, ["2025-08-29", "2025-09-02"])   # 09-01 is Labor Day
    assert "2025-09-01" not in expected_sessions("2025-08-29", "2025-09-03", dbp)


# --- truncation: the check that would have caught 2025-09-02 ----------------

MEDIAN = 270_000_000


def _sizes(**kw):
    return dict(kw)


def test_a_partial_download_is_reported():
    """2025-09-02 downloaded at 64.5 MB against a ~270 MB median and then passed
    the backfill's `size > 0` skip guard. That is the failure this catches."""
    sizes = {f"d{i}": MEDIAN for i in range(9)}
    sizes["short"] = 64_500_000
    out = truncated(sizes, set(sizes))
    assert [d for d, _ in out] == ["short"]


def test_a_half_day_is_not_reported():
    """The Friday after Thanksgiving is a real 1pm-close session at ~60% of a
    normal day. Flagging it would train everyone to ignore the check."""
    sizes = {f"d{i}": MEDIAN for i in range(9)}
    sizes["halfday"] = int(MEDIAN * 0.60)
    assert truncated(sizes, set(sizes)) == []


def test_a_zero_byte_file_on_a_REAL_session_is_reported():
    """The blind spot review caught. The first version excluded sub-holiday-size
    files from the *report* as well as from the median — but a holiday is never
    in `expected` (no equity bars), so inside `expected` an 87-byte file is the
    most complete truncation there is, and it was silently passing. Worse, the
    rollup records prints=0 for an empty file, which marks the session ingested,
    so 'not rolled' did not catch it either — all three checks said no gaps."""
    sizes = {f"d{i}": MEDIAN for i in range(9)}
    sizes["killed"] = 87
    assert [d for d, _ in truncated(sizes, set(sizes))] == ["killed"]


def test_holiday_markers_outside_expected_are_ignored():
    """Holidays are excluded by NOT being expected sessions — which is the only
    correct mechanism, since size alone cannot tell a holiday from a truncation.
    The previous version of this test passed `expected` containing the holiday,
    a state the calendar logic makes impossible, and so locked in the bug."""
    sizes = {f"d{i}": MEDIAN for i in range(9)}
    sizes["holiday"] = 87
    expected = {k for k in sizes if k != "holiday"}
    assert truncated(sizes, expected) == []


def test_holidays_cannot_drag_the_median_down():
    """If zero-byte markers counted toward the median, a directory that is half
    holidays would have a near-zero reference and every real truncation would
    read as normal. The median is taken over non-holiday files only. Holidays are
    passed outside `expected`, which is where the real calendar puts them."""
    sizes = {f"h{i}": 0 for i in range(9)}
    sizes.update({f"d{i}": MEDIAN for i in range(9)})
    sizes["short"] = 10_000_000
    expected = {k for k in sizes if not k.startswith("h")}
    assert [d for d, _ in truncated(sizes, expected)] == ["short"]


def test_files_outside_the_expected_window_are_ignored():
    """Scoping matters: a stray file from another range must not be measured
    against, or reported as, a gap in this one."""
    sizes = {f"d{i}": MEDIAN for i in range(9)}
    sizes["stray"] = 1_000
    expected = {k for k in sizes if k != "stray"}
    assert truncated(sizes, expected) == []


def test_too_few_files_reports_nothing_rather_than_guessing():
    """With three files a 'median' is noise, and a confident wrong answer here
    would be worse than silence — the caller still sees the missing-file lists."""
    assert truncated({"a": MEDIAN, "b": 1_000}, {"a", "b"}) == []
