"""Hermetic tests for deterministic setup structure detection."""

from __future__ import annotations

from ai.research.structures import detect_structures


def _pullback_base(**overrides) -> dict:
    base = {
        "latest_close": 1005.0,
        "sma50": 990.0,
        "atr14": 15.0,
        "levels": {
            "support_prices": [1000.08, 950.0],
            "resistance_prices": [1095.69, 1124.55],
            "dist_to_support_pct": 0.49,
            "dist_to_resistance_pct": 9.0,
        },
    }
    base.update(overrides)
    if "levels" in overrides:
        base["levels"] = {**_pullback_base()["levels"], **overrides["levels"]}
    return base


def _breakout_base(**overrides) -> dict:
    base = {
        "latest_close": 98.0,
        "sma50": 90.0,
        "atr14": 2.0,
        "levels": {
            "support_prices": [92.0, 85.0],
            "resistance_prices": [100.0, 115.0, 125.0],
            "dist_to_resistance_pct": 2.04,
        },
    }
    base.update(overrides)
    if "levels" in overrides:
        base["levels"] = {**_breakout_base()["levels"], **overrides["levels"]}
    return base


def _only_pullback(content: dict) -> dict:
    structures = detect_structures(content)
    pullbacks = [s for s in structures if s["type"] == "pullback_to_support"]
    assert len(pullbacks) == 1
    return pullbacks[0]


def _only_breakout(content: dict) -> dict:
    structures = detect_structures(content)
    breakouts = [s for s in structures if s["type"] == "breakout_above_resistance"]
    assert len(breakouts) == 1
    return breakouts[0]


def test_pullback_ready_close_inside_zone():
    structure = _only_pullback(_pullback_base())
    assert structure["entry_state"] == "ready"
    assert structure["entry_zone"]["low"] == "1000.08"
    assert structure["entry_zone"]["high"] == "1010.08"


def test_pullback_ready_edge_within_one_pct_above_zone():
    content = _pullback_base(latest_close=1020.0)
    structure = _only_pullback(content)
    assert structure["entry_state"] == "ready"


def test_pullback_forming_three_pct_above_zone():
    content = _pullback_base(latest_close=1040.0)
    structure = _only_pullback(content)
    assert structure["entry_state"] == "forming"


def test_pullback_none_more_than_five_pct_above_zone():
    content = _pullback_base(latest_close=1070.0)
    assert detect_structures(content) == []


def test_pullback_none_close_below_support():
    content = _pullback_base(latest_close=995.0)
    assert detect_structures(content) == []


def test_pullback_none_close_below_sma50():
    content = _pullback_base(latest_close=1005.0, sma50=1010.0)
    assert detect_structures(content) == []


def test_pullback_none_rr_below_floor():
    content = _pullback_base(
        levels={"resistance_prices": [1025.0]},
    )
    assert detect_structures(content) == []


def test_pullback_stop_uses_atr():
    content = _pullback_base(atr14=15.0)
    structure = _only_pullback(content)
    assert structure["stop"] == "988.83"


def test_pullback_stop_fallback_without_atr():
    content = _pullback_base(atr14=None)
    structure = _only_pullback(content)
    assert structure["stop"] == "985.08"


def test_breakout_forming_two_pct_below_resistance():
    structure = _only_breakout(_breakout_base())
    assert structure["entry_state"] == "forming"
    assert float(structure["entry_zone"]["low"]) > 98.0
    assert structure["targets"] == ["115.00", "125.00"]


def test_breakout_none_more_than_three_pct_below():
    content = _breakout_base(
        latest_close=96.0,
        levels={
            "support_prices": [80.0],
            "resistance_prices": [100.0, 115.0],
        },
    )
    assert detect_structures(content) == []


def test_breakout_entry_zone_above_close():
    structure = _only_breakout(_breakout_base())
    assert float(structure["entry_zone"]["low"]) > 98.0
    assert float(structure["entry_zone"]["high"]) > 98.0


def test_breakout_stop_from_near_support():
    structure = _only_breakout(_breakout_base())
    assert structure["stop"] == "92.00"


def test_breakout_stop_from_atr_when_support_too_far():
    content = _breakout_base(
        levels={"support_prices": [80.0]},
    )
    structure = _only_breakout(content)
    assert structure["stop"] == "95.00"


def test_breakout_skipped_without_targets_above_zone():
    content = _breakout_base(
        levels={"resistance_prices": [100.0]},
    )
    assert [s for s in detect_structures(content) if s["type"] == "breakout_above_resistance"] == []


def test_breakout_skipped_without_stop_source():
    content = _breakout_base(
        atr14=None,
        levels={"support_prices": [80.0], "resistance_prices": [100.0, 115.0]},
    )
    assert [s for s in detect_structures(content) if s["type"] == "breakout_above_resistance"] == []


def test_determinism_same_input_same_output():
    content = _pullback_base()
    first = detect_structures(content)
    second = detect_structures(content)
    assert first == second


def test_fail_soft_empty_content():
    assert detect_structures({}) == []


def test_fail_soft_missing_levels():
    assert detect_structures({"latest_close": 100.0, "sma50": 90.0}) == []
