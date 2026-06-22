"""Tests for research.earnings — network-free."""

from __future__ import annotations

from pathlib import Path

from db.base import connect
from research.earnings import (
    _ensure_schema,
    _parse_earnings_from_submissions,
    _resolve_cik,
    earnings_dates,
)


def test_parse_earnings_from_submissions_filters_8k_item_202() -> None:
    recent = {
        "form": ["8-K", "10-Q", "8-K", "8-K", "8-K"],
        "filingDate": [
            "2024-01-25",
            "2024-02-01",
            "2024-04-30",
            "2024-07-31",
            "2024-10-30",
        ],
        "items": ["2.02", "2.02", "5.02", "2.02,9.01", "2.02"],
    }
    assert _parse_earnings_from_submissions(recent) == [
        "2024-01-25",
        "2024-07-31",
        "2024-10-30",
    ]


def test_parse_earnings_excludes_non_8k_and_missing_item_202() -> None:
    recent = {
        "form": ["10-Q", "8-K"],
        "filingDate": ["2024-02-01", "2024-03-15"],
        "items": ["2.02", "5.02"],
    }
    assert _parse_earnings_from_submissions(recent) == []


def test_parse_earnings_handles_empty_and_missing_items() -> None:
    assert _parse_earnings_from_submissions({}) == []
    assert _parse_earnings_from_submissions(
        {"form": ["8-K"], "filingDate": ["2024-01-01"]}
    ) == []
    assert _parse_earnings_from_submissions(
        {"form": ["8-K"], "filingDate": ["2024-01-01"], "items": [None]}
    ) == []


def test_resolve_cik_strips_delisted_suffix() -> None:
    ticker_cik = {"FOO": "1234567"}
    assert _resolve_cik("FOO-202110", ticker_cik) == "1234567"
    assert _resolve_cik("FOO", ticker_cik) == "1234567"
    assert _resolve_cik("MISSING-202110", ticker_cik) is None


def test_earnings_dates_from_cache(tmp_path: Path) -> None:
    cache_db = tmp_path / "earnings.db"
    conn = connect(cache_db)
    try:
        _ensure_schema(conn)
        conn.executemany(
            "INSERT INTO earnings (symbol, cik, date) VALUES (?, ?, ?)",
            [
                ("AAPL", "0000320193", "2024-01-25"),
                ("AAPL", "0000320193", "2024-04-30"),
                ("AAPL", "0000320193", "2024-07-31"),
            ],
        )
        conn.commit()
    finally:
        conn.close()

    assert earnings_dates("AAPL", cache_db=cache_db) == [
        "2024-01-25",
        "2024-04-30",
        "2024-07-31",
    ]
    assert earnings_dates("MISSING", cache_db=cache_db) == []
