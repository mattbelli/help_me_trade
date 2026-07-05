"""Hermetic tests for setup scan idea validation and candidate resolution."""

from __future__ import annotations

from decimal import Decimal

import pytest

from ai.research.ideas import validate_ideas
from ai.research.setup_scan import CANDIDATE_CAP, resolve_scan_candidates


def _valid_raw(**overrides) -> dict:
    base = {
        "symbol": "AAPL",
        "direction": "long",
        "thesis": "Pullback to support with improving momentum.",
        "entry_zone": {"low": "210.50", "high": "213.00"},
        "stop": "204.00",
        "targets": ["221.00", "230.00"],
        "confidence": "medium",
        "confidence_reason": "Levels align with recent support.",
        "entry_condition": {"type": "price_in_zone", "low": "199.00", "high": "220.00"},
        "status": "watching",
        "risks": ["Macro headwinds"],
        "evidence": ["https://example.com/aapl"],
    }
    base.update(overrides)
    return base


@pytest.fixture
def last_prices() -> dict[str, Decimal]:
    return {"AAPL": Decimal("211.00")}


@pytest.fixture
def allowed() -> set[str]:
    return {"AAPL"}


def test_valid_long_idea_passes_and_is_normalized(last_prices, allowed):
    valid, rejected = validate_ideas(
        [_valid_raw()],
        last_prices,
        allowed_symbols=allowed,
    )
    assert rejected == []
    assert len(valid) == 1
    idea = valid[0]
    assert idea["direction"] == "long"
    assert idea["status"] == "candidate"
    assert idea["entry_condition"] == {
        "type": "price_in_zone",
        "low": "210.5",
        "high": "213",
    }
    assert idea["as_of"]
    assert isinstance(idea["entry_zone"]["low"], str)
    assert isinstance(idea["stop"], str)
    assert all(isinstance(t, str) for t in idea["targets"])


def test_short_direction_rejected(last_prices, allowed):
    _, rejected = validate_ideas(
        [_valid_raw(direction="short")],
        last_prices,
        allowed_symbols=allowed,
    )
    assert rejected[0]["reason"] == "only long setups supported"


def test_stop_at_or_above_entry_low_rejected(last_prices, allowed):
    _, rejected = validate_ideas(
        [_valid_raw(stop="210.50")],
        last_prices,
        allowed_symbols=allowed,
    )
    assert rejected[0]["reason"] == "stop must be below entry zone low"


def test_target_at_or_below_entry_high_rejected(last_prices, allowed):
    _, rejected = validate_ideas(
        [_valid_raw(targets=["212.00"])],
        last_prices,
        allowed_symbols=allowed,
    )
    assert rejected[0]["reason"] == "target must exceed entry zone high"


def test_non_ascending_targets_rejected(last_prices, allowed):
    _, rejected = validate_ideas(
        [_valid_raw(targets=["230.00", "221.00"])],
        last_prices,
        allowed_symbols=allowed,
    )
    assert rejected[0]["reason"] == "targets must be strictly ascending"


def test_entry_zone_30pct_away_from_last_price_rejected(allowed):
    last_prices = {"AAPL": Decimal("211.00")}
    _, rejected = validate_ideas(
        [_valid_raw(entry_zone={"low": "150.00", "high": "155.00"}, stop="140.00", targets=["170.00"])],
        last_prices,
        allowed_symbols=allowed,
    )
    assert "implausible vs last price" in rejected[0]["reason"]


def test_unknown_last_price_rejected(allowed):
    _, rejected = validate_ideas(
        [_valid_raw()],
        {},
        allowed_symbols=allowed,
    )
    assert rejected[0]["reason"] == "no last price for symbol"


def test_fabricated_probability_in_thesis_rejected(last_prices, allowed):
    for thesis in (
        "70% win rate on this breakout pattern.",
        "win rate of 70",
        "probability of success is 60%",
        "60% chance of a bounce",
    ):
        _, rejected = validate_ideas(
            [_valid_raw(thesis=thesis)],
            last_prices,
            allowed_symbols=allowed,
        )
        assert rejected[0]["reason"] == "fabricated probability", thesis


def test_benign_percentage_prose_passes(last_prices, allowed):
    # Regression: a bare \d+% check rejected legit theses ("1.0% above support")
    # and made idea survival depend on LLM wording (caught live 2026-07-04).
    for thesis in (
        "Price 1.0% above support with RSI at 53; bounce setup.",
        "A 2.4% pullback to the 731.57 zone offers a defined entry.",
        "Earnings increase the likelihood of volatility near resistance.",
        "The stock fell 13% last month but holds above SMA50.",
    ):
        valid, rejected = validate_ideas(
            [_valid_raw(thesis=thesis)],
            last_prices,
            allowed_symbols=allowed,
        )
        assert not rejected, (thesis, rejected)
        assert len(valid) == 1, thesis


def test_symbol_not_in_candidate_set_rejected(last_prices):
    _, rejected = validate_ideas(
        [_valid_raw(symbol="MSFT")],
        last_prices,
        allowed_symbols={"AAPL"},
    )
    assert rejected[0]["reason"] == "symbol not in scan candidates"


def test_duplicate_symbol_deduped(last_prices, allowed):
    valid, rejected = validate_ideas(
        [_valid_raw(), _valid_raw(thesis="Second duplicate thesis.")],
        last_prices,
        allowed_symbols=allowed,
    )
    assert len(valid) == 1
    assert len(rejected) == 1
    assert rejected[0]["reason"] == "duplicate symbol"


def test_prices_stay_strings_not_floats(last_prices, allowed):
    valid, _ = validate_ideas(
        [_valid_raw(entry_zone={"low": "210.50", "high": "213.00"})],
        last_prices,
        allowed_symbols=allowed,
    )
    idea = valid[0]
    assert idea["entry_zone"]["low"] == "210.5"
    assert idea["entry_zone"]["high"] == "213"
    assert idea["stop"] == "204"
    assert idea["targets"] == ["221", "230"]


def test_explicit_symbols_beat_watchlist():
    candidates, dropped = resolve_scan_candidates(
        ["aapl", " msft "],
        ["NVDA", "TSLA"],
    )
    assert candidates == ["AAPL", "MSFT"]
    assert dropped == []


def test_cap_at_12_records_dropped_list():
    symbols = [f"S{i}" for i in range(20)]
    candidates, dropped = resolve_scan_candidates(symbols, cap=CANDIDATE_CAP)
    assert len(candidates) == CANDIDATE_CAP
    assert len(dropped) == 8
    assert dropped[0] == "S12"


def test_empty_explicit_symbols_not_watchlist():
    candidates, dropped = resolve_scan_candidates([], ["NVDA", "TSLA"])
    assert candidates == []
    assert dropped == []


def test_none_symbols_uses_watchlist():
    candidates, dropped = resolve_scan_candidates(None, ["nvda", "aapl", "nvda"])
    assert candidates == ["NVDA", "AAPL"]
    assert dropped == []


def test_entry_state_ready_and_forming_pass(last_prices, allowed):
    for state in ("ready", "forming"):
        valid, rejected = validate_ideas(
            [_valid_raw(entry_state=state)],
            last_prices,
            allowed_symbols=allowed,
        )
        assert rejected == []
        assert valid[0]["entry_state"] == state


def test_entry_state_junk_rejected(last_prices, allowed):
    _, rejected = validate_ideas(
        [_valid_raw(entry_state="pending")],
        last_prices,
        allowed_symbols=allowed,
    )
    assert rejected[0]["reason"] == "invalid entry_state"


def test_structure_type_and_why_passthrough(last_prices, allowed):
    valid, rejected = validate_ideas(
        [
            _valid_raw(
                entry_state="ready",
                structure_type="pullback_to_support",
                why="price 1.5% above nearest support 1000.08; close > SMA50",
            )
        ],
        last_prices,
        allowed_symbols=allowed,
    )
    assert rejected == []
    assert valid[0]["structure_type"] == "pullback_to_support"
    assert "support" in valid[0]["why"]


def test_origin_stamped_server_side_default_structure(last_prices, allowed):
    valid, rejected = validate_ideas(
        [_valid_raw()],
        last_prices,
        allowed_symbols=allowed,
    )
    assert rejected == []
    assert valid[0]["origin"] == "structure"


def test_origin_stamped_server_side_llm_conviction(last_prices, allowed):
    valid, rejected = validate_ideas(
        [_valid_raw()],
        last_prices,
        allowed_symbols=allowed,
        origin="llm_conviction",
    )
    assert rejected == []
    assert valid[0]["origin"] == "llm_conviction"


def test_llm_provided_origin_is_overwritten(last_prices, allowed):
    valid, rejected = validate_ideas(
        [_valid_raw(origin="llm_conviction")],
        last_prices,
        allowed_symbols=allowed,
        origin="structure",
    )
    assert rejected == []
    assert valid[0]["origin"] == "structure"


def test_conviction_ideas_rejected_by_same_sanity_rules(last_prices, allowed):
    _, rejected = validate_ideas(
        [_valid_raw(stop="210.50")],
        last_prices,
        allowed_symbols=allowed,
        origin="llm_conviction",
    )
    assert rejected[0]["reason"] == "stop must be below entry zone low"

