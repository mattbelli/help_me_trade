"""Tests for research.fundamentals — network-free."""

from __future__ import annotations

from pathlib import Path

import pytest

from db.base import connect
from research.fundamentals import (
    _base_ticker,
    _ensure_schema,
    _parse_shares,
    _parse_submissions,
    market_cap,
    sic_to_sector,
)


def test_sic_to_sector_buckets() -> None:
    assert sic_to_sector(100) == "Agriculture"
    assert sic_to_sector(1311) == "Mining"
    assert sic_to_sector(3571) == "Manufacturing"
    assert sic_to_sector(4911) == "Utilities/Transport"
    assert sic_to_sector(5110) == "Wholesale"
    assert sic_to_sector(5311) == "Retail"
    assert sic_to_sector(6021) == "Finance"
    assert sic_to_sector(7372) == "Services"
    assert sic_to_sector(9999) == "Other"
    assert sic_to_sector(None) == "Unknown"


def test_base_ticker_strips_delisted_suffix() -> None:
    assert _base_ticker("XLNX-202202") == "XLNX"
    assert _base_ticker("aapl") == "AAPL"
    assert _base_ticker("BRK-B") == "BRK-B"


def test_parse_submissions() -> None:
    sic, desc = _parse_submissions(
        {"sic": "7372", "sicDescription": "Prepackaged Software"}
    )
    assert sic == 7372
    assert desc == "Prepackaged Software"

    sic_none, desc_none = _parse_submissions({})
    assert sic_none is None
    assert desc_none is None


def test_parse_shares() -> None:
    payload = {
        "units": {
            "shares": [
                {"end": "2023-12-31", "val": 1_000_000},
                {"end": "2024-03-31", "val": 1_050_000},
                {"end": "2024-06-30", "val": None},
            ]
        }
    }
    assert _parse_shares(payload) == [
        ("2023-12-31", 1_000_000.0),
        ("2024-03-31", 1_050_000.0),
    ]


def _seed_fundamentals(
    cache_db: Path,
    *,
    symbol: str,
    cik: str,
    shares: list[tuple[str, float]],
) -> None:
    conn = connect(cache_db)
    try:
        _ensure_schema(conn)
        conn.execute(
            """
            INSERT OR REPLACE INTO companies (symbol, cik, sic, sic_desc, sector)
            VALUES (?, ?, ?, ?, ?)
            """,
            (symbol, cik, 7372, "Prepackaged Software", "Services"),
        )
        conn.executemany(
            "INSERT OR REPLACE INTO shares (cik, date, shares) VALUES (?, ?, ?)",
            [(cik, d, val) for d, val in shares],
        )
        conn.commit()
    finally:
        conn.close()


def test_market_cap_point_in_time(tmp_path: Path) -> None:
    cache_db = tmp_path / "fundamentals.db"
    _seed_fundamentals(
        cache_db,
        symbol="TEST",
        cik="0000123456",
        shares=[
            ("2024-01-31", 100.0),
            ("2024-06-30", 120.0),
        ],
    )

    cap_mid = market_cap("TEST", "2024-03-15", 10.0, cache_db=cache_db)
    assert cap_mid == pytest.approx(1000.0)

    cap_on_boundary = market_cap("TEST", "2024-06-30", 10.0, cache_db=cache_db)
    assert cap_on_boundary == pytest.approx(1200.0)

    cap_before = market_cap("TEST", "2023-12-31", 10.0, cache_db=cache_db)
    assert cap_before is None

    cap_unresolved = market_cap("MISSING", "2024-03-15", 10.0, cache_db=cache_db)
    assert cap_unresolved is None
