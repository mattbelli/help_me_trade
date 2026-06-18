"""Pure technical indicators and feature helpers for the signal engine."""

from __future__ import annotations

from typing import TypedDict


class Bar(TypedDict):
    o: float
    h: float
    l: float  # noqa: E741
    c: float
    v: float


def closes(bars: list[Bar]) -> list[float]:
    return [bar["c"] for bar in bars]


def highs(bars: list[Bar]) -> list[float]:
    return [bar["h"] for bar in bars]


def lows(bars: list[Bar]) -> list[float]:
    return [bar["l"] for bar in bars]


def volumes(bars: list[Bar]) -> list[float]:
    return [bar["v"] for bar in bars]


def pct_change(values: list[float], window: int) -> float | None:
    if len(values) <= window:
        return None
    earlier = values[-1 - window]
    if earlier == 0:
        return None
    return (values[-1] / earlier - 1) * 100


def sma(values: list[float], n: int) -> float | None:
    if len(values) < n:
        return None
    window = values[-n:]
    return sum(window) / n


def ema(values: list[float], n: int) -> float | None:
    if len(values) < n:
        return None
    multiplier = 2 / (n + 1)
    ema_value = sum(values[:n]) / n
    for value in values[n:]:
        ema_value = (value - ema_value) * multiplier + ema_value
    return ema_value


def cross_above(a: list[float], b: list[float]) -> bool:
    if len(a) != len(b) or len(a) < 2:
        return False
    return a[-2] <= b[-2] and a[-1] > b[-1]


def cross_below(a: list[float], b: list[float]) -> bool:
    if len(a) != len(b) or len(a) < 2:
        return False
    return a[-2] >= b[-2] and a[-1] < b[-1]


def rsi(closes: list[float], n: int = 14) -> float | None:
    if len(closes) <= n:
        return None

    changes = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    gains = [max(change, 0.0) for change in changes]
    losses = [max(-change, 0.0) for change in changes]

    avg_gain = sum(gains[:n]) / n
    avg_loss = sum(losses[:n]) / n

    for i in range(n, len(gains)):
        avg_gain = (avg_gain * (n - 1) + gains[i]) / n
        avg_loss = (avg_loss * (n - 1) + losses[i]) / n

    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def _true_ranges(highs: list[float], lows: list[float], closes: list[float]) -> list[float]:
    true_ranges: list[float] = [highs[0] - lows[0]]
    for i in range(1, len(highs)):
        high_low = highs[i] - lows[i]
        high_close = abs(highs[i] - closes[i - 1])
        low_close = abs(lows[i] - closes[i - 1])
        true_ranges.append(max(high_low, high_close, low_close))
    return true_ranges


def atr(highs: list[float], lows: list[float], closes: list[float], n: int = 14) -> float | None:
    if len(highs) != len(lows) or len(highs) != len(closes) or len(highs) < n:
        return None

    true_ranges = _true_ranges(highs, lows, closes)
    atr_value = sum(true_ranges[:n]) / n
    for tr in true_ranges[n:]:
        atr_value = (atr_value * (n - 1) + tr) / n
    return atr_value


def rel_strength(values: list[float], benchmark: list[float], window: int) -> float | None:
    asset_change = pct_change(values, window)
    benchmark_change = pct_change(benchmark, window)
    if asset_change is None or benchmark_change is None:
        return None
    return asset_change - benchmark_change


def distance_from_high(values: list[float], lookback: int) -> float | None:
    if len(values) < lookback:
        return None
    window = values[-lookback:]
    high = max(window)
    if high == 0:
        return None
    return (values[-1] / high - 1) * 100


def volume_vs_avg(volumes: list[float], n: int) -> float | None:
    if len(volumes) <= n:
        return None
    prior = volumes[-1 - n : -1]
    avg = sum(prior) / n
    if avg == 0:
        return None
    return volumes[-1] / avg
