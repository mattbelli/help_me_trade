"""Tests for research.regime — network-free."""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pytest

from db.base import connect
from research.bars import cache_bars
from research.regime import (
    _ensure_schema,
    _parse_fred_csv,
    compute_breadth,
    market_regime,
)


def _seed_macro(cache_db: Path, rows: list[tuple[str, str, float]]) -> None:
    conn = connect(cache_db)
    try:
        _ensure_schema(conn)
        conn.executemany(
            "INSERT OR REPLACE INTO macro (series, date, value) VALUES (?, ?, ?)",
            rows,
        )
        conn.commit()
    finally:
        conn.close()


def _dated_bars(symbol: str, closes: list[float], start: date) -> list[dict]:
    bars: list[dict] = []
    d = start
    for i, c in enumerate(closes):
        prev = closes[i - 1] if i > 0 else c
        bars.append(
            {
                "d": d.isoformat(),
                "o": prev,
                "h": c + 1.0,
                "l": c - 1.0,
                "c": c,
                "v": 1000.0,
            }
        )
        d += timedelta(days=1)
    return bars


def test_parse_fred_csv_skips_missing_values() -> None:
    csv_text = """observation_date,VIXCLS
2024-01-02,15.5
2024-01-03,.
2024-01-04,16.0
"""
    assert _parse_fred_csv(csv_text, "VIXCLS") == [
        ("2024-01-02", 15.5),
        ("2024-01-04", 16.0),
    ]


def test_compute_breadth_point_in_time(tmp_path: Path) -> None:
    bars_db = tmp_path / "bars.db"
    cache_db = tmp_path / "regime.db"
    start = date(2020, 1, 1)
    target = (start + timedelta(days=209)).isoformat()

    flat = [100.0] * 209
    cache_bars("AAA", _dated_bars("AAA", flat + [110.0], start), source="test", db_path=bars_db)
    cache_bars("BBB", _dated_bars("BBB", flat + [110.0], start), source="test", db_path=bars_db)
    cache_bars("CCC", _dated_bars("CCC", flat + [90.0], start), source="test", db_path=bars_db)

    n_dates = compute_breadth(source="test", bars_db=bars_db, cache_db=cache_db)
    assert n_dates == 210

    conn = connect(cache_db)
    try:
        row = conn.execute(
            "SELECT pct_above_200, pct_above_50, adv_dec FROM breadth WHERE date = ?",
            (target,),
        ).fetchone()
    finally:
        conn.close()

    assert row is not None
    assert row["pct_above_200"] == pytest.approx(2 / 3)
    assert row["pct_above_50"] == pytest.approx(2 / 3)
    assert row["adv_dec"] == pytest.approx(1 / 3)


def test_market_regime_forward_fills_macro(tmp_path: Path) -> None:
    cache_db = tmp_path / "regime.db"
    _seed_macro(
        cache_db,
        [
            ("vix", "2024-01-01", 20.0),
            ("vix", "2024-01-05", 25.0),
            ("y10", "2024-01-01", 4.0),
        ],
    )

    snap = market_regime("2024-01-03", cache_db=cache_db)
    assert snap is not None
    assert snap["vix"] == pytest.approx(20.0)
    assert snap["y10"] == pytest.approx(4.0)
    assert snap["y2"] is None
    assert snap["term_spread"] is None
    assert snap["hy_spread"] is None
    assert snap["breadth_above_200"] is None

    before = market_regime("2023-12-31", cache_db=cache_db)
    assert before is not None
    assert before["vix"] is None
    assert before["y10"] is None


def test_vix_percentile_1y(tmp_path: Path) -> None:
    cache_db = tmp_path / "regime.db"
    rows: list[tuple[str, str, float]] = []
    start = date(2023, 1, 1)
    for i in range(252):
        rows.append(("vix", (start + timedelta(days=i)).isoformat(), float(i + 1)))
    _seed_macro(cache_db, rows)

    query_date = (start + timedelta(days=251)).isoformat()
    snap = market_regime(query_date, cache_db=cache_db)
    assert snap is not None
    assert snap["vix"] == pytest.approx(252.0)
    assert snap["vix_pctile_1y"] == pytest.approx(100.0)

    mid_date = (start + timedelta(days=125)).isoformat()
    mid_snap = market_regime(mid_date, cache_db=cache_db)
    assert mid_snap is not None
    assert mid_snap["vix"] == pytest.approx(126.0)
    expected_pctile = float(np.sum(np.arange(1, 127) <= 126) / 126 * 100.0)
    assert mid_snap["vix_pctile_1y"] == pytest.approx(expected_pctile)
