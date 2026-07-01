"""Unit tests for signals.screener — preset universe screener.

Covers:
- _metrics(): the technical snapshot computed from daily bars.
- Each preset's match set + ranking over seeded bars.
- The data-safety contract: run_scan reads bars ONLY through the injected
  ``get_bars`` callable, exactly once per (bounded) universe symbol — it never
  fans out into a live per-symbol broker burst.
"""

from __future__ import annotations

import pytest

from signals.indicators import Bar
from signals.screener import (
    PRESETS,
    _metrics,
    get_preset,
    preset_summary,
    run_scan,
)


def _bars(close_list: list[float], volumes: list[float] | None = None) -> list[Bar]:
    vols = volumes if volumes is not None else [100.0] * len(close_list)
    return [
        {"o": c, "h": c, "l": c, "c": c, "v": v}
        for c, v in zip(close_list, vols, strict=True)
    ]


# Monotonic year-long series (≥252 bars → 200-DMA + 52-week range defined).
_UP = _bars([100.0 + 0.5 * i for i in range(260)])  # rising: RSI≈100, near 52w high
_DOWN = _bars([230.0 - 0.5 * i for i in range(260)])  # falling: RSI≈0, near 52w low


def _recording_getter(mapping: dict[str, list[Bar]]):
    """A fake bars source that records every symbol it was asked for."""
    calls: list[str] = []

    async def _get(symbol: str) -> list[Bar] | None:
        calls.append(symbol)
        return mapping.get(symbol)

    return _get, calls


# ---------------------------------------------------------------------------
# _metrics — the technical vocabulary
# ---------------------------------------------------------------------------


def test_metrics_uptrend() -> None:
    m = _metrics(_UP)
    assert m["rsi14"] == pytest.approx(100.0)
    assert m["vs_sma200_pct"] > 0
    assert m["dist_52w_high_pct"] == pytest.approx(0.0)  # last bar is the high
    assert m["price"] == pytest.approx(229.5)


def test_metrics_downtrend() -> None:
    m = _metrics(_DOWN)
    assert m["rsi14"] == pytest.approx(0.0)
    assert m["vs_sma200_pct"] < 0
    assert m["dist_52w_low_pct"] == pytest.approx(0.0)  # last bar is the low


def test_metrics_relative_volume() -> None:
    bars = _bars([100.0] * 40, volumes=[100.0] * 39 + [300.0])
    assert _metrics(bars)["rvol"] == pytest.approx(3.0)


def test_metrics_skips_missing_when_short() -> None:
    m = _metrics(_bars([100.0] * 30))  # too short for SMA200 / 52-week range
    assert "vs_sma200_pct" not in m
    assert "dist_52w_high_pct" not in m
    assert "rvol" in m  # 20-day avg volume still computable


def test_metrics_omits_rvol_when_volume_absent() -> None:
    # Store-backed (S&P 500) bars keep only open/close → volume is None; the
    # scan must skip the volume metric (never fabricate it), not crash.
    bars: list[Bar] = [
        {"o": 100.0, "h": 100.0, "l": 100.0, "c": 100.0, "v": None}  # type: ignore[typeddict-item]
        for _ in range(40)
    ]
    m = _metrics(bars)
    assert "rvol" not in m
    assert "price" in m  # price/RSI-style metrics still computed from close


# ---------------------------------------------------------------------------
# preset registry
# ---------------------------------------------------------------------------


def test_presets_registered_and_summaries() -> None:
    assert PRESETS
    for preset in PRESETS:
        assert get_preset(preset.id) is preset
        summary = preset_summary(preset)
        assert set(summary) == {"id", "label", "description", "metric_label"}
    assert get_preset("does_not_exist") is None


# ---------------------------------------------------------------------------
# preset match sets + ranking
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_oversold_and_overbought() -> None:
    get_bars, _ = _recording_getter({"UP": _UP, "DOWN": _DOWN})

    oversold = await run_scan("oversold", ["UP", "DOWN"], get_bars)
    assert [r["symbol"] for r in oversold["results"]] == ["DOWN"]

    overbought = await run_scan("overbought", ["UP", "DOWN"], get_bars)
    assert [r["symbol"] for r in overbought["results"]] == ["UP"]


@pytest.mark.asyncio
async def test_200dma_presets() -> None:
    get_bars, _ = _recording_getter({"UP": _UP, "DOWN": _DOWN})

    below = await run_scan("below_200dma", ["UP", "DOWN"], get_bars)
    assert [r["symbol"] for r in below["results"]] == ["DOWN"]

    above = await run_scan("above_200dma", ["UP", "DOWN"], get_bars)
    assert [r["symbol"] for r in above["results"]] == ["UP"]


@pytest.mark.asyncio
async def test_unusual_volume_ranked_descending() -> None:
    mapping = {
        "SPIKE3": _bars([100.0] * 40, volumes=[100.0] * 39 + [300.0]),
        "SPIKE2": _bars([100.0] * 40, volumes=[100.0] * 39 + [200.0]),
        "QUIET": _bars([100.0] * 40, volumes=[100.0] * 40),  # rvol 1.0 → no match
    }
    get_bars, _ = _recording_getter(mapping)

    result = await run_scan("unusual_volume", list(mapping), get_bars)
    assert result["count"] == 2
    assert [r["symbol"] for r in result["results"]] == ["SPIKE3", "SPIKE2"]
    assert result["results"][0]["value"] == pytest.approx(3.0)


@pytest.mark.asyncio
async def test_big_movers_ranked_by_absolute_move() -> None:
    mapping = {
        "MOVER_UP": _bars([100.0] * 40 + [108.0]),  # +8%
        "MOVER_DN": _bars([100.0] * 40 + [95.0]),  # -5%
        "SMALL": _bars([100.0] * 40 + [101.0]),  # +1% → no match
    }
    get_bars, _ = _recording_getter(mapping)

    result = await run_scan("big_movers", list(mapping), get_bars)
    assert [r["symbol"] for r in result["results"]] == ["MOVER_UP", "MOVER_DN"]
    assert result["results"][0]["value"] == pytest.approx(8.0)


@pytest.mark.asyncio
async def test_near_52w_high_and_low() -> None:
    get_bars, _ = _recording_getter({"UP": _UP, "DOWN": _DOWN})

    high = await run_scan("near_52w_high", ["UP", "DOWN"], get_bars)
    assert [r["symbol"] for r in high["results"]] == ["UP"]

    low = await run_scan("near_52w_low", ["UP", "DOWN"], get_bars)
    assert [r["symbol"] for r in low["results"]] == ["DOWN"]


@pytest.mark.asyncio
async def test_limit_truncates_results() -> None:
    mapping = {
        f"S{i}": _bars([100.0] * 40, volumes=[100.0] * 39 + [200.0 + i])
        for i in range(5)
    }
    get_bars, _ = _recording_getter(mapping)

    result = await run_scan("unusual_volume", list(mapping), get_bars, limit=2)
    assert result["count"] == 2


# ---------------------------------------------------------------------------
# data-safety contract
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reads_only_via_injected_getter_once_per_symbol() -> None:
    """The scan must fetch each symbol exactly once through the injected getter.

    This is the anti-burst guarantee: run_scan owns no broker client and issues
    no per-symbol live requests — it consumes only the (cache-backed) callable the
    caller hands in, once per bounded-universe symbol.
    """
    universe = ["UP", "DOWN", "SPIKE"]
    mapping = {
        "UP": _UP,
        "DOWN": _DOWN,
        "SPIKE": _bars([100.0] * 40, volumes=[100.0] * 39 + [300.0]),
    }
    get_bars, calls = _recording_getter(mapping)

    await run_scan("overbought", universe, get_bars)

    assert calls == universe  # one fetch per symbol, in order, no re-fetch


@pytest.mark.asyncio
async def test_missing_symbol_bars_skipped() -> None:
    get_bars, calls = _recording_getter({})  # getter returns None for everything
    result = await run_scan("oversold", ["NOPE"], get_bars)
    assert result["count"] == 0
    assert calls == ["NOPE"]  # still asked once, then skipped on None


@pytest.mark.asyncio
async def test_unknown_preset_raises() -> None:
    get_bars, _ = _recording_getter({})
    with pytest.raises(ValueError):
        await run_scan("bogus", ["UP"], get_bars)
