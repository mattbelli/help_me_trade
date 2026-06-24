"""Tests for research.earnings — network-free."""

from __future__ import annotations

from pathlib import Path
from dataclasses import dataclass

from db.base import connect
from research.earnings import (
    _ensure_schema,
    _parse_earnings_from_submissions,
    _resolve_cik,
    compute_reaction_pct,
    earnings_dates,
    earnings_history_reactions,
)


@dataclass
class _Bar:
    time: str
    open: float
    high: float
    low: float
    close: float
    volume: float


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


# ---------------------------------------------------------------------------
# compute_reaction_pct
# ---------------------------------------------------------------------------


def _make_bars(*date_close_pairs: tuple[str, float]) -> list[_Bar]:
    return [
        _Bar(time=d, open=c, high=c, low=c, close=c, volume=1_000_000)
        for d, c in date_close_pairs
    ]


def test_compute_reaction_pct_basic() -> None:
    """Earnings on 2024-01-25; reaction = bar on 2024-01-25 vs 2024-01-24."""
    bars = _make_bars(
        ("2024-01-23", 100.0),
        ("2024-01-24", 102.0),  # bar before earnings date
        ("2024-01-25", 108.0),  # first bar on/after earnings date
        ("2024-01-26", 107.0),
    )
    result = compute_reaction_pct("2024-01-25", bars)
    # (108 - 102) / 102 * 100 = 5.882...  → rounded to 5.88
    assert result == round((108.0 - 102.0) / 102.0 * 100, 2)


def test_compute_reaction_pct_negative() -> None:
    bars = _make_bars(
        ("2024-04-29", 200.0),
        ("2024-04-30", 180.0),  # first bar on/after earnings date
    )
    result = compute_reaction_pct("2024-04-30", bars)
    assert result == round((180.0 - 200.0) / 200.0 * 100, 2)  # -10.0


def test_compute_reaction_pct_no_prior_bar() -> None:
    """Earnings date matches the very first bar — no prior bar available."""
    bars = _make_bars(("2024-01-25", 100.0), ("2024-01-26", 105.0))
    assert compute_reaction_pct("2024-01-25", bars) is None


def test_compute_reaction_pct_no_bar_on_or_after() -> None:
    """Earnings date is after all available bars."""
    bars = _make_bars(("2024-01-23", 100.0), ("2024-01-24", 102.0))
    assert compute_reaction_pct("2025-01-25", bars) is None


def test_compute_reaction_pct_uses_first_bar_on_or_after() -> None:
    """Earnings on a weekend — next trading day is Monday 2024-01-29."""
    bars = _make_bars(
        ("2024-01-25", 100.0),  # Friday before earnings
        ("2024-01-29", 110.0),  # Monday — first trading day on/after 2024-01-27
        ("2024-01-30", 109.0),
    )
    result = compute_reaction_pct("2024-01-27", bars)  # Saturday earnings date
    assert result == round((110.0 - 100.0) / 100.0 * 100, 2)  # 10.0


def test_earnings_history_reactions_order_and_limit(tmp_path: Path) -> None:
    """earnings_history_reactions returns most-recent-first, up to n."""
    cache_db = tmp_path / "earnings.db"
    conn = connect(cache_db)
    try:
        _ensure_schema(conn)
        conn.executemany(
            "INSERT INTO earnings (symbol, cik, date) VALUES (?, ?, ?)",
            [
                ("TSLA", "0001318605", "2024-01-24"),
                ("TSLA", "0001318605", "2024-04-23"),
                ("TSLA", "0001318605", "2024-07-23"),
                ("TSLA", "0001318605", "2024-10-23"),
            ],
        )
        conn.commit()
    finally:
        conn.close()

    bars = _make_bars(
        ("2024-01-23", 200.0),
        ("2024-01-24", 210.0),
        ("2024-04-22", 210.0),
        ("2024-04-23", 189.0),
        ("2024-07-22", 189.0),
        ("2024-07-23", 200.0),
        ("2024-10-22", 200.0),
        ("2024-10-23", 220.0),
    )
    results = earnings_history_reactions("TSLA", bars, n=3, cache_db=cache_db)
    # n=3 → last 3 dates: 2024-04-23, 2024-07-23, 2024-10-23; most-recent first
    assert len(results) == 3
    assert results[0]["date"] == "2024-10-23"
    assert results[1]["date"] == "2024-07-23"
    assert results[2]["date"] == "2024-04-23"
    assert results[0]["reaction_pct"] == round((220.0 - 200.0) / 200.0 * 100, 2)  # 10.0
    assert results[2]["reaction_pct"] == round((189.0 - 210.0) / 210.0 * 100, 2)  # -10.0
