"""Unit tests for signals.features — indicator facts builder."""

from __future__ import annotations

import pytest

from signals.features import build_indicator_facts
from signals.indicators import Bar, rsi, sma


def _bars_from_closes(
    close_series: list[float],
    *,
    volume: float = 1000.0,
) -> list[Bar]:
    return [
        {"o": c, "h": c + 1.0, "l": c - 1.0, "c": c, "v": volume}
        for c in close_series
    ]


def test_basic_facts_uptrend() -> None:
    close_series = [100.0 + i for i in range(55)]
    facts = build_indicator_facts({"NVDA": _bars_from_closes(close_series)})

    assert facts["NVDA_price"] == pytest.approx(154.0)
    assert facts["NVDA_pct_change_1d"] == pytest.approx((154.0 / 153.0 - 1) * 100)
    assert facts["NVDA_rsi_14"] == pytest.approx(rsi(close_series, 14))
    assert facts["NVDA_sma_50"] == pytest.approx(sma(close_series, 50))
    assert "NVDA_sma_200" not in facts
    assert facts["NVDA_cross_above_sma_200"] is False
    assert facts["NVDA_cross_below_sma_200"] is False


def test_cross_below_sma_200_on_last_bar() -> None:
    close_series = [100.0] * 200 + [105.0, 95.0]
    facts = build_indicator_facts({"NVDA": _bars_from_closes(close_series)})

    assert facts["NVDA_cross_below_sma_200"] is True
    assert facts["NVDA_cross_above_sma_200"] is False


def test_no_cross_sma_200() -> None:
    close_series = [100.0] * 202
    facts = build_indicator_facts({"NVDA": _bars_from_closes(close_series)})

    assert facts["NVDA_cross_below_sma_200"] is False
    assert facts["NVDA_cross_above_sma_200"] is False


def test_insufficient_data_omits_none_facts() -> None:
    close_series = [100.0 + i for i in range(10)]
    facts = build_indicator_facts({"NVDA": _bars_from_closes(close_series)})

    assert facts["NVDA_price"] == pytest.approx(109.0)
    assert facts["NVDA_pct_change_1d"] == pytest.approx((109.0 / 108.0 - 1) * 100)
    assert "NVDA_sma_200" not in facts
    assert "NVDA_rsi_14" not in facts
    assert facts["NVDA_cross_below_sma_200"] is False
    assert facts["NVDA_cross_above_sma_200"] is False


def test_rel_strength_vs_benchmark() -> None:
    asset_closes = [100.0 + (10.0 * i / 21) for i in range(22)]
    bench_closes = [100.0 + (5.0 * i / 21) for i in range(22)]
    facts = build_indicator_facts(
        {
            "NVDA": _bars_from_closes(asset_closes),
            "SPY": _bars_from_closes(bench_closes),
        },
        benchmark="SPY",
    )

    assert facts["NVDA_rel_strength_21"] == pytest.approx(5.0)
    assert "SPY_rel_strength_21" not in facts


def test_multi_symbol_prefixed_facts() -> None:
    facts = build_indicator_facts(
        {
            "NVDA": _bars_from_closes([100.0, 101.0, 102.0]),
            "AAPL": _bars_from_closes([200.0, 199.0, 198.0]),
        }
    )

    assert facts["NVDA_price"] == pytest.approx(102.0)
    assert facts["AAPL_price"] == pytest.approx(198.0)
    assert facts["NVDA_pct_change_1d"] == pytest.approx((102.0 / 101.0 - 1) * 100)
    assert facts["AAPL_pct_change_1d"] == pytest.approx((198.0 / 199.0 - 1) * 100)
    nvda_keys = {key for key in facts if key.startswith("NVDA_")}
    aapl_keys = {key for key in facts if key.startswith("AAPL_")}
    assert not nvda_keys & aapl_keys
