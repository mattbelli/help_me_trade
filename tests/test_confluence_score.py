"""Importance score — honest unusualness math, pure and hermetic."""

from __future__ import annotations

import pytest

from signals.confluence_score import score_confluence


def _flow(**over) -> dict:
    base = {
        "premium_usd": 500_000.0,  # 2x base -> 1 point
        "direction": "buy_side",
        "exchanges": [],
    }
    base.update(over)
    return base


def _tape(**over) -> dict:
    base = {
        "notional_usd": 4_000_000.0,
        "direction": "buy_side",
        "detail": {"floor_usd": 1_000_000.0},  # 4x floor -> 2 points
    }
    base.update(over)
    return base


def test_size_points_are_log2_multiples() -> None:
    out = score_confluence(
        flow_event=_flow(direction="undetermined"),
        tape_event=None,
        rvol=None,
        level={"name": "VAH", "price": 100.0, "dist_atr": 0.5},
        interest=None,
    )
    # flow 2x base = 1.0; level at the 0.5 ATR edge = 0.0
    assert out["score"] == 1.0
    assert any("flow base" in d for d in out["drivers"])


def test_component_caps_hold() -> None:
    out = score_confluence(
        flow_event=_flow(premium_usd=250_000.0 * 2**10, direction="undetermined"),
        tape_event=None,
        rvol=None,
        level={"name": "POC", "price": 1.0, "dist_atr": 0.0},
        interest=None,
    )
    # size capped at 4 despite 1024x; level dead-on = 1
    assert out["score"] == 5.0


def test_breadth_agreement_and_interest() -> None:
    out = score_confluence(
        flow_event=_flow(),  # 1.0
        tape_event=_tape(),  # 2.0
        rvol=3.0,  # log2(3/1.5) = 1.0
        level={"name": "alert", "price": 100.0, "dist_atr": 0.25},  # 0.5 + 0.5 alert
        interest={"held": True, "alerted": True, "active_watchlist": True},
    )
    # + breadth (4 components -> 1.5) + sides agree (1.0) + held (1.0, max not sum)
    assert out["score"] == pytest.approx(8.5)
    assert "sides agree" in out["drivers"]
    assert "held" in out["drivers"]
    assert "4 components" in out["drivers"]
    assert "alerted" not in out["drivers"]  # max of interest tiers, not sum


def test_undetermined_sides_never_agree() -> None:
    out = score_confluence(
        flow_event=_flow(direction="undetermined"),
        tape_event=_tape(direction="undetermined"),
        rvol=None,
        level=None,
        interest=None,
    )
    assert "sides agree" not in out["drivers"]


def test_tape_floor_fallback_when_detail_missing() -> None:
    out = score_confluence(
        flow_event=None,
        tape_event=_tape(detail=None, notional_usd=2_000_000.0, direction="undetermined"),
        rvol=None,
        level={"name": "PDL", "price": 1.0, "dist_atr": 0.0},
        interest=None,
    )
    # $2M vs $1M fallback floor = 1 point + level 1 point
    assert out["score"] == 2.0


def test_extended_hours_flagged() -> None:
    out = score_confluence(
        flow_event=_flow(),
        tape_event=None,
        rvol=2.0,
        level=None,
        extended_hours=True,
    )
    assert "ext-hours" in out["drivers"]
