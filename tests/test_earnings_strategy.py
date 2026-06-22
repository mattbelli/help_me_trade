"""Tests for research.earnings_strategy — synthetic, no network."""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pytest

from db.base import connect
from research.bars import cache_bars
from research.earnings_strategy import (
    _blowout_reaction,
    _idx_of,
    _symbol_setups,
    build_earnings_dataset,
)
from signals.trade_context import DatedBar


def _flat_bars(
    *,
    count: int,
    start: date,
    close: float = 100.0,
) -> list[DatedBar]:
    bars: list[DatedBar] = []
    d = start
    for _ in range(count):
        bars.append(
            {
                "d": d.isoformat(),
                "o": close,
                "h": close + 1.0,
                "l": close - 1.0,
                "c": close,
                "v": 1000.0,
            }
        )
        d += timedelta(days=1)
    return bars


def _set_close(bars: list[DatedBar], idx: int, close: float) -> None:
    bars[idx]["c"] = close
    bars[idx]["o"] = close
    bars[idx]["h"] = close + 1.0
    bars[idx]["l"] = close - 1.0


def _seed_bars_db(tmp_path: Path, symbol: str, bars: list[DatedBar]) -> Path:
    bars_db = tmp_path / "bars.db"
    cache_bars(symbol, bars, source="test", db_path=bars_db)
    return bars_db


def _load_setups(db_path: Path) -> list[dict]:
    conn = connect(db_path)
    try:
        rows = conn.execute(
            "SELECT * FROM strategy_setups ORDER BY entry_date ASC"
        ).fetchall()
    finally:
        conn.close()
    return [dict(row) for row in rows]


def test_idx_of_and_blowout_reaction_helpers() -> None:
    dates = ["2020-01-01", "2020-01-02", "2020-01-03", "2020-01-04"]
    assert _idx_of(dates, "2020-01-02") == 1
    assert _idx_of(dates, "2020-01-05") is None

    closes = [100.0, 100.0, 106.0, 100.0]
    assert _blowout_reaction(closes, 1) == pytest.approx(0.06)
    assert _blowout_reaction(closes, 0) is None
    assert _blowout_reaction(closes, len(closes) - 1) is None


def test_one_blowout_trade_entry_exit_and_return(tmp_path: Path) -> None:
    """Blowout quarter + next earnings 63 td later -> exactly one trade."""
    start = date(2020, 1, 1)
    bar_count = 120
    bars = _flat_bars(count=bar_count, start=start, close=100.0)

    j = 20
    jn = j + 63
    assert jn < bar_count

    _set_close(bars, j - 1, 100.0)
    _set_close(bars, j + 1, 106.0)  # +6% blowout straddle

    entry_idx = jn - 10
    exit_idx = jn - 1
    _set_close(bars, entry_idx, 100.0)
    _set_close(bars, exit_idx, 110.0)

    e1 = bars[j]["d"]
    e2 = bars[jn]["d"]
    cost_pct = 0.20
    expected_return = (110.0 / 100.0 - 1.0) * 100.0 - cost_pct

    bars_db = _seed_bars_db(tmp_path, "TEST", bars)
    out_db = tmp_path / "earnings_labeled.db"

    summary = build_earnings_dataset(
        ["TEST"],
        blowout_pct=5.0,
        entry_lead_td=10,
        exit_lead_td=1,
        require_blowout=True,
        cost_pct=cost_pct,
        source="test",
        bars_db_path=bars_db,
        out_db_path=out_db,
        earnings_dates_fn=lambda _sym: [e1, e2],
    )

    assert summary["n_setups"] == 1
    setups = _load_setups(out_db)
    assert len(setups) == 1
    row = setups[0]
    assert row["symbol"] == "TEST"
    assert row["entry_date"] == bars[entry_idx]["d"]
    assert row["holding_days"] == exit_idx - entry_idx
    assert row["blended_return_pct"] == pytest.approx(expected_return)
    assert row["blowout_reaction_pct"] == pytest.approx(6.0)
    assert row["next_earnings_date"] == e2
    assert row["source"] == "test"


def _earnings_fn(e1: str, e2: str):
    def _fn(_sym: str) -> list[str]:
        return [e1, e2]

    return _fn


def test_non_blowout_skipped_with_filter_control_runs(tmp_path: Path) -> None:
    start = date(2020, 1, 1)
    bars = _flat_bars(count=120, start=start, close=100.0)

    j = 20
    jn = j + 63
    _set_close(bars, j - 1, 100.0)
    _set_close(bars, j + 1, 102.0)  # +2% — below 5% threshold

    entry_idx = jn - 10
    exit_idx = jn - 1
    _set_close(bars, entry_idx, 100.0)
    _set_close(bars, exit_idx, 105.0)

    e1 = bars[j]["d"]
    e2 = bars[jn]["d"]
    cost_pct = 0.20
    expected_return = (105.0 / 100.0 - 1.0) * 100.0 - cost_pct

    bars_db = _seed_bars_db(tmp_path, "TEST", bars)
    out_db = tmp_path / "earnings_labeled.db"
    dates_fn = _earnings_fn(e1, e2)

    filtered = build_earnings_dataset(
        ["TEST"],
        require_blowout=True,
        blowout_pct=5.0,
        cost_pct=cost_pct,
        source="test",
        bars_db_path=bars_db,
        out_db_path=out_db,
        earnings_dates_fn=dates_fn,
    )
    assert filtered["n_setups"] == 0

    control_db = tmp_path / "control.db"
    control = build_earnings_dataset(
        ["TEST"],
        require_blowout=False,
        blowout_pct=5.0,
        cost_pct=cost_pct,
        source="test",
        bars_db_path=bars_db,
        out_db_path=control_db,
        earnings_dates_fn=dates_fn,
    )
    assert control["n_setups"] == 1
    row = _load_setups(control_db)[0]
    assert row["blended_return_pct"] == pytest.approx(expected_return)


def test_gap_guard_skips_wide_spacing(tmp_path: Path) -> None:
    start = date(2020, 1, 1)
    bars = _flat_bars(count=250, start=start, close=100.0)

    j = 20
    jn = j + 200  # outside 40-90 td window
    _set_close(bars, j - 1, 100.0)
    _set_close(bars, j + 1, 106.0)

    e1 = bars[j]["d"]
    e2 = bars[jn]["d"]

    bars_db = _seed_bars_db(tmp_path, "TEST", bars)
    out_db = tmp_path / "earnings_labeled.db"

    summary = build_earnings_dataset(
        ["TEST"],
        require_blowout=True,
        source="test",
        bars_db_path=bars_db,
        out_db_path=out_db,
        earnings_dates_fn=lambda _sym: [e1, e2],
    )
    assert summary["n_setups"] == 0


def test_bounds_guards_no_crash_at_series_edges() -> None:
    bars = _flat_bars(count=5, start=date(2020, 1, 1))
    # Earnings at first/last bars — insufficient room for straddle or entry/exit
    earnings = [bars[0]["d"], bars[-1]["d"]]
    assert (
        _symbol_setups(
            "TEST",
            bars,
            earnings,
            blowout_pct=5.0,
            entry_lead_td=10,
            exit_lead_td=1,
            require_blowout=False,
            cost_pct=0.0,
            min_gap_td=1,
            max_gap_td=90,
            source="test",
            created_at="2020-01-01T00:00:00Z",
        )
        == []
    )

    # Blowout straddle needs j-1 and j+1 in range
    long_bars = _flat_bars(count=30, start=date(2020, 1, 1))
    assert _blowout_reaction([b["c"] for b in long_bars], 0) is None
    assert _blowout_reaction([b["c"] for b in long_bars], len(long_bars) - 1) is None

    dates = [b["d"] for b in long_bars]
    assert _idx_of(dates, "2099-01-01") is None


def test_replace_by_source(tmp_path: Path) -> None:
    start = date(2020, 1, 1)
    bars = _flat_bars(count=120, start=start, close=100.0)
    j, jn = 20, 83
    _set_close(bars, j - 1, 100.0)
    _set_close(bars, j + 1, 106.0)
    _set_close(bars, jn - 10, 100.0)
    _set_close(bars, jn - 1, 110.0)
    e1, e2 = bars[j]["d"], bars[jn]["d"]

    bars_db = tmp_path / "bars.db"
    cache_bars("TEST", bars, source="src_a", db_path=bars_db)
    cache_bars("TEST", bars, source="src_b", db_path=bars_db)
    out_db = tmp_path / "earnings_labeled.db"
    dates_fn = _earnings_fn(e1, e2)

    build_earnings_dataset(
        ["TEST"],
        source="src_a",
        bars_db_path=bars_db,
        out_db_path=out_db,
        earnings_dates_fn=dates_fn,
    )
    build_earnings_dataset(
        ["TEST"],
        source="src_b",
        bars_db_path=bars_db,
        out_db_path=out_db,
        earnings_dates_fn=dates_fn,
    )

    conn = connect(out_db)
    try:
        counts = conn.execute(
            "SELECT source, COUNT(*) AS cnt FROM strategy_setups GROUP BY source ORDER BY source"
        ).fetchall()
    finally:
        conn.close()

    assert [(row["source"], row["cnt"]) for row in counts] == [("src_a", 1), ("src_b", 1)]

    build_earnings_dataset(
        ["TEST"],
        source="src_a",
        bars_db_path=bars_db,
        out_db_path=out_db,
        earnings_dates_fn=dates_fn,
        replace=True,
    )
    conn = connect(out_db)
    try:
        counts = conn.execute(
            "SELECT source, COUNT(*) AS cnt FROM strategy_setups GROUP BY source ORDER BY source"
        ).fetchall()
    finally:
        conn.close()
    assert [(row["source"], row["cnt"]) for row in counts] == [("src_a", 1), ("src_b", 1)]
