"""Unit tests for signals.indicators — pure, deterministic feature vocabulary."""

from __future__ import annotations

import pytest

from signals.indicators import (
    Bar,
    atr,
    closes,
    cross_above,
    cross_below,
    distance_from_high,
    ema,
    highs,
    lows,
    pct_change,
    rel_strength,
    rsi,
    sma,
    volume_vs_avg,
    volumes,
)


def test_bar_extractors() -> None:
    bars: list[Bar] = [
        {"o": 1.0, "h": 2.0, "l": 0.5, "c": 1.5, "v": 100.0},
        {"o": 1.5, "h": 3.0, "l": 1.0, "c": 2.5, "v": 200.0},
    ]
    assert closes(bars) == [1.5, 2.5]
    assert highs(bars) == [2.0, 3.0]
    assert lows(bars) == [0.5, 1.0]
    assert volumes(bars) == [100.0, 200.0]


def test_pct_change_known_and_insufficient() -> None:
    assert pct_change([100, 110], 1) == pytest.approx(10.0)
    assert pct_change([100, 110, 121], 2) == pytest.approx(21.0)
    assert pct_change([100], 1) is None
    assert pct_change([100, 110], 2) is None
    assert pct_change([0, 110], 1) is None


def test_sma_known_and_insufficient() -> None:
    assert sma([1, 2, 3, 4], 3) == 3.0
    assert sma([1, 2], 3) is None


def test_ema_known_and_insufficient() -> None:
    # SMA seed of first 3 = 2.0; multiplier 0.5 → EMA after 4 = 3.0, after 5 = 4.0
    assert ema([1, 2, 3, 4, 5], 3) == pytest.approx(4.0)
    assert ema([1, 2], 3) is None


def test_cross_above() -> None:
    assert cross_above([1, 2], [3, 1]) is True
    assert cross_above([1, 1], [2, 2]) is False
    assert cross_above([2, 2], [1, 1]) is False
    assert cross_above([1, 2], [1, 2]) is False
    assert cross_above([1], [1]) is False
    assert cross_above([1, 2], [1]) is False


def test_cross_below() -> None:
    assert cross_below([3, 2], [1, 3]) is True
    assert cross_below([2, 2], [1, 1]) is False
    assert cross_below([1, 3], [1, 2]) is False
    assert cross_below([3, 3], [1, 2]) is False


def test_rsi_textbook_and_all_up() -> None:
    # Wilder RSI on a short hand-verified series (n=2 seed + one smooth step).
    assert rsi([10, 12, 11, 13], n=2) == pytest.approx(85.71428571428571)

    all_up = [float(100 + i) for i in range(15)]
    assert rsi(all_up, n=14) == pytest.approx(100.0)
    assert rsi([100.0, 101.0], n=14) is None


def test_atr_known_and_insufficient() -> None:
    h = [48.70, 48.72, 48.90]
    low = [47.79, 48.14, 48.16]
    c = [48.16, 48.61, 48.75]
    # TRs: 0.91, 0.58, 0.74 → seed SMA(2)=0.745, Wilder step → 0.7425
    assert atr(h, low, c, n=2) == pytest.approx(0.7425)

    assert atr([10.0], [9.0], [9.5], n=2) is None
    assert atr([10.0, 11.0], [9.0, 10.0], [9.5, 10.5], n=3) is None


def test_rel_strength_known_and_insufficient() -> None:
    assert rel_strength([100, 110], [100, 105], window=1) == pytest.approx(5.0)
    assert rel_strength([100], [100, 105], window=1) is None


def test_distance_from_high_known_and_insufficient() -> None:
    assert distance_from_high([90, 95, 100, 98], lookback=4) == pytest.approx(-2.0)
    assert distance_from_high([100, 110], lookback=3) is None


def test_volume_vs_avg_known_and_insufficient() -> None:
    assert volume_vs_avg([100, 200, 300, 400], n=3) == pytest.approx(2.0)
    assert volume_vs_avg([100, 200], n=3) is None
    assert volume_vs_avg([0, 0, 0, 100], n=3) is None
