"""Tests for strategy dataset builder — entry filter + exit sim + features."""

from __future__ import annotations

import inspect
import json
from datetime import date, timedelta
from pathlib import Path

import pytest

from db.base import connect
from research.bars import cache_bars
from research.strategy_dataset import DEFAULT_COST_PCT, build_strategy_dataset
from research.strategy_sim import simulate_exit
from signals.trade_context import MIN_BARS_FOR_CONTEXT, DatedBar


def _uptrend_closes(n: int, *, start: float = 100.0, step: float = 0.35) -> list[float]:
    return [start + i * step for i in range(n)]


def _apply_three_day_dip(closes: list[float], idx: int, drop_pct: float) -> None:
    """In-place 3-bar decline ending at idx with total drop_pct from idx-3."""
    peak = closes[idx - 3]
    factor = 1.0 + drop_pct / 100.0
    closes[idx - 2] = peak
    closes[idx - 1] = peak * (factor ** (1 / 3))
    closes[idx] = peak * factor


def _apply_three_day_rally(closes: list[float], idx: int, rally_pct: float) -> None:
    """In-place 3-bar rally ending at idx with total rally_pct from idx-3."""
    base = closes[idx - 3]
    factor = 1.0 + rally_pct / 100.0
    closes[idx - 2] = base
    closes[idx - 1] = base * (factor ** (1 / 3))
    closes[idx] = base * factor


def _short_runner_forward_overrides(
    entry_close: float,
    entry_idx: int,
    count: int,
) -> tuple[list[float | None], list[float | None]]:
    """High/low overrides: day+1 hits -3% target, day+2 hits -10% runner."""
    highs: list[float | None] = [None] * count
    lows: list[float | None] = [None] * count
    highs[entry_idx + 1] = entry_close * 1.01
    lows[entry_idx + 1] = entry_close * 0.97
    highs[entry_idx + 2] = entry_close * 0.96
    lows[entry_idx + 2] = entry_close * 0.90
    return highs, lows


def _symbol_bars_with_single_short_entry(
    *,
    bar_count: int,
    entry_idx: int,
    entry_date: date,
    stock_rally_pct: float = 6.0,
) -> list[DatedBar]:
    start_date = entry_date - timedelta(days=entry_idx)
    closes = [220.0 - i * 0.45 for i in range(bar_count)]
    _apply_three_day_rally(closes, entry_idx, stock_rally_pct)
    entry_close = closes[entry_idx]
    highs_override, lows_override = _short_runner_forward_overrides(
        entry_close, entry_idx, bar_count
    )
    return _dated_bars_from_closes(
        closes,
        start_date,
        highs_override=highs_override,
        lows_override=lows_override,
    )


def _spy_bars_aligned_rally(
    *,
    bar_count: int,
    entry_idx: int,
    entry_date: date,
    spy_rally_pct: float,
) -> list[DatedBar]:
    start_date = entry_date - timedelta(days=entry_idx)
    closes = [450.0] * bar_count
    _apply_three_day_rally(closes, entry_idx, spy_rally_pct)
    return _dated_bars_from_closes(closes, start_date)


def _dated_bars_from_closes(
    close_series: list[float],
    start_date: date,
    *,
    highs_override: list[float] | None = None,
    lows_override: list[float] | None = None,
    volume: float | list[float] = 1000.0,
) -> list[DatedBar]:
    bars: list[DatedBar] = []
    d = start_date
    for i, c in enumerate(close_series):
        prev_c = close_series[i - 1] if i > 0 else c
        h = (
            highs_override[i]
            if highs_override is not None and highs_override[i] is not None
            else c + 1.0
        )
        lo = (
            lows_override[i]
            if lows_override is not None and lows_override[i] is not None
            else c - 1.0
        )
        if isinstance(volume, list):
            v = volume[i]
        else:
            v = volume
        bars.append(
            {
                "d": d.isoformat(),
                "o": prev_c if i > 0 else c,
                "h": h,
                "l": lo,
                "c": c,
                "v": v,
            }
        )
        d += timedelta(days=1)
    return bars


def _runner_forward_overrides(
    entry_close: float,
    entry_idx: int,
    count: int,
) -> tuple[list[float | None], list[float | None]]:
    """High/low overrides: day+1 hits +3% target, day+2 hits +10% runner."""
    highs: list[float | None] = [None] * count
    lows: list[float | None] = [None] * count
    highs[entry_idx + 1] = entry_close * 1.03
    lows[entry_idx + 1] = entry_close * 0.99
    highs[entry_idx + 2] = entry_close * 1.10
    lows[entry_idx + 2] = entry_close * 1.04
    return highs, lows


def _symbol_bars_with_single_entry(
    *,
    bar_count: int,
    entry_idx: int,
    entry_date: date,
    stock_drop_pct: float = -6.0,
    exit_style: str = "runner",
) -> list[DatedBar]:
    start_date = entry_date - timedelta(days=entry_idx)
    closes = _uptrend_closes(bar_count)
    _apply_three_day_dip(closes, entry_idx, stock_drop_pct)

    highs_override: list[float] | None = None
    lows_override: list[float] | None = None
    if exit_style == "runner":
        highs_override, lows_override = _runner_forward_overrides(
            closes[entry_idx], entry_idx, bar_count
        )
    elif exit_style == "breakeven":
        entry_close = closes[entry_idx]
        highs = [None] * bar_count
        lows = [None] * bar_count
        highs[entry_idx + 1] = entry_close * 1.03
        lows[entry_idx + 1] = entry_close * 0.99
        highs[entry_idx + 2] = entry_close * 1.01
        lows[entry_idx + 2] = entry_close * 1.00
        highs_override, lows_override = highs, lows

    return _dated_bars_from_closes(
        closes,
        start_date,
        highs_override=highs_override,
        lows_override=lows_override,
    )


def _spy_bars_aligned_uptrend(
    *,
    bar_count: int,
    entry_idx: int,
    entry_date: date,
) -> list[DatedBar]:
    start_date = entry_date - timedelta(days=entry_idx)
    closes = _uptrend_closes(bar_count, start=400.0, step=0.35)
    return _dated_bars_from_closes(closes, start_date)


def _symbol_bars_with_single_momentum_entry(
    *,
    bar_count: int,
    entry_idx: int,
    entry_date: date,
    mom_21d_pct: float = 8.0,
) -> list[DatedBar]:
    start_date = entry_date - timedelta(days=entry_idx)
    prefix_len = entry_idx + 1
    closes = _uptrend_closes(prefix_len - 22, start=70.0, step=0.12)
    base_21d = closes[-1]
    target = base_21d * (1.0 + mom_21d_pct / 100.0)
    closes.append(base_21d)
    for i in range(1, 22):
        closes.append(base_21d + (target - base_21d) * i / 21)
    while len(closes) < bar_count:
        closes.append(closes[-1])

    entry_close = closes[entry_idx]
    highs_override, lows_override = _runner_forward_overrides(
        entry_close, entry_idx, bar_count
    )
    volumes = [1000.0] * bar_count
    volumes[entry_idx] = 2400.0
    return _dated_bars_from_closes(
        closes,
        start_date,
        highs_override=highs_override,
        lows_override=lows_override,
        volume=volumes,
    )


def _symbol_bars_with_single_rsi2_entry(
    *,
    bar_count: int,
    entry_idx: int,
    entry_date: date,
) -> list[DatedBar]:
    start_date = entry_date - timedelta(days=entry_idx)
    closes = _uptrend_closes(entry_idx)
    peak = closes[-1]
    closes.append(peak * 0.92)
    while len(closes) < bar_count:
        closes.append(closes[-1] * 1.015)

    entry_close = closes[entry_idx]
    highs_override, lows_override = _runner_forward_overrides(
        entry_close, entry_idx, bar_count
    )
    return _dated_bars_from_closes(
        closes,
        start_date,
        highs_override=highs_override,
        lows_override=lows_override,
    )


def _symbol_bars_with_single_consec_down_entry(
    *,
    bar_count: int,
    entry_idx: int,
    entry_date: date,
    down_days: int = 3,
) -> list[DatedBar]:
    start_date = entry_date - timedelta(days=entry_idx)
    prefix_len = entry_idx + 1
    closes = _uptrend_closes(prefix_len - down_days + 1)
    peak = closes[-1]
    for step in range(1, down_days):
        closes.append(peak * (1.0 - 0.02 * step))
    while len(closes) < bar_count:
        closes.append(closes[-1])

    entry_close = closes[entry_idx]
    highs_override, lows_override = _runner_forward_overrides(
        entry_close, entry_idx, bar_count
    )
    return _dated_bars_from_closes(
        closes,
        start_date,
        highs_override=highs_override,
        lows_override=lows_override,
    )


def _build_momentum_params(
    bars_db: Path,
    strategy_db: Path,
    *,
    symbols: list[str],
    max_holding: int = 20,
) -> tuple[dict, date, int, list[DatedBar], dict[str, list[DatedBar]]]:
    entry_date = date(2024, 6, 15)
    start = date(2024, 6, 1)
    end = date(2024, 8, 1)
    entry_idx = MIN_BARS_FOR_CONTEXT + 5
    bar_count = MIN_BARS_FOR_CONTEXT + max_holding + 25

    spy_bars = _spy_bars_aligned_uptrend(
        bar_count=bar_count,
        entry_idx=entry_idx,
        entry_date=entry_date,
    )
    symbol_bars: dict[str, list[DatedBar]] = {}
    for sym in symbols:
        symbol_bars[sym] = _symbol_bars_with_single_momentum_entry(
            bar_count=bar_count,
            entry_idx=entry_idx,
            entry_date=entry_date,
        )

    params = {
        "symbols": symbols,
        "start": start,
        "end": end,
        "benchmark": "SPY",
        "max_holding": max_holding,
        "source": "test",
        "bars_db_path": bars_db,
        "strategy_db_path": strategy_db,
        "strategy_type": "momentum",
    }
    return params, entry_date, entry_idx, spy_bars, symbol_bars


def _build_rsi2_params(
    bars_db: Path,
    strategy_db: Path,
    *,
    symbols: list[str],
    max_holding: int = 20,
) -> tuple[dict, date, int, list[DatedBar], dict[str, list[DatedBar]]]:
    entry_date = date(2024, 6, 15)
    start = date(2024, 6, 1)
    end = date(2024, 8, 1)
    entry_idx = MIN_BARS_FOR_CONTEXT + 5
    bar_count = MIN_BARS_FOR_CONTEXT + max_holding + 25

    spy_bars = _spy_bars_aligned_uptrend(
        bar_count=bar_count,
        entry_idx=entry_idx,
        entry_date=entry_date,
    )
    symbol_bars: dict[str, list[DatedBar]] = {}
    for sym in symbols:
        symbol_bars[sym] = _symbol_bars_with_single_rsi2_entry(
            bar_count=bar_count,
            entry_idx=entry_idx,
            entry_date=entry_date,
        )

    params = {
        "symbols": symbols,
        "start": start,
        "end": end,
        "benchmark": "SPY",
        "max_holding": max_holding,
        "source": "test",
        "bars_db_path": bars_db,
        "strategy_db_path": strategy_db,
        "strategy_type": "rsi2",
    }
    return params, entry_date, entry_idx, spy_bars, symbol_bars


def _build_consec_down_params(
    bars_db: Path,
    strategy_db: Path,
    *,
    symbols: list[str],
    max_holding: int = 20,
) -> tuple[dict, date, int, list[DatedBar], dict[str, list[DatedBar]]]:
    entry_date = date(2024, 6, 15)
    start = date(2024, 6, 1)
    end = date(2024, 8, 1)
    entry_idx = MIN_BARS_FOR_CONTEXT + 5
    bar_count = MIN_BARS_FOR_CONTEXT + max_holding + 25

    spy_bars = _spy_bars_aligned_uptrend(
        bar_count=bar_count,
        entry_idx=entry_idx,
        entry_date=entry_date,
    )
    symbol_bars: dict[str, list[DatedBar]] = {}
    for sym in symbols:
        symbol_bars[sym] = _symbol_bars_with_single_consec_down_entry(
            bar_count=bar_count,
            entry_idx=entry_idx,
            entry_date=entry_date,
        )

    params = {
        "symbols": symbols,
        "start": start,
        "end": end,
        "benchmark": "SPY",
        "max_holding": max_holding,
        "source": "test",
        "bars_db_path": bars_db,
        "strategy_db_path": strategy_db,
        "strategy_type": "consec_down",
    }
    return params, entry_date, entry_idx, spy_bars, symbol_bars


def _spy_bars_aligned(
    *,
    bar_count: int,
    entry_idx: int,
    entry_date: date,
    spy_drop_pct: float,
) -> list[DatedBar]:
    start_date = entry_date - timedelta(days=entry_idx)
    closes = [450.0] * bar_count
    _apply_three_day_dip(closes, entry_idx, spy_drop_pct)
    return _dated_bars_from_closes(closes, start_date)


@pytest.fixture
def strategy_tmp_paths(tmp_path: Path) -> tuple[Path, Path]:
    return tmp_path / "bars.db", tmp_path / "strategy_labeled.db"


def _build_params(
    bars_db: Path,
    strategy_db: Path,
    *,
    symbols: list[str],
    spy_drop_pct: float = -2.0,
    cost_pct: float = 0.0,
    max_holding: int = 20,
) -> tuple[dict, date, int, list[DatedBar], dict[str, list[DatedBar]]]:
    entry_date = date(2024, 6, 15)
    start = date(2024, 6, 1)
    end = date(2024, 8, 1)
    entry_idx = MIN_BARS_FOR_CONTEXT + 5
    bar_count = MIN_BARS_FOR_CONTEXT + max_holding + 25

    spy_bars = _spy_bars_aligned(
        bar_count=bar_count,
        entry_idx=entry_idx,
        entry_date=entry_date,
        spy_drop_pct=spy_drop_pct,
    )
    symbol_bars: dict[str, list[DatedBar]] = {}
    for sym in symbols:
        symbol_bars[sym] = _symbol_bars_with_single_entry(
            bar_count=bar_count,
            entry_idx=entry_idx,
            entry_date=entry_date,
        )

    params = {
        "symbols": symbols,
        "start": start,
        "end": end,
        "benchmark": "SPY",
        "max_holding": max_holding,
        "source": "test",
        "bars_db_path": bars_db,
        "strategy_db_path": strategy_db,
        "cost_pct": cost_pct,
    }
    return params, entry_date, entry_idx, spy_bars, symbol_bars


def _patch_get_daily_bars(
    monkeypatch: pytest.MonkeyPatch,
    *,
    spy_bars: list[DatedBar],
    symbol_bars: dict[str, list[DatedBar]],
) -> None:
    def fake_get_daily_bars(symbol, start_date, end_date, **kwargs):
        sym = symbol.strip().upper()
        if sym == "SPY":
            pool = spy_bars
        elif sym in symbol_bars:
            pool = symbol_bars[sym]
        else:
            return []
        return [
            b for b in pool if start_date <= date.fromisoformat(b["d"]) <= end_date
        ]

    monkeypatch.setattr("research.strategy_dataset.get_daily_bars", fake_get_daily_bars)


def _strategy_row_tuples(strategy_db: Path) -> list[tuple]:
    conn = connect(strategy_db)
    try:
        rows = conn.execute(
            """
            SELECT symbol, entry_date, features_json, label, blended_return_pct,
                   outcome, holding_days, same_day_ambiguous, max_holding,
                   stop_pct, first_target_pct, runner_target_pct, source
            FROM strategy_setups
            ORDER BY symbol, entry_date, outcome, label, blended_return_pct
            """
        ).fetchall()
    finally:
        conn.close()
    return [tuple(row) for row in rows]


def test_build_strategy_dataset_end_to_end(
    monkeypatch: pytest.MonkeyPatch,
    strategy_tmp_paths: tuple[Path, Path],
) -> None:
    bars_db, strategy_db = strategy_tmp_paths
    params, entry_date, entry_idx, spy_bars, symbol_bars = _build_params(
        bars_db,
        strategy_db,
        symbols=["AAPL", "MSFT"],
    )
    _patch_get_daily_bars(monkeypatch, spy_bars=spy_bars, symbol_bars=symbol_bars)

    summary = build_strategy_dataset(**params)

    assert summary["n_setups"] == 2
    assert summary["n_symbols_with_setups"] == 2
    assert summary["outcome_counts"]["first_then_runner"] == 2
    assert summary["win_rate"] == 1.0
    assert summary["avg_return"] == pytest.approx(6.5)

    conn = connect(strategy_db)
    try:
        rows = conn.execute(
            "SELECT * FROM strategy_setups ORDER BY symbol"
        ).fetchall()
    finally:
        conn.close()

    assert len(rows) == 2
    for row in rows:
        sym = row["symbol"]
        assert row["entry_date"] == entry_date.isoformat()
        assert row["outcome"] == "first_then_runner"
        assert row["blended_return_pct"] == pytest.approx(6.5)
        assert row["label"] == 1
        features = json.loads(row["features_json"])
        assert "mkt_ret_3d" in features
        assert "rsi_14" in features

        expected = simulate_exit(
            symbol_bars[sym],
            entry_idx,
            max_holding=params["max_holding"],
        )
        assert expected is not None
        assert row["outcome"] == expected["outcome"]
        assert row["blended_return_pct"] == pytest.approx(expected["blended_return_pct"])
        assert row["holding_days"] == expected["holding_days"]


def test_build_strategy_dataset_far_support_min_reduces_setups(
    monkeypatch: pytest.MonkeyPatch,
    strategy_tmp_paths: tuple[Path, Path],
) -> None:
    bars_db, strategy_db = strategy_tmp_paths
    params, _, _, spy_bars, symbol_bars = _build_params(
        bars_db,
        strategy_db,
        symbols=["AAPL", "MSFT"],
    )
    _patch_get_daily_bars(monkeypatch, spy_bars=spy_bars, symbol_bars=symbol_bars)

    unfiltered = build_strategy_dataset(**params)
    filtered = build_strategy_dataset(**params, far_support_min=5.0, replace=True)

    assert unfiltered["n_setups"] == 2
    assert filtered["n_setups"] < unfiltered["n_setups"]
    assert filtered["n_setups"] == 0
    assert len(_strategy_row_tuples(strategy_db)) == 0


def test_build_strategy_dataset_momentum_end_to_end(
    monkeypatch: pytest.MonkeyPatch,
    strategy_tmp_paths: tuple[Path, Path],
) -> None:
    bars_db, strategy_db = strategy_tmp_paths
    params, entry_date, entry_idx, spy_bars, symbol_bars = _build_momentum_params(
        bars_db,
        strategy_db,
        symbols=["AAPL"],
    )
    _patch_get_daily_bars(monkeypatch, spy_bars=spy_bars, symbol_bars=symbol_bars)

    summary = build_strategy_dataset(**params)

    assert summary["n_setups"] == 1
    assert summary["strategy_type"] == "momentum"
    assert summary["outcome_counts"]["first_then_runner"] == 1
    assert summary["avg_return"] == pytest.approx(6.5)

    conn = connect(strategy_db)
    try:
        row = conn.execute("SELECT * FROM strategy_setups").fetchone()
    finally:
        conn.close()

    assert row is not None
    assert row["entry_date"] == entry_date.isoformat()
    assert row["outcome"] == "first_then_runner"
    assert row["blended_return_pct"] == pytest.approx(6.5)

    expected = simulate_exit(
        symbol_bars["AAPL"],
        entry_idx,
        max_holding=params["max_holding"],
    )
    assert expected is not None
    assert row["outcome"] == expected["outcome"]
    assert row["blended_return_pct"] == pytest.approx(expected["blended_return_pct"])
    assert row["holding_days"] == expected["holding_days"]


def test_build_strategy_dataset_rsi2_end_to_end(
    monkeypatch: pytest.MonkeyPatch,
    strategy_tmp_paths: tuple[Path, Path],
) -> None:
    bars_db, strategy_db = strategy_tmp_paths
    params, entry_date, entry_idx, spy_bars, symbol_bars = _build_rsi2_params(
        bars_db,
        strategy_db,
        symbols=["AAPL"],
    )
    _patch_get_daily_bars(monkeypatch, spy_bars=spy_bars, symbol_bars=symbol_bars)

    summary = build_strategy_dataset(**params)

    assert summary["n_setups"] == 1
    assert summary["strategy_type"] == "rsi2"
    assert summary["outcome_counts"]["first_then_runner"] == 1
    assert summary["avg_return"] == pytest.approx(6.5)

    conn = connect(strategy_db)
    try:
        row = conn.execute("SELECT * FROM strategy_setups").fetchone()
    finally:
        conn.close()

    assert row is not None
    assert row["entry_date"] == entry_date.isoformat()
    assert row["outcome"] == "first_then_runner"
    assert row["blended_return_pct"] == pytest.approx(6.5)

    expected = simulate_exit(
        symbol_bars["AAPL"],
        entry_idx,
        max_holding=params["max_holding"],
    )
    assert expected is not None
    assert row["outcome"] == expected["outcome"]
    assert row["blended_return_pct"] == pytest.approx(expected["blended_return_pct"])
    assert row["holding_days"] == expected["holding_days"]


def test_build_strategy_dataset_consec_down_end_to_end(
    monkeypatch: pytest.MonkeyPatch,
    strategy_tmp_paths: tuple[Path, Path],
) -> None:
    bars_db, strategy_db = strategy_tmp_paths
    params, entry_date, entry_idx, spy_bars, symbol_bars = _build_consec_down_params(
        bars_db,
        strategy_db,
        symbols=["AAPL"],
    )
    _patch_get_daily_bars(monkeypatch, spy_bars=spy_bars, symbol_bars=symbol_bars)

    summary = build_strategy_dataset(**params)

    assert summary["n_setups"] == 1
    assert summary["strategy_type"] == "consec_down"
    assert summary["outcome_counts"]["first_then_runner"] == 1
    assert summary["avg_return"] == pytest.approx(6.5)

    conn = connect(strategy_db)
    try:
        row = conn.execute("SELECT * FROM strategy_setups").fetchone()
    finally:
        conn.close()

    assert row is not None
    assert row["entry_date"] == entry_date.isoformat()
    assert row["outcome"] == "first_then_runner"
    assert row["blended_return_pct"] == pytest.approx(6.5)

    expected = simulate_exit(
        symbol_bars["AAPL"],
        entry_idx,
        max_holding=params["max_holding"],
    )
    assert expected is not None
    assert row["outcome"] == expected["outcome"]
    assert row["blended_return_pct"] == pytest.approx(expected["blended_return_pct"])
    assert row["holding_days"] == expected["holding_days"]


def test_build_strategy_dataset_market_up_rejects(
    monkeypatch: pytest.MonkeyPatch,
    strategy_tmp_paths: tuple[Path, Path],
) -> None:
    bars_db, strategy_db = strategy_tmp_paths
    params, _, _, spy_bars, symbol_bars = _build_params(
        bars_db,
        strategy_db,
        symbols=["AAPL"],
        spy_drop_pct=1.0,
    )
    _patch_get_daily_bars(monkeypatch, spy_bars=spy_bars, symbol_bars=symbol_bars)

    summary = build_strategy_dataset(**params)

    assert summary["n_setups"] == 0
    assert len(_strategy_row_tuples(strategy_db)) == 0


def test_build_strategy_dataset_replace_avoids_duplication(
    monkeypatch: pytest.MonkeyPatch,
    strategy_tmp_paths: tuple[Path, Path],
) -> None:
    bars_db, strategy_db = strategy_tmp_paths
    params, _, _, spy_bars, symbol_bars = _build_params(
        bars_db,
        strategy_db,
        symbols=["AAPL", "MSFT"],
    )
    _patch_get_daily_bars(monkeypatch, spy_bars=spy_bars, symbol_bars=symbol_bars)

    first = build_strategy_dataset(**params, replace=True)
    second = build_strategy_dataset(**params, replace=True)

    assert first["n_setups"] == 2
    assert second["n_setups"] == first["n_setups"]
    assert len(_strategy_row_tuples(strategy_db)) == first["n_setups"]


def test_build_strategy_dataset_append_doubles_rows(
    monkeypatch: pytest.MonkeyPatch,
    strategy_tmp_paths: tuple[Path, Path],
) -> None:
    bars_db, strategy_db = strategy_tmp_paths
    params, _, _, spy_bars, symbol_bars = _build_params(
        bars_db,
        strategy_db,
        symbols=["AAPL"],
    )
    _patch_get_daily_bars(monkeypatch, spy_bars=spy_bars, symbol_bars=symbol_bars)

    first = build_strategy_dataset(**params, replace=False)
    second = build_strategy_dataset(**params, replace=False)

    assert first["n_setups"] == 1
    assert second["n_setups"] == first["n_setups"]
    assert len(_strategy_row_tuples(strategy_db)) == first["n_setups"] * 2


def _seed_strategy_bars_cache(
    bars_db: Path,
    symbols: list[str],
    *,
    entry_date: date,
    entry_idx: int,
    max_holding: int,
    spy_drop_pct: float = -2.0,
    source: str = "test",
) -> None:
    bar_count = MIN_BARS_FOR_CONTEXT + max_holding + 25
    spy_bars = _spy_bars_aligned(
        bar_count=bar_count,
        entry_idx=entry_idx,
        entry_date=entry_date,
        spy_drop_pct=spy_drop_pct,
    )
    cache_bars("SPY", spy_bars, source=source, db_path=bars_db)
    for symbol in symbols:
        symbol_bars = _symbol_bars_with_single_entry(
            bar_count=bar_count,
            entry_idx=entry_idx,
            entry_date=entry_date,
        )
        cache_bars(symbol, symbol_bars, source=source, db_path=bars_db)


def test_build_strategy_dataset_parallel_matches_sequential(
    tmp_path: Path,
) -> None:
    bars_db = tmp_path / "bars.db"
    seq_db = tmp_path / "strategy_seq.db"
    par_db = tmp_path / "strategy_par.db"
    entry_date = date(2024, 6, 15)
    entry_idx = MIN_BARS_FOR_CONTEXT + 5
    symbols = ["AAPL", "MSFT", "NVDA"]
    start = date(2024, 6, 1)
    end = date(2024, 8, 1)
    max_holding = 20
    _seed_strategy_bars_cache(
        bars_db,
        symbols,
        entry_date=entry_date,
        entry_idx=entry_idx,
        max_holding=max_holding,
    )
    params = {
        "symbols": symbols,
        "start": start,
        "end": end,
        "benchmark": "SPY",
        "max_holding": max_holding,
        "source": "test",
        "bars_db_path": bars_db,
        "strategy_db_path": seq_db,
    }

    seq_summary = build_strategy_dataset(**params, jobs=1)
    par_summary = build_strategy_dataset(
        **{**params, "strategy_db_path": par_db},
        jobs=2,
    )

    for key in (
        "n_setups",
        "n_symbols_with_setups",
        "win_rate",
        "avg_return",
        "median_return",
    ):
        assert par_summary[key] == seq_summary[key]
    assert seq_summary["n_setups"] > 0
    assert _strategy_row_tuples(seq_db) == _strategy_row_tuples(par_db)


def test_build_strategy_dataset_cost_shifts_label(
    monkeypatch: pytest.MonkeyPatch,
    strategy_tmp_paths: tuple[Path, Path],
) -> None:
    bars_db, strategy_db = strategy_tmp_paths
    params, entry_date, entry_idx, spy_bars, symbol_bars = _build_params(
        bars_db,
        strategy_db,
        symbols=["AAPL"],
        cost_pct=2.0,
    )
    symbol_bars["AAPL"] = _symbol_bars_with_single_entry(
        bar_count=MIN_BARS_FOR_CONTEXT + params["max_holding"] + 25,
        entry_idx=entry_idx,
        entry_date=entry_date,
        exit_style="breakeven",
    )
    _patch_get_daily_bars(monkeypatch, spy_bars=spy_bars, symbol_bars=symbol_bars)

    expected = simulate_exit(
        symbol_bars["AAPL"],
        entry_idx,
        max_holding=params["max_holding"],
    )
    assert expected is not None
    assert expected["blended_return_pct"] == pytest.approx(1.5)
    assert expected["label"] == 1

    summary = build_strategy_dataset(**params)

    assert summary["n_setups"] == 1
    assert summary["win_rate"] == 0.0

    conn = connect(strategy_db)
    try:
        row = conn.execute("SELECT label, blended_return_pct FROM strategy_setups").fetchone()
    finally:
        conn.close()

    assert row["label"] == 0
    assert row["blended_return_pct"] == pytest.approx(-0.5)


def test_default_cost_is_single_sourced() -> None:
    """The build default is the validated-book 10 bps RT, from DEFAULT_COST_PCT."""
    assert DEFAULT_COST_PCT == pytest.approx(0.10)
    sig = inspect.signature(build_strategy_dataset)
    assert sig.parameters["cost_pct"].default == DEFAULT_COST_PCT


def test_build_strategy_dataset_short_end_to_end(
    monkeypatch: pytest.MonkeyPatch,
    strategy_tmp_paths: tuple[Path, Path],
) -> None:
    bars_db, strategy_db = strategy_tmp_paths
    entry_date = date(2024, 6, 15)
    start = date(2024, 6, 1)
    end = date(2024, 8, 1)
    entry_idx = MIN_BARS_FOR_CONTEXT + 5
    max_holding = 20
    bar_count = MIN_BARS_FOR_CONTEXT + max_holding + 25

    spy_bars = _spy_bars_aligned_rally(
        bar_count=bar_count,
        entry_idx=entry_idx,
        entry_date=entry_date,
        spy_rally_pct=2.0,
    )
    symbol_bars = {
        "AAPL": _symbol_bars_with_single_short_entry(
            bar_count=bar_count,
            entry_idx=entry_idx,
            entry_date=entry_date,
        ),
    }
    _patch_get_daily_bars(monkeypatch, spy_bars=spy_bars, symbol_bars=symbol_bars)

    params = {
        "symbols": ["AAPL"],
        "start": start,
        "end": end,
        "benchmark": "SPY",
        "max_holding": max_holding,
        "source": "test",
        "bars_db_path": bars_db,
        "strategy_db_path": strategy_db,
        "cost_pct": 0.0,
        "side": "short",
    }
    summary = build_strategy_dataset(**params)

    assert summary["n_setups"] == 1
    assert summary["side"] == "short"
    assert summary["outcome_counts"]["first_then_runner"] == 1
    assert summary["avg_return"] == pytest.approx(6.5)

    conn = connect(strategy_db)
    try:
        row = conn.execute("SELECT * FROM strategy_setups").fetchone()
    finally:
        conn.close()

    assert row is not None
    assert row["entry_date"] == entry_date.isoformat()
    assert row["outcome"] == "first_then_runner"
    assert row["blended_return_pct"] == pytest.approx(6.5)

    expected = simulate_exit(
        symbol_bars["AAPL"],
        entry_idx,
        max_holding=max_holding,
        side="short",
    )
    assert expected is not None
    assert row["outcome"] == expected["outcome"]
    assert row["blended_return_pct"] == pytest.approx(expected["blended_return_pct"])
    assert row["holding_days"] == expected["holding_days"]


def test_build_strategy_dataset_skips_benchmark_symbol(
    tmp_path: Path,
) -> None:
    """SPY in the symbol list should not produce duplicate benchmark rows."""
    bars_db = tmp_path / "bars.db"
    strategy_db = tmp_path / "strategy_labeled.db"
    warmup = MIN_BARS_FOR_CONTEXT + 30
    start = date(2024, 6, 1)
    end = date(2024, 8, 1)
    fetch_start = start - timedelta(days=warmup + 30)
    _seed_flat_cache(bars_db, ["SPY", "AAPL"], fetch_start, warmup + 60)

    build_strategy_dataset(
        ["SPY", "AAPL"],
        start,
        end,
        benchmark="SPY",
        max_holding=5,
        source="test",
        bars_db_path=bars_db,
        strategy_db_path=strategy_db,
        healthy_required=False,
        oversold_3d_max=100.0,
        mkt_down_3d_max=100.0,
    )

    conn = connect(strategy_db)
    try:
        symbols = {
            row["symbol"]
            for row in conn.execute("SELECT DISTINCT symbol FROM strategy_setups").fetchall()
        }
    finally:
        conn.close()

    assert "SPY" not in symbols


def _seed_flat_cache(
    bars_db: Path,
    symbols: list[str],
    start: date,
    count: int,
    source: str = "test",
) -> None:
    for symbol in symbols:
        closes = [100.0 + i * 0.1 for i in range(count)]
        bars = _dated_bars_from_closes(closes, start)
        cache_bars(symbol, bars, source=source, db_path=bars_db)
