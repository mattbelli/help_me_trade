"""Preset technical universe screener (read-only).

A SCAN screens a whole universe *right now* and returns the symbols that
currently match a preset's technical criteria — distinct from an ALERT/rule,
which watches the user's own symbols and fires on a crossing.

Data safety: ``run_scan`` never touches the broker itself. It takes an injected
``get_bars`` coroutine and calls it once per symbol over a BOUNDED universe. The
API layer wires that to the app's already-cached daily-bars path (the same
``_BARS_CACHE`` the chart endpoint + prewarm loop keep warm), so a scan can't
burst-request Schwab.

Every preset reuses the standard signal vocabulary from ``signals.indicators``
(RSI, SMA, 52-week range, relative volume, 1-day move) — the same technicals
``ai/perception/context`` surfaces. Presets are plain data, so adding one is a
single tuple entry.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any

from signals.indicators import (
    Bar,
    closes,
    distance_from_high,
    pct_change,
    rsi,
    sma,
    volume_vs_avg,
    volumes,
)

# Lookback windows (trading days). 252 ≈ one year; a 365-calendar-day bar pull
# yields ~250 trading days, enough for the 200-day SMA and 52-week range.
_YEAR_WINDOW = 252
_UNUSUAL_VOL = 1.5  # RVOL threshold for the unusual-volume preset
_NEAR_HIGH_PCT = 3.0  # within 3% of the 52-week high
_NEAR_LOW_PCT = 3.0  # within 3% of the 52-week low
_BIG_MOVE_PCT = 3.0  # |1-day % change| threshold for big movers

Metrics = dict[str, float]
GetBars = Callable[[str], Awaitable[list[Bar] | None]]


@dataclass(frozen=True)
class ScanPreset:
    """One named screen: a condition over metrics + a ranking rule.

    ``requires`` lists the metric keys the preset needs; symbols missing any of
    them are skipped before ``match``/``value``/``rank`` run, so those callables
    can assume the keys are present (no None juggling in the conditions).
    """

    id: str
    label: str
    description: str
    metric_label: str  # column header for the triggering value
    requires: tuple[str, ...]
    match: Callable[[Metrics], bool]
    value: Callable[[Metrics], float]  # the triggering metric to display
    rank: Callable[[Metrics], float]  # sort magnitude
    rank_desc: bool = True  # True → highest first


PRESETS: tuple[ScanPreset, ...] = (
    ScanPreset(
        id="oversold",
        label="Oversold",
        description="RSI(14) below 30",
        metric_label="RSI",
        requires=("rsi14",),
        match=lambda m: m["rsi14"] < 30,
        value=lambda m: m["rsi14"],
        rank=lambda m: m["rsi14"],
        rank_desc=False,
    ),
    ScanPreset(
        id="overbought",
        label="Overbought",
        description="RSI(14) above 70",
        metric_label="RSI",
        requires=("rsi14",),
        match=lambda m: m["rsi14"] > 70,
        value=lambda m: m["rsi14"],
        rank=lambda m: m["rsi14"],
        rank_desc=True,
    ),
    ScanPreset(
        id="unusual_volume",
        label="Unusual Volume",
        description=f"Volume ≥ {_UNUSUAL_VOL:g}× its 20-day average",
        metric_label="RVOL",
        requires=("rvol",),
        match=lambda m: m["rvol"] >= _UNUSUAL_VOL,
        value=lambda m: m["rvol"],
        rank=lambda m: m["rvol"],
        rank_desc=True,
    ),
    ScanPreset(
        id="below_200dma",
        label="Below 200-DMA",
        description="Price below its 200-day moving average",
        metric_label="vs 200-DMA",
        requires=("vs_sma200_pct",),
        match=lambda m: m["vs_sma200_pct"] < 0,
        value=lambda m: m["vs_sma200_pct"],
        rank=lambda m: m["vs_sma200_pct"],
        rank_desc=False,  # most-below first
    ),
    ScanPreset(
        id="above_200dma",
        label="Above 200-DMA",
        description="Price above its 200-day moving average",
        metric_label="vs 200-DMA",
        requires=("vs_sma200_pct",),
        match=lambda m: m["vs_sma200_pct"] > 0,
        value=lambda m: m["vs_sma200_pct"],
        rank=lambda m: m["vs_sma200_pct"],
        rank_desc=True,  # most-above first
    ),
    ScanPreset(
        id="near_52w_high",
        label="Near 52-Week High",
        description=f"Within {_NEAR_HIGH_PCT:g}% of the 52-week high",
        metric_label="% from high",
        requires=("dist_52w_high_pct",),
        match=lambda m: m["dist_52w_high_pct"] >= -_NEAR_HIGH_PCT,
        value=lambda m: m["dist_52w_high_pct"],
        rank=lambda m: m["dist_52w_high_pct"],
        rank_desc=True,  # closest to the high (nearest 0) first
    ),
    ScanPreset(
        id="near_52w_low",
        label="Near 52-Week Low",
        description=f"Within {_NEAR_LOW_PCT:g}% of the 52-week low",
        metric_label="% from low",
        requires=("dist_52w_low_pct",),
        match=lambda m: m["dist_52w_low_pct"] <= _NEAR_LOW_PCT,
        value=lambda m: m["dist_52w_low_pct"],
        rank=lambda m: m["dist_52w_low_pct"],
        rank_desc=False,  # closest to the low (nearest 0) first
    ),
    ScanPreset(
        id="big_movers",
        label="Big Movers",
        description=f"|1-day change| ≥ {_BIG_MOVE_PCT:g}%",
        metric_label="1d %",
        requires=("change_pct",),
        match=lambda m: abs(m["change_pct"]) >= _BIG_MOVE_PCT,
        value=lambda m: m["change_pct"],
        rank=lambda m: abs(m["change_pct"]),
        rank_desc=True,
    ),
)

_PRESETS_BY_ID: dict[str, ScanPreset] = {p.id: p for p in PRESETS}


def get_preset(preset_id: str) -> ScanPreset | None:
    return _PRESETS_BY_ID.get(preset_id)


def preset_summary(preset: ScanPreset) -> dict[str, str]:
    return {
        "id": preset.id,
        "label": preset.label,
        "description": preset.description,
        "metric_label": preset.metric_label,
    }


def _dist_from_low(values: list[float], lookback: int) -> float | None:
    """Percent above the lowest low over ``lookback`` bars (0 = at the low)."""
    if len(values) < lookback:
        return None
    low = min(values[-lookback:])
    if low == 0:
        return None
    return (values[-1] / low - 1) * 100


def _metrics(bars: list[Bar]) -> Metrics:
    """Compute the screener's technical metrics from daily bars.

    Only non-null metrics are included; ``run_scan`` skips a symbol for a preset
    whose required metric couldn't be computed (e.g. too little history for the
    200-day SMA).
    """
    c = closes(bars)
    if not c:
        return {}
    v = volumes(bars)
    price = c[-1]
    out: Metrics = {"price": round(price, 2)}

    change = pct_change(c, 1)
    if change is not None:
        out["change_pct"] = round(change, 2)

    rsi14 = rsi(c, 14)
    if rsi14 is not None:
        out["rsi14"] = round(rsi14, 1)

    sma200 = sma(c, 200)
    if sma200 is not None and sma200 != 0:
        out["sma200"] = round(sma200, 2)
        out["vs_sma200_pct"] = round((price / sma200 - 1) * 100, 2)

    dist_high = distance_from_high(c, _YEAR_WINDOW)
    if dist_high is not None:
        out["dist_52w_high_pct"] = round(dist_high, 2)

    dist_low = _dist_from_low(c, _YEAR_WINDOW)
    if dist_low is not None:
        out["dist_52w_low_pct"] = round(dist_low, 2)

    rvol = volume_vs_avg(v, 20)
    if rvol is not None:
        out["rvol"] = round(rvol, 2)

    return out


def _result_row(symbol: str, value: float, metrics: Metrics) -> dict[str, Any]:
    """A ranked match: the triggering value plus a compact stat block."""
    return {
        "symbol": symbol,
        "value": round(value, 2),
        "price": metrics.get("price"),
        "change_pct": metrics.get("change_pct"),
        "rsi14": metrics.get("rsi14"),
        "vs_sma200_pct": metrics.get("vs_sma200_pct"),
        "rvol": metrics.get("rvol"),
        "dist_52w_high_pct": metrics.get("dist_52w_high_pct"),
    }


async def run_scan(
    preset_id: str,
    universe: Sequence[str],
    get_bars: GetBars,
    *,
    limit: int = 50,
) -> dict[str, Any]:
    """Screen ``universe`` against ``preset_id`` and return ranked matches.

    ``get_bars(symbol)`` returns that symbol's daily bars (or None) — injected by
    the caller so the screener stays decoupled from the broker/cache layer. It is
    awaited once per symbol; the universe is bounded by the caller, so this is a
    bounded set of cache-first reads, never a live burst.
    """
    preset = get_preset(preset_id)
    if preset is None:
        raise ValueError(f"Unknown scan preset {preset_id!r}")

    matches: list[tuple[float, dict[str, Any]]] = []
    for symbol in universe:
        bars = await get_bars(symbol)
        if not bars:
            continue
        metrics = _metrics(bars)
        if any(key not in metrics for key in preset.requires):
            continue
        if not preset.match(metrics):
            continue
        matches.append((preset.rank(metrics), _result_row(symbol, preset.value(metrics), metrics)))

    matches.sort(key=lambda pair: pair[0], reverse=preset.rank_desc)
    results = [row for _, row in matches[:limit]]

    return {
        "preset_id": preset.id,
        "preset_label": preset.label,
        "metric_label": preset.metric_label,
        "universe_size": len(universe),
        "count": len(results),
        "results": results,
    }
