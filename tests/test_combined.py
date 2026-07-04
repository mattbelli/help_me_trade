"""Tests for research.combined — synthetic, no network."""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pytest

from db.base import connect, now_iso
from research.bars import cache_bars
from research.combined import combine_and_report, load_strategy_trades
from research.portfolio import backtest_portfolio
from research.strategy_dataset import _CREATE_STRATEGY_SETUPS
from signals.trade_context import DatedBar


def _seed_strategy_db(
    db_path: Path,
    rows: list[tuple[str, int, float]],
    *,
    source: str = "norgate",
) -> None:
    conn = connect(db_path)
    try:
        conn.execute(_CREATE_STRATEGY_SETUPS)
        for entry_date, holding_days, blended_return_pct in rows:
            conn.execute(
                """
                INSERT INTO strategy_setups (
                    symbol, entry_date, features_json, label, blended_return_pct, outcome,
                    holding_days, same_day_ambiguous, max_holding, stop_pct, first_target_pct,
                    runner_target_pct, source, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "AAPL",
                    entry_date,
                    "{}",
                    1,
                    blended_return_pct,
                    "first_then_runner",
                    holding_days,
                    0,
                    20,
                    3.0,
                    3.0,
                    10.0,
                    source,
                    now_iso(),
                ),
            )
        conn.commit()
    finally:
        conn.close()


def _seed_spy_bars(
    db_path: Path,
    *,
    start: date,
    count: int,
    close: float = 100.0,
) -> list[DatedBar]:
    bars: list[DatedBar] = []
    for i in range(count):
        d = (start + timedelta(days=i)).isoformat()
        bars.append({"d": d, "o": close, "h": close, "l": close, "c": close, "v": 1_000.0})
    cache_bars("SPY", bars, source="norgate", db_path=db_path)
    return bars


def test_load_strategy_trades_applies_cost_and_filters(tmp_path: Path) -> None:
    db = tmp_path / "strategy.db"
    _seed_strategy_db(
        db,
        [
            ("2019-06-01", 5, 10.0),
            ("2020-01-01", 5, 8.0),
            ("2021-03-01", 5, 6.0),
        ],
    )

    all_trades = load_strategy_trades(db, cost_pct=0.2)
    assert len(all_trades) == 3
    assert all_trades[0][2] == pytest.approx(9.8)

    filtered = load_strategy_trades(db, cost_pct=0.0, start="2020-01-01", end="2020-12-31")
    assert len(filtered) == 1
    assert filtered[0][0] == "2020-01-01"


def test_load_strategy_trades_requires_explicit_cost(tmp_path: Path) -> None:
    """Gross-or-net must be stated — no silent cost default."""
    db = tmp_path / "strategy.db"
    _seed_strategy_db(db, [("2020-01-01", 5, 10.0)])
    with pytest.raises(TypeError):
        load_strategy_trades(db)


def test_window_excludes_trades_exiting_past_end(tmp_path: Path) -> None:
    """TRAIN/TEST hygiene: a late-TRAIN entry whose exit lands in TEST is dropped."""
    db = tmp_path / "strategy.db"
    _seed_strategy_db(
        db,
        [
            ("2020-12-01", 10, 5.0),  # exits 2020-12-11 — contained
            ("2020-12-28", 10, 5.0),  # exits 2021-01-07 — leaks past the boundary
        ],
    )

    trades = load_strategy_trades(db, cost_pct=0.0, end="2020-12-31")
    assert [t[0] for t in trades] == ["2020-12-01"]

    # Exit exactly ON the boundary is still contained.
    boundary = load_strategy_trades(db, cost_pct=0.0, end="2021-01-07")
    assert len(boundary) == 2


def test_combine_windows_drop_boundary_leaking_trades(tmp_path: Path) -> None:
    db = tmp_path / "strategy.db"
    bars_db = tmp_path / "bars.db"
    _seed_strategy_db(
        db,
        [
            ("2020-06-01", 5, 5.0),
            ("2020-12-28", 10, 5.0),  # exits 2021-01-07
        ],
    )
    _seed_spy_bars(bars_db, start=date(2020, 6, 1), count=250)

    report = combine_and_report(
        [("dip", db, 0.0)],
        windows={"TRAIN": ("2020-01-01", "2020-12-31"), "full": (None, None)},
        bars_db_path=bars_db,
    )

    assert report["TRAIN"]["dip"]["n_taken"] == 1
    assert report["full"]["dip"]["n_taken"] == 2


def test_combine_merged_trade_count_and_cost(tmp_path: Path) -> None:
    dip_db = tmp_path / "dip.db"
    earn_db = tmp_path / "earn.db"
    bars_db = tmp_path / "bars.db"

    dip_rows = [
        ("2020-01-01", 5, 10.0),
        ("2020-02-01", 5, 5.0),
    ]
    earn_rows = [
        ("2020-03-01", 5, 8.0),
    ]
    _seed_strategy_db(dip_db, dip_rows)
    _seed_strategy_db(earn_db, earn_rows)
    _seed_spy_bars(bars_db, start=date(2020, 1, 1), count=120)

    dip_loaded = load_strategy_trades(dip_db, cost_pct=0.2)
    earn_loaded = load_strategy_trades(earn_db, cost_pct=0.0)
    assert len(dip_loaded) == 2
    assert dip_loaded[0][2] == pytest.approx(9.8)
    assert len(earn_loaded) == 1
    assert earn_loaded[0][2] == pytest.approx(8.0)

    report = combine_and_report(
        [("dip", dip_db, 0.2), ("earn", earn_db, 0.0)],
        max_concurrent=10,
        per_trade_fraction=1.0,
        windows={"full": (None, None)},
        bars_db_path=bars_db,
    )

    dip_bt = backtest_portfolio(dip_loaded, max_concurrent=10, per_trade_fraction=1.0, cost_pct=0.0)
    earn_bt = backtest_portfolio(earn_loaded, max_concurrent=10, per_trade_fraction=1.0, cost_pct=0.0)
    merged_bt = backtest_portfolio(
        dip_loaded + earn_loaded,
        max_concurrent=10,
        per_trade_fraction=1.0,
        cost_pct=0.0,
    )

    window = report["full"]
    assert window["dip"]["n_taken"] == dip_bt["n_taken"]
    assert window["earn"]["n_taken"] == earn_bt["n_taken"]
    assert window["COMBINED"]["n_taken"] == merged_bt["n_taken"]
    assert window["COMBINED"]["n_taken"] == dip_bt["n_taken"] + earn_bt["n_taken"]
    assert "benchmark" in window
    assert window["benchmark"]["symbol"] == "SPY"


def test_single_source_matches_combined(tmp_path: Path) -> None:
    db = tmp_path / "solo.db"
    bars_db = tmp_path / "bars.db"
    _seed_strategy_db(db, [("2020-01-01", 5, 10.0), ("2020-04-01", 5, 4.0)])
    _seed_spy_bars(bars_db, start=date(2020, 1, 1), count=150)

    report = combine_and_report(
        [("solo", db, 0.0)],
        max_concurrent=5,
        per_trade_fraction=0.5,
        windows={"full": (None, None)},
        bars_db_path=bars_db,
    )

    solo = report["full"]["solo"]
    combined = report["full"]["COMBINED"]
    for key in ("final_equity", "n_taken", "cagr", "sharpe"):
        assert combined[key] == pytest.approx(solo[key])


def test_empty_source_does_not_crash(tmp_path: Path) -> None:
    empty_db = tmp_path / "empty.db"
    bars_db = tmp_path / "bars.db"
    conn = connect(empty_db)
    try:
        conn.execute(_CREATE_STRATEGY_SETUPS)
        conn.commit()
    finally:
        conn.close()
    _seed_spy_bars(bars_db, start=date(2020, 1, 1), count=30)

    report = combine_and_report(
        [("empty", empty_db, 0.0)],
        windows={"full": (None, None)},
        bars_db_path=bars_db,
    )

    window = report["full"]
    assert window["empty"]["n_taken"] == 0
    assert window["COMBINED"]["n_taken"] == 0
    assert window["empty"]["final_equity"] == pytest.approx(100_000.0)


def test_report_dict_shape(tmp_path: Path) -> None:
    dip_db = tmp_path / "dip.db"
    bars_db = tmp_path / "bars.db"
    _seed_strategy_db(dip_db, [("2020-01-01", 5, 5.0)])
    _seed_spy_bars(bars_db, start=date(2020, 1, 1), count=30)

    report = combine_and_report(
        [("dip", dip_db, 0.0)],
        windows={"full": (None, None), "TRAIN": ("2011-01-01", "2020-12-31")},
        bars_db_path=bars_db,
    )

    assert set(report.keys()) == {"full", "TRAIN"}
    for window_name, window in report.items():
        assert set(window.keys()) == {"dip", "COMBINED", "benchmark"}
        for label in ("dip", "COMBINED"):
            metrics = window[label]
            assert "final_equity" in metrics
            assert "cagr" in metrics
            assert "max_drawdown" in metrics
            assert "sharpe" in metrics
            assert "n_taken" in metrics
            assert "avg_pct_invested" in metrics
        bench = window["benchmark"]
        assert "cagr" in bench
        assert "final_equity" in bench
