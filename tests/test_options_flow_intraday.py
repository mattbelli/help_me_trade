"""Hermetic tests for intraday detection primitives (F-1/F-2/BLK-1/DIR-1)."""

from __future__ import annotations

from typing import Any

import pytest

from signals.options_flow import constants as C
from signals.options_flow.intraday import (
    aggregate_directional_pressure,
    classify_direction,
    classify_midpoint,
    detect_block,
    is_hygiene_excluded,
    is_multileg,
    normalize_print,
    tag_likely_spreads,
)

_FORBIDDEN_KEYS = frozenset({"win_prob", "ev", "confidence"})


def _meta(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "occ_symbol": "NVDA260821C00190000",
        "underlying": "NVDA",
        "right": "call",
        "strike": 190.0,
        "expiry": "2026-08-21",
        "dte": 43,
    }
    base.update(overrides)
    return base


def _raw(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "t": "2026-07-09T14:30:00.123Z",
        "p": 4.5,
        "s": 400,
        "x": "C",
        "c": ["I"],
    }
    base.update(overrides)
    return base


def _print(**overrides: Any) -> dict[str, Any]:
    """Normalized print that clears block thresholds (400 × 4.5 × 100 = 180_000)."""
    base: dict[str, Any] = {
        "occ_symbol": "NVDA260821C00190000",
        "underlying": "NVDA",
        "right": "call",
        "strike": 190.0,
        "expiry": "2026-08-21",
        "dte": 43,
        "ts_ms": 1_752_073_800_123,
        "price": 4.5,
        "size": 400,
        "exchange": "C",
        "conditions": ["I"],
    }
    base.update(overrides)
    return base


def _assert_no_forbidden_keys(ev: dict) -> None:
    assert _FORBIDDEN_KEYS.isdisjoint(ev.keys())


# --- normalize_print -------------------------------------------------------------


def test_normalize_print_happy_path() -> None:
    out = normalize_print(_raw(), meta=_meta())
    assert out is not None
    assert out["occ_symbol"] == "NVDA260821C00190000"
    assert out["underlying"] == "NVDA"
    assert out["right"] == "call"
    assert out["strike"] == 190.0
    assert out["expiry"] == "2026-08-21"
    assert out["dte"] == 43
    assert out["price"] == 4.5
    assert out["size"] == 400
    assert out["exchange"] == "C"
    assert out["conditions"] == ["I"]
    assert isinstance(out["ts_ms"], int)
    assert out["ts_ms"] > 0


def test_normalize_print_nanosecond_timestamp() -> None:
    out = normalize_print(
        _raw(t="2026-07-09T14:30:00.123456789Z"),
        meta=_meta(),
    )
    assert out is not None
    # Truncated to 6 fractional digits — must not crash
    assert isinstance(out["ts_ms"], int)


def test_normalize_print_garbage_returns_none() -> None:
    assert normalize_print(_raw(p=0), meta=_meta()) is None
    assert normalize_print(_raw(s=0), meta=_meta()) is None
    assert normalize_print(_raw(p=-1.0), meta=_meta()) is None
    assert normalize_print(_raw(t=""), meta=_meta()) is None
    assert normalize_print(_raw(p="bad"), meta=_meta()) is None
    assert normalize_print({}, meta=_meta()) is None


# --- F-1 hygiene -----------------------------------------------------------------


@pytest.mark.parametrize("code", sorted(C.CANCEL_CONDITIONS))
def test_hygiene_cancel_conditions(code: str) -> None:
    assert is_hygiene_excluded([code]) is True


@pytest.mark.parametrize("code", sorted(C.LATE_OOS_CONDITIONS))
def test_hygiene_late_oos_conditions(code: str) -> None:
    assert is_hygiene_excluded([code]) is True


def test_hygiene_ext_hours() -> None:
    assert is_hygiene_excluded(["v"]) is True


def test_hygiene_clean_print_passes() -> None:
    assert is_hygiene_excluded(["I", "S"]) is False


# --- F-2 multileg + spread clustering --------------------------------------------


def test_is_multileg() -> None:
    assert is_multileg(["f"]) is True
    assert is_multileg(["I"]) is False
    assert is_multileg(["g", "I"]) is True


def test_tag_likely_spreads_multileg_sets_spread_leg() -> None:
    prints = [_print(conditions=["f"])]
    tag_likely_spreads(prints)
    assert prints[0]["spread_leg"] is True
    assert "likely_spread" not in prints[0]


def test_tag_likely_spreads_two_strikes_within_window() -> None:
    prints = [
        _print(strike=190.0, size=100, ts_ms=1_000_000),
        _print(
            occ_symbol="NVDA260821C00200000",
            strike=200.0,
            size=100,
            ts_ms=1_000_500,
        ),
    ]
    tag_likely_spreads(prints)
    assert prints[0]["likely_spread"] is True
    assert prints[1]["likely_spread"] is True


def test_tag_likely_spreads_same_strikes_apart_not_flagged() -> None:
    prints = [
        _print(strike=190.0, size=100, ts_ms=1_000_000),
        _print(strike=190.0, size=100, ts_ms=1_002_000),
    ]
    tag_likely_spreads(prints)
    assert "likely_spread" not in prints[0]
    assert "likely_spread" not in prints[1]


def test_tag_likely_spreads_single_strike_not_flagged() -> None:
    prints = [_print(strike=190.0, size=100, ts_ms=1_000_000)]
    tag_likely_spreads(prints)
    assert "likely_spread" not in prints[0]


def test_tag_likely_spreads_mismatched_sizes_not_flagged() -> None:
    prints = [
        _print(strike=190.0, size=100, ts_ms=1_000_000),
        _print(
            occ_symbol="NVDA260821C00200000",
            strike=200.0,
            size=200,
            ts_ms=1_000_500,
        ),
    ]
    tag_likely_spreads(prints)
    assert "likely_spread" not in prints[0]
    assert "likely_spread" not in prints[1]


# --- DIR-1 classify_direction ----------------------------------------------------


def test_classify_direction_at_ask() -> None:
    assert classify_direction(price=5.0, bid=4.0, ask=5.0) == "buy_side"


def test_classify_direction_above_ask() -> None:
    assert classify_direction(price=5.5, bid=4.0, ask=5.0) == "buy_side"


def test_classify_direction_at_bid() -> None:
    assert classify_direction(price=4.0, bid=4.0, ask=5.0) == "sell_side"


def test_classify_direction_midpoint_undetermined() -> None:
    assert classify_direction(price=4.5, bid=4.0, ask=5.0) == "undetermined"


def test_classify_direction_missing_quote() -> None:
    assert classify_direction(price=4.5, bid=None, ask=5.0) == "undetermined"
    assert classify_direction(price=4.5, bid=4.0, ask=None) == "undetermined"


def test_classify_direction_crossed_quote() -> None:
    assert classify_direction(price=4.5, bid=5.0, ask=4.0) == "undetermined"


def test_classify_direction_zero_quote() -> None:
    assert classify_direction(price=4.5, bid=0.0, ask=5.0) == "undetermined"


def test_classify_direction_eps_boundary() -> None:
    # ask=5.00, eps=0.01 → buy at 4.99
    assert classify_direction(price=4.99, bid=4.0, ask=5.0, eps=0.01) == "buy_side"
    assert classify_direction(price=4.98, bid=4.0, ask=5.0, eps=0.01) == "undetermined"
    # bid=4.00, eps=0.01 → sell at 4.01
    assert classify_direction(price=4.01, bid=4.0, ask=5.0, eps=0.01) == "sell_side"
    assert classify_direction(price=4.02, bid=4.0, ask=5.0, eps=0.01) == "undetermined"


# --- corpus midpoint annotation (classify_midpoint) -------------------------------


def test_classify_midpoint_sides() -> None:
    assert classify_midpoint(price=4.6, bid=4.0, ask=5.0) == "buy"
    assert classify_midpoint(price=4.4, bid=4.0, ask=5.0) == "sell"
    assert classify_midpoint(price=4.5, bid=4.0, ask=5.0) == "unclassified"


def test_classify_midpoint_wide_spread_fence() -> None:
    # spread 2.0 > 0.5 * mid 3.0 → fenced, exactly like the corpus v7 rule
    assert classify_midpoint(price=3.5, bid=2.0, ask=4.0) == "wide_spread"
    # spread exactly at the fence is NOT wide (corpus uses strict >)
    assert classify_midpoint(price=4.5, bid=3.0, ask=5.0) == "buy"


def test_classify_midpoint_bad_quotes() -> None:
    assert classify_midpoint(price=4.5, bid=None, ask=5.0) == "unclassified"
    assert classify_midpoint(price=4.5, bid=4.0, ask=None) == "unclassified"
    assert classify_midpoint(price=4.5, bid=0.0, ask=5.0) == "unclassified"
    assert classify_midpoint(price=4.5, bid=5.0, ask=4.0) == "unclassified"
    assert classify_midpoint(price=4.5, bid=4.5, ask=4.5) == "unclassified"


def test_classify_midpoint_parity_with_corpus_fence() -> None:
    """The scalar mirror may NEVER drift from the corpus fence_and_classify.

    Runs both implementations over the same cases (dollars scaled to raw DBN
    fixed-point for the corpus side) and asserts identical labels, plus that
    the local fence constant still equals the corpus one.
    """
    np = pytest.importorskip("numpy")
    from signals.options_flow.intraday import MID_SPREAD_MAX_FRAC
    from tools.flow_corpus_rollup import PX, SPREAD_MAX_FRAC, UNDEF, fence_and_classify

    assert MID_SPREAD_MAX_FRAC == SPREAD_MAX_FRAC

    corpus_label = {0: "buy", 1: "sell", 2: "unclassified", 3: "wide_spread"}
    cases = [
        (4.6, 4.0, 5.0),    # above mid
        (4.4, 4.0, 5.0),    # below mid
        (4.5, 4.0, 5.0),    # at mid
        (3.5, 2.0, 4.0),    # wide fence
        (4.5, 3.0, 5.0),    # spread exactly at the fence boundary (strict >)
        (4.5, 5.0, 4.0),    # crossed
        (4.5, 0.0, 5.0),    # zero bid
        (4.5, None, 5.0),   # missing bid → UNDEF on the corpus side
        (0.05, 0.01, 0.10), # deep-penny wide quote
    ]
    dt = np.dtype(
        [("price", "i8"), ("size", "u4"), ("bid_px_00", "i8"), ("ask_px_00", "i8")]
    )
    arr = np.zeros(len(cases), dtype=dt)
    for i, (price, bid, ask) in enumerate(cases):
        arr[i]["price"] = int(round(price * PX))
        arr[i]["size"] = 1
        arr[i]["bid_px_00"] = UNDEF if bid is None else int(round(bid * PX))
        arr[i]["ask_px_00"] = UNDEF if ask is None else int(round(ask * PX))
    fenced = fence_and_classify(arr)
    for i, (price, bid, ask) in enumerate(cases):
        assert classify_midpoint(price=price, bid=bid, ask=ask) == corpus_label[
            int(fenced.side[i])
        ], f"case {i}: {(price, bid, ask)}"


# --- BLK-1 detect_block ----------------------------------------------------------


def test_detect_block_fires_at_thresholds() -> None:
    ev = detect_block(_print(), event_date="2026-07-09", direction="buy_side", direction_basis="polled_quote")
    assert ev is not None
    assert ev["event_type"] == "block"
    assert ev["contracts"] == 400
    assert ev["premium_usd"] == 180_000.0
    assert ev["direction"] == "buy_side"
    assert ev["direction_basis"] == "polled_quote"
    assert ev["n_prints"] == 1
    assert ev["exchanges"] == ["C"]
    assert ev["vol_oi_ratio"] is None
    assert ev["vol_zscore"] is None
    assert "block" in ev["reason"]
    assert "buy_side" in ev["reason"]
    assert "Savickas-Wilson" in ev["caveat"]
    _assert_no_forbidden_keys(ev)


def test_detect_block_carries_mid_side() -> None:
    p = _print()
    p["mid_side"] = "buy"
    ev = detect_block(p, event_date="2026-07-09", direction="undetermined")
    assert ev is not None
    assert ev["mid_side"] == "buy"
    # un-stamped prints (e.g. older paths) yield None, never a guessed side
    ev2 = detect_block(_print(), event_date="2026-07-09")
    assert ev2 is not None
    assert ev2["mid_side"] is None


def test_detect_block_rejects_below_size() -> None:
    assert detect_block(_print(size=249), event_date="2026-07-09") is None


def test_detect_block_rejects_below_premium() -> None:
    # 250 × 0.50 × 100 = 12_500 < 100_000
    assert detect_block(_print(size=250, price=0.50), event_date="2026-07-09") is None


def test_detect_block_rejects_spread_leg() -> None:
    assert detect_block(_print(spread_leg=True), event_date="2026-07-09") is None
    assert detect_block(_print(likely_spread=True), event_date="2026-07-09") is None


def test_detect_block_rejects_hygiene() -> None:
    assert detect_block(_print(conditions=["A"]), event_date="2026-07-09") is None


def test_detect_block_rejects_zero_dte_index() -> None:
    assert (
        detect_block(
            _print(
                underlying="SPY",
                occ_symbol="SPY260709C00500000",
                dte=1,
                size=500,
                price=5.0,
            ),
            event_date="2026-07-09",
        )
        is None
    )


def test_detect_block_id_unique_same_contract_day() -> None:
    a = detect_block(_print(ts_ms=1_000_000), event_date="2026-07-09")
    b = detect_block(_print(ts_ms=2_000_000), event_date="2026-07-09")
    assert a is not None and b is not None
    assert a["id"] != b["id"]
    assert a["id"].endswith("_blk_1000000")
    assert b["id"].endswith("_blk_2000000")


def test_detect_block_undetermined_caveat_no_accuracy_citation() -> None:
    ev = detect_block(_print(), event_date="2026-07-09")
    assert ev is not None
    assert "Savickas-Wilson" not in ev["caveat"]
    assert "undetermined" in ev["caveat"]


def test_detect_block_premium_math_exact() -> None:
    ev = detect_block(_print(size=300, price=4.0), event_date="2026-07-09")
    assert ev is not None
    assert ev["premium_usd"] == 120_000.0


# --- DIR-1 directional_pressure --------------------------------------------------


def _pressure_print(**overrides: Any) -> dict[str, Any]:
    """Print with direction set; default 300 contracts @ $10 = $300k premium."""
    base = _print(size=300, price=10.0, direction="buy_side")
    base.update(overrides)
    return base


def test_directional_pressure_net_buy() -> None:
    prints = [
        _pressure_print(direction="buy_side", right="call", size=300, price=10.0),
        _pressure_print(
            occ_symbol="NVDA260821P00180000",
            right="put",
            direction="buy_side",
            size=100,
            price=5.0,
        ),
    ]
    ev = aggregate_directional_pressure(
        underlying="NVDA",
        prints=prints,
        event_date="2026-07-09",
        direction_basis="polled_quote",
    )
    assert ev is not None
    assert ev["event_type"] == "directional_pressure"
    assert ev["direction"] == "buy_side"
    assert ev["premium_usd"] == 350_000.0  # 300k + 50k
    assert ev["id"] == "of_2026-07-09_NVDA_pressure"
    assert ev["occ_symbol"] == ""
    assert ev["strike"] is None
    assert ev["exchanges"] == []
    assert "buy-side" in ev["reason"]
    assert "calls" in ev["reason"]
    assert "puts" in ev["reason"]
    assert "Savickas-Wilson" in ev["caveat"]
    _assert_no_forbidden_keys(ev)


def test_directional_pressure_net_sell() -> None:
    prints = [
        _pressure_print(direction="sell_side", size=500, price=6.0),
    ]
    ev = aggregate_directional_pressure(
        underlying="NVDA",
        prints=prints,
        event_date="2026-07-09",
        direction_basis="polled_quote",
    )
    assert ev is not None
    assert ev["direction"] == "sell_side"
    assert ev["premium_usd"] == 300_000.0


def test_directional_pressure_rejects_under_min_usd() -> None:
    prints = [
        _pressure_print(direction="buy_side", size=100, price=10.0),  # 100k
    ]
    assert (
        aggregate_directional_pressure(
            underlying="NVDA",
            prints=prints,
            event_date="2026-07-09",
            direction_basis="polled_quote",
        )
        is None
    )


def test_directional_pressure_rejects_low_classified_fraction() -> None:
    prints = [
        _pressure_print(direction="buy_side", size=300, price=10.0),  # 300k classified
        _print(size=500, price=10.0, direction="undetermined"),  # 500k unclassified
    ]
    assert (
        aggregate_directional_pressure(
            underlying="NVDA",
            prints=prints,
            event_date="2026-07-09",
            direction_basis="polled_quote",
        )
        is None
    )


def test_directional_pressure_excludes_spread_prints() -> None:
    prints = [
        _pressure_print(direction="buy_side", size=300, price=10.0),
        _pressure_print(direction="buy_side", size=300, price=10.0, spread_leg=True),
        _pressure_print(direction="buy_side", size=300, price=10.0, likely_spread=True),
    ]
    ev = aggregate_directional_pressure(
        underlying="NVDA",
        prints=prints,
        event_date="2026-07-09",
        direction_basis="polled_quote",
    )
    assert ev is not None
    assert ev["premium_usd"] == 300_000.0
    assert ev["n_prints"] == 1


def test_directional_pressure_stable_id() -> None:
    prints_a = [_pressure_print(direction="buy_side", size=300, price=10.0)]
    prints_b = [
        _pressure_print(direction="buy_side", size=300, price=10.0),
        _pressure_print(direction="sell_side", size=50, price=10.0),
    ]
    ev_a = aggregate_directional_pressure(
        underlying="NVDA", prints=prints_a, event_date="2026-07-09", direction_basis="polled_quote"
    )
    ev_b = aggregate_directional_pressure(
        underlying="NVDA", prints=prints_b, event_date="2026-07-09", direction_basis="polled_quote"
    )
    assert ev_a is not None and ev_b is not None
    assert ev_a["id"] == ev_b["id"] == "of_2026-07-09_NVDA_pressure"


def test_directional_pressure_excludes_hygiene() -> None:
    prints = [
        _pressure_print(direction="buy_side", size=300, price=10.0),
        _pressure_print(direction="buy_side", size=300, price=10.0, conditions=["A"]),
    ]
    ev = aggregate_directional_pressure(
        underlying="NVDA",
        prints=prints,
        event_date="2026-07-09",
        direction_basis="polled_quote",
    )
    assert ev is not None
    assert ev["premium_usd"] == 300_000.0
