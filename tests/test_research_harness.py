"""Tests for offline research harness: labeling, features, and dataset assembly."""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import pytest

from research.bars import cache_bars
from research.dataset import build_labeled_dataset
from research.features import compute_features
from research.labeling import triple_barrier_label
from signals.indicators import Bar, atr, closes, highs, lows
from signals.trade_context import MIN_BARS_FOR_CONTEXT, DatedBar


def _bar(
    c: float,
    *,
    o: float | None = None,
    h: float | None = None,
    low: float | None = None,
    v: float = 1000.0,
) -> Bar:
    return {
        "o": o if o is not None else c,
        "h": h if h is not None else c + 1.0,
        "l": low if low is not None else c - 1.0,
        "c": c,
        "v": v,
    }


def _dated_bars_from_closes(
    close_series: list[float],
    start_date: date,
    *,
    highs_override: list[float] | None = None,
    lows_override: list[float] | None = None,
    volume: float = 1000.0,
) -> list[DatedBar]:
    bars: list[DatedBar] = []
    d = start_date
    for i, c in enumerate(close_series):
        prev_c = close_series[i - 1] if i > 0 else c
        h = highs_override[i] if highs_override else c + 1.0
        lo = lows_override[i] if lows_override else c - 1.0
        bars.append(
            {
                "d": d.isoformat(),
                "o": prev_c if i > 0 else c,
                "h": h,
                "l": lo,
                "c": c,
                "v": volume,
            }
        )
        d += timedelta(days=1)
    return bars


def _warmup_bars(count: int, base: float = 100.0) -> list[Bar]:
    """Flat-ish bars with enough history for ATR(14)."""
    return [_bar(base + (i % 3) * 0.1) for i in range(count)]


# --- triple_barrier_label ---


def test_triple_barrier_upper_hit_first_is_win() -> None:
    bars = _warmup_bars(20, base=100.0)
    entry_idx = len(bars) - 1
    prefix = bars[: entry_idx + 1]
    atr_val = atr(highs(prefix), lows(prefix), closes(prefix), 14)
    assert atr_val is not None
    upper = bars[entry_idx]["c"] + 2.0 * atr_val
    bars.append(_bar(bars[entry_idx]["c"], h=upper + 1.0, low=bars[entry_idx]["c"] - 0.5))

    result = triple_barrier_label(bars, entry_idx, upper_atr_mult=2.0, lower_atr_mult=2.0)
    assert result is not None
    assert result["outcome"] == "win"
    assert result["label"] == 1
    assert result["exit_idx"] == entry_idx + 1
    assert result["holding_days"] == 1
    expected_return = (result["upper"] / result["entry_price"] - 1) * 100
    assert result["return_pct"] == expected_return


def test_triple_barrier_lower_hit_first_is_loss() -> None:
    bars = _warmup_bars(20, base=100.0)
    entry_idx = len(bars) - 1
    prefix = bars[: entry_idx + 1]
    atr_val = atr(highs(prefix), lows(prefix), closes(prefix), 14)
    assert atr_val is not None
    lower = bars[entry_idx]["c"] - 2.0 * atr_val
    bars.append(_bar(bars[entry_idx]["c"], h=bars[entry_idx]["c"] + 0.5, low=lower - 1.0))

    result = triple_barrier_label(bars, entry_idx)
    assert result is not None
    assert result["outcome"] == "loss"
    assert result["label"] == 0
    assert result["exit_idx"] == entry_idx + 1
    assert result["holding_days"] == 1


def test_triple_barrier_timeout_uses_last_close() -> None:
    bars = _warmup_bars(20, base=100.0)
    entry_idx = len(bars) - 1
    entry_price = bars[entry_idx]["c"]
  # quiet forward bars — no barrier touch
    for _ in range(5):
        bars.append(_bar(entry_price, h=entry_price + 0.2, low=entry_price - 0.2))

    result = triple_barrier_label(bars, entry_idx, max_holding=5)
    assert result is not None
    assert result["outcome"] == "timeout"
    assert result["exit_idx"] == entry_idx + 5
    assert result["holding_days"] == 5
    exit_close = bars[result["exit_idx"]]["c"]
    assert result["return_pct"] == (exit_close / entry_price - 1) * 100


def test_triple_barrier_same_bar_both_barriers_is_loss() -> None:
    bars = _warmup_bars(20, base=100.0)
    entry_idx = len(bars) - 1
    prefix = bars[: entry_idx + 1]
    atr_val = atr(highs(prefix), lows(prefix), closes(prefix), 14)
    assert atr_val is not None
    upper = bars[entry_idx]["c"] + 2.0 * atr_val
    lower = bars[entry_idx]["c"] - 2.0 * atr_val
    bars.append(_bar(bars[entry_idx]["c"], h=upper + 1.0, low=lower - 1.0))

    result = triple_barrier_label(bars, entry_idx)
    assert result is not None
    assert result["outcome"] == "loss"
    assert result["label"] == 0
    assert result["exit_idx"] == entry_idx + 1


def test_triple_barrier_insufficient_history_returns_none() -> None:
    bars = _warmup_bars(10, base=100.0)
    assert triple_barrier_label(bars, len(bars) - 1) is None


def test_triple_barrier_wider_barriers_more_timeouts() -> None:
    """Larger ATR multipliers widen barriers — identical forward path times out more often."""
    base_bars = _warmup_bars(20, base=100.0)
    entry_idx = len(base_bars) - 1
    entry_price = base_bars[entry_idx]["c"]
    prefix = base_bars[: entry_idx + 1]
    atr_val = atr(highs(prefix), lows(prefix), closes(prefix), 14)
    assert atr_val is not None

    # Rally touches a 1×ATR upper barrier but stays inside a 4×ATR band.
    touch_high = entry_price + atr_val + 0.5
    forward = [_bar(entry_price, h=touch_high, low=entry_price - 0.5) for _ in range(10)]

    narrow_bars = base_bars + forward
    wide_bars = base_bars + forward

    narrow = triple_barrier_label(narrow_bars, entry_idx, upper_atr_mult=1.0, lower_atr_mult=1.0)
    wide = triple_barrier_label(wide_bars, entry_idx, upper_atr_mult=4.0, lower_atr_mult=4.0)
    assert narrow is not None
    assert wide is not None
    assert narrow["outcome"] == "win"
    assert wide["outcome"] == "timeout"


def test_triple_barrier_bad_entry_price_returns_none() -> None:
    bars = _warmup_bars(20, base=100.0)
    entry_idx = len(bars) - 1
    bars[entry_idx] = _bar(0.0, h=1.0, low=0.0)

    assert triple_barrier_label(bars, entry_idx) is None


def test_triple_barrier_bad_forward_bar_returns_none() -> None:
    bars = _warmup_bars(20, base=100.0)
    entry_idx = len(bars) - 1
    entry_price = bars[entry_idx]["c"]
    bars.append(_bar(entry_price, h=entry_price + 0.2, low=0.0))

    assert triple_barrier_label(bars, entry_idx, max_holding=1) is None


def test_triple_barrier_timeout_clips_absurd_upper_return() -> None:
    bars = _warmup_bars(20, base=100.0)
    entry_idx = len(bars) - 1
    entry_price = bars[entry_idx]["c"]
    prefix = bars[: entry_idx + 1]
    atr_val = atr(highs(prefix), lows(prefix), closes(prefix), 14)
    assert atr_val is not None
    upper = entry_price + 2.0 * atr_val
    lower = entry_price - 2.0 * atr_val

    for _ in range(4):
        bars.append(_bar(entry_price, h=upper - 0.5, low=lower + 0.5))
    bars.append(_bar(50_000.0, h=upper - 0.5, low=lower + 0.5))

    result = triple_barrier_label(bars, entry_idx, max_holding=5)
    assert result is not None
    assert result["outcome"] == "timeout"
    assert result["return_pct"] == 100.0
    assert result["label"] == 1


def test_triple_barrier_timeout_clips_near_zero_lower_return() -> None:
    bars = _warmup_bars(20, base=100.0)
    entry_idx = len(bars) - 1
    entry_price = bars[entry_idx]["c"]
    prefix = bars[: entry_idx + 1]
    atr_val = atr(highs(prefix), lows(prefix), closes(prefix), 14)
    assert atr_val is not None
    upper = entry_price + 2.0 * atr_val
    lower = entry_price - 2.0 * atr_val

    bars.append(_bar(0.01, o=entry_price, h=upper - 0.5, low=lower + 0.5))

    result = triple_barrier_label(bars, entry_idx, max_holding=1)
    assert result is not None
    assert result["outcome"] == "timeout"
    assert result["return_pct"] >= -100.0
    assert result["return_pct"] == pytest.approx(-99.99, abs=0.01)
    assert result["label"] == 0


def test_triple_barrier_normal_timeout_return_unchanged() -> None:
    bars = _warmup_bars(20, base=100.0)
    entry_idx = len(bars) - 1
    entry_price = bars[entry_idx]["c"]
    exit_price = entry_price * 1.05
    prefix = bars[: entry_idx + 1]
    atr_val = atr(highs(prefix), lows(prefix), closes(prefix), 14)
    assert atr_val is not None
    upper = entry_price + 4.0 * atr_val
    bars.append(
        _bar(exit_price, o=entry_price, h=min(exit_price + 0.2, upper - 0.5), low=exit_price - 0.2)
    )

    result = triple_barrier_label(
        bars, entry_idx, max_holding=1, upper_atr_mult=4.0, lower_atr_mult=4.0
    )
    assert result is not None
    assert result["outcome"] == "timeout"
    assert result["return_pct"] == pytest.approx(5.0)
    assert result["label"] == 1


def test_triple_barrier_enters_at_next_bar_open() -> None:
    bars = _warmup_bars(20, base=100.0)
    entry_idx = len(bars) - 1
    prefix = bars[: entry_idx + 1]
    atr_val = atr(highs(prefix), lows(prefix), closes(prefix), 14)
    assert atr_val is not None
    # Entry bar opens well above the signal close — barriers must anchor there.
    bars.append(_bar(107.0, o=105.0, h=107.5, low=104.5))

    result = triple_barrier_label(bars, entry_idx)
    assert result is not None
    assert result["entry_price"] == pytest.approx(105.0)
    assert result["upper"] == pytest.approx(105.0 + 2.0 * atr_val)
    assert result["lower"] == pytest.approx(105.0 - 2.0 * atr_val)
    assert result["outcome"] == "timeout"
    assert result["return_pct"] == pytest.approx((107.0 / 105.0 - 1) * 100)
    assert result["holding_days"] == 1


def test_triple_barrier_signal_without_next_bar_returns_none() -> None:
    bars = _warmup_bars(20, base=100.0)
    # No bar after the signal — nothing to fill at, so no label.
    assert triple_barrier_label(bars, len(bars) - 1) is None


# --- compute_features point-in-time ---


def test_compute_features_ignores_future_bars() -> None:
    n = MIN_BARS_FOR_CONTEXT + 20
    closes_stable = [100.0 + i * 0.05 for i in range(n)]
    start = date(2020, 1, 1)
    bars = _dated_bars_from_closes(closes_stable, start)

    idx = MIN_BARS_FOR_CONTEXT - 1
    features_prefix = compute_features(bars[: idx + 1], idx)

    # Future spike would change RSI / pct_change if look-ahead crept in
    closes_spiky = list(closes_stable)
    for j in range(idx + 1, n):
        closes_spiky[j] = closes_stable[j] + 50.0
    bars_with_future = _dated_bars_from_closes(closes_spiky, start)
    features_full = compute_features(bars_with_future, idx)

    assert features_prefix is not None
    assert features_full is not None
    assert features_prefix == features_full


def test_compute_features_requires_min_history() -> None:
    bars = _dated_bars_from_closes([100.0] * 50, date(2024, 1, 1))
    assert compute_features(bars, len(bars) - 1) is None


# --- dataset assembler ---


def _synthetic_symbol_bars(
    count: int,
    start: date,
    *,
    spike_at: int | None = None,
) -> list[DatedBar]:
    closes_list = [100.0 + i * 0.1 for i in range(count)]
    if spike_at is not None:
        closes_list[spike_at] += 25.0
    return _dated_bars_from_closes(closes_list, start)


@pytest.fixture
def research_tmp_paths(tmp_path: Path) -> tuple[Path, Path]:
    bars_db = tmp_path / "bars.db"
    labeled_db = tmp_path / "labeled.db"
    return bars_db, labeled_db


def test_build_labeled_dataset_inserts_rows(
    monkeypatch: pytest.MonkeyPatch,
    research_tmp_paths: tuple[Path, Path],
) -> None:
    bars_db, labeled_db = research_tmp_paths
    warmup = MIN_BARS_FOR_CONTEXT + 15
    start = date(2024, 6, 1)
    end = date(2024, 8, 1)
    fetch_start = start - timedelta(days=warmup + 30)

    spy_bars = _synthetic_symbol_bars(warmup + 60, fetch_start)
    aapl_bars = _synthetic_symbol_bars(warmup + 60, fetch_start)

    def fake_get_daily_bars(symbol, start_date, end_date, **kwargs):
        sym = symbol.strip().upper()
        if sym == "SPY":
            return [b for b in spy_bars if start_date <= date.fromisoformat(b["d"]) <= end_date]
        if sym == "AAPL":
            return [b for b in aapl_bars if start_date <= date.fromisoformat(b["d"]) <= end_date]
        return []

    monkeypatch.setattr("research.dataset.get_daily_bars", fake_get_daily_bars)

    summary = build_labeled_dataset(
        ["AAPL"],
        start,
        end,
        benchmark="SPY",
        max_holding=5,
        entry_stride=3,
        bars_db_path=bars_db,
        labeled_db_path=labeled_db,
    )

    assert summary["rows"] > 0
    assert summary["wins"] + summary["losses"] + summary["timeouts"] == summary["rows"]

    from db.base import connect

    conn = connect(labeled_db)
    try:
        rows = conn.execute("SELECT * FROM labeled_setups").fetchall()
    finally:
        conn.close()

    assert len(rows) == summary["rows"]
    for row in rows:
        assert row["symbol"] == "AAPL"
        features = json.loads(row["features_json"])
        assert isinstance(features, dict)
        assert row["outcome"] in ("win", "loss", "timeout")
        assert row["label"] in (0, 1)
        assert row["return_pct"] is not None
        assert row["holding_days"] >= 1


def _seed_bars_cache(
    bars_db: Path,
    symbols: list[str],
    *,
    start: date,
    count: int,
    source: str = "test",
) -> None:
    for i, symbol in enumerate(symbols):
        bars = _synthetic_symbol_bars(count, start, spike_at=count // 2 + i * 3)
        cache_bars(symbol, bars, source=source, db_path=bars_db)


def _labeled_row_tuples(labeled_db: Path) -> list[tuple]:
    from db.base import connect

    conn = connect(labeled_db)
    try:
        rows = conn.execute(
            """
            SELECT symbol, entry_date, features_json, label, outcome,
                   return_pct, holding_days, max_holding,
                   upper_atr_mult, lower_atr_mult, source
            FROM labeled_setups
            ORDER BY symbol, entry_date, outcome, label, return_pct
            """
        ).fetchall()
    finally:
        conn.close()
    return [tuple(row) for row in rows]


def _multi_symbol_build_params(
    bars_db: Path,
    labeled_db: Path,
    *,
    symbols: list[str] | None = None,
) -> dict:
    warmup = MIN_BARS_FOR_CONTEXT + 15
    start = date(2024, 6, 1)
    end = date(2024, 8, 1)
    fetch_start = start - timedelta(days=warmup + 30)
    syms = symbols or ["AAPL", "MSFT", "NVDA"]
    _seed_bars_cache(
        bars_db,
        ["SPY", *syms],
        start=fetch_start,
        count=warmup + 60,
        source="test",
    )
    return {
        "symbols": syms,
        "start": start,
        "end": end,
        "benchmark": "SPY",
        "max_holding": 5,
        "entry_stride": 3,
        "source": "test",
        "bars_db_path": bars_db,
        "labeled_db_path": labeled_db,
    }


def test_build_labeled_dataset_parallel_matches_sequential(
    tmp_path: Path,
) -> None:
    bars_db = tmp_path / "bars.db"
    seq_db = tmp_path / "labeled_seq.db"
    par_db = tmp_path / "labeled_par.db"
    params = _multi_symbol_build_params(bars_db, seq_db)

    seq_summary = build_labeled_dataset(**params, jobs=1)
    par_summary = build_labeled_dataset(
        **{**params, "labeled_db_path": par_db},
        jobs=2,
    )

    for key in ("rows", "wins", "losses", "timeouts"):
        assert par_summary[key] == seq_summary[key]
    assert seq_summary["rows"] > 0

    assert _labeled_row_tuples(seq_db) == _labeled_row_tuples(par_db)


def test_build_labeled_dataset_replace_avoids_duplication(
    tmp_path: Path,
) -> None:
    bars_db = tmp_path / "bars.db"
    labeled_db = tmp_path / "labeled.db"
    params = _multi_symbol_build_params(bars_db, labeled_db, symbols=["AAPL", "MSFT"])

    first = build_labeled_dataset(**params, replace=True)
    second = build_labeled_dataset(**params, replace=True)

    assert first["rows"] > 0
    assert second["rows"] == first["rows"]
    assert len(_labeled_row_tuples(labeled_db)) == first["rows"]


def test_build_labeled_dataset_append_doubles_rows(
    tmp_path: Path,
) -> None:
    bars_db = tmp_path / "bars.db"
    labeled_db = tmp_path / "labeled.db"
    params = _multi_symbol_build_params(bars_db, labeled_db, symbols=["AAPL"])

    first = build_labeled_dataset(**params, replace=False)
    second = build_labeled_dataset(**params, replace=False)

    assert first["rows"] > 0
    assert second["rows"] == first["rows"]
    assert len(_labeled_row_tuples(labeled_db)) == first["rows"] * 2
