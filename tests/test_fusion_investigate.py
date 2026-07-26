from __future__ import annotations

import math
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from config.idea_engine import REASON_DAILY_BUDGET, REASON_OPENING_RESERVE_FRAC
from db import lane_stats
from db.base import connect
from db.fusion import init_fusion_tables
from signals.fusion.investigate import evaluate_investigation
from signals.fusion.scorer import CONVICTION_BAR, FUSION_Z_GATE_T

_ET = ZoneInfo("America/New_York")
TODAY = "2026-07-20"  # a Monday trading day
ARCH = ["options_actor", "structure"]


@pytest.fixture
def inv_db(tmp_path: Path) -> Path:
    path = tmp_path / "fusion.db"
    init_fusion_tables(path)
    return path


def _live(
    episode_id: str,
    market_day: str,
    score: float,
    groups: list[str],
    *,
    contested: bool = False,
    z: float = 5.0,
    conviction: float = 0.6,
) -> tuple[dict, dict]:
    # Defaults clear the dual gate (z 5.0 ≥ T, conviction 0.6 ≥ bar) so the
    # budget/reserve/sticky tests exercise their own lanes, not the cut-line.
    episode = {"id": episode_id, "symbol": "S" + episode_id, "market_day": market_day}
    state = {
        "score": score,
        "group_scores": {g: score for g in groups},
        "contested": contested,
        "direction_state": "contested" if contested else "aligned(bull)",
        "decayed_thesis": {"z": z, "conviction": conviction},
    }
    return episode, state


def _shadow_count(db: Path) -> int:
    with connect(db) as conn:
        return int(
            conn.execute(
                "SELECT COUNT(*) FROM fusion_investigations "
                "WHERE decision = 'shadow_investigate'"
            ).fetchone()[0]
        )


def _meter(db: Path) -> float:
    return lane_stats.day("fusion_investigate", TODAY, db_path=db).get(
        "fusion_would_investigate", 0.0
    )


def _inside_window() -> datetime:
    return datetime(2026, 7, 20, 9, 45, tzinfo=_ET)  # 09:45 ET → inside open window


def _after_window() -> datetime:
    return datetime(2026, 7, 20, 10, 15, tzinfo=_ET)  # 10:15 ET → full budget unlocked


def test_below_t_is_a_non_decision(inv_db: Path) -> None:
    # z just under the founder's T → not a candidate: no row, no meter.
    episode, state = _live("under", TODAY, 5.0, ARCH, z=FUSION_Z_GATE_T - 0.1)
    assert (
        evaluate_investigation(episode, state, db_path=inv_db, now=_after_window())
        is None
    )
    with connect(inv_db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM fusion_investigations").fetchone()[0] == 0
    assert _meter(inv_db) == 0.0


def test_high_z_low_conviction_cannot_pass(inv_db: Path) -> None:
    # The Grok catch the dual gate exists for: a weak lean on a huge ocean posts
    # big z (z ≈ L·√gross) but must NOT clear the gate — significance without
    # effect size (the spec's live QQQ check: z 6.2, conviction 0.25 → not armable).
    episode, state = _live("ocean", TODAY, 5.0, ARCH, z=6.2, conviction=CONVICTION_BAR - 0.15)
    assert (
        evaluate_investigation(episode, state, db_path=inv_db, now=_after_window())
        is None
    )
    with connect(inv_db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM fusion_investigations").fetchone()[0] == 0


def test_dual_gate_pass_records_z_and_t(inv_db: Path) -> None:
    # At exactly T with conviction at the bar the gate opens; the row records
    # score = the z compared, threshold = T (both on the baseline-table scale).
    episode, state = _live(
        "att", TODAY, 5.0, ARCH, z=FUSION_Z_GATE_T, conviction=CONVICTION_BAR
    )
    assert (
        evaluate_investigation(episode, state, db_path=inv_db, now=_after_window())
        == "shadow_investigate"
    )
    with connect(inv_db) as conn:
        row = conn.execute(
            "SELECT score, threshold FROM fusion_investigations WHERE episode_id = 'att'"
        ).fetchone()
    assert row["score"] == FUSION_Z_GATE_T
    assert row["threshold"] == FUSION_Z_GATE_T


def test_missing_decayed_thesis_gates_closed(inv_db: Path) -> None:
    # A legacy/degenerate state without the stashed thesis must gate CLOSED.
    episode, state = _live("bare", TODAY, 5.0, ARCH)
    del state["decayed_thesis"]
    assert (
        evaluate_investigation(episode, state, db_path=inv_db, now=_after_window())
        is None
    )


def test_archetype_filter_requires_options_and_structure(inv_db: Path) -> None:
    now = _after_window()

    options_only = _live("opts_only", TODAY, 5.0, ["options_actor"])
    assert evaluate_investigation(*options_only, db_path=inv_db, now=now) is None

    archetype = _live("arch", TODAY, 5.0, ARCH)
    assert (
        evaluate_investigation(*archetype, db_path=inv_db, now=now) == "shadow_investigate"
    )

    with connect(inv_db) as conn:
        ids = [
            r[0] for r in conn.execute("SELECT episode_id FROM fusion_investigations")
        ]
    assert ids == ["arch"]  # the options-only stack was never a candidate


def test_legacy_contested_bit_does_not_gate(inv_db: Path) -> None:
    """score_dots' contested-if-EVER bit must not veto a convicted decayed thesis.

    It fires on one contrary print anywhere in the session, so it was mutually
    exclusive with z ≥ T by construction: 691 archetype scorings cleared the dual
    gate on 07-22/23/24 and every one was vetoed here. The conviction leg below
    is the contested test now (Horizon Redesign §1)."""
    episode, state = _live("cont", TODAY, 5.0, ARCH, contested=True)
    assert (
        evaluate_investigation(episode, state, db_path=inv_db, now=_after_window())
        == "shadow_investigate"
    )


def test_decayed_conviction_is_what_rejects_a_contested_stack(inv_db: Path) -> None:
    """Same legacy bit set — but now the DECAYED read is the one that vetoes."""
    episode, state = _live(
        "cont_real", TODAY, 5.0, ARCH, contested=True, conviction=CONVICTION_BAR - 0.05
    )
    assert (
        evaluate_investigation(episode, state, db_path=inv_db, now=_after_window())
        is None
    )
    with connect(inv_db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM fusion_investigations").fetchone()[0] == 0


def test_opening_reserve_defers_third_then_upgrades(inv_db: Path) -> None:
    inside = _inside_window()

    d1 = evaluate_investigation(*_live("e1", TODAY, 5.0, ARCH), db_path=inv_db, now=inside)
    d2 = evaluate_investigation(*_live("e2", TODAY, 5.0, ARCH), db_path=inv_db, now=inside)
    episode3, state3 = _live("e3", TODAY, 5.0, ARCH)
    d3 = evaluate_investigation(episode3, state3, db_path=inv_db, now=inside)
    assert (d1, d2, d3) == (
        "shadow_investigate",
        "shadow_investigate",
        "reserve_deferred",
    )

    # After the window the deferred stack re-fires and upgrades in place.
    d3_after = evaluate_investigation(
        episode3, state3, db_path=inv_db, now=_after_window()
    )
    assert d3_after == "shadow_investigate"
    assert _shadow_count(inv_db) == 3
    assert _meter(inv_db) == 3.0

    with connect(inv_db) as conn:  # upgrade replaced the row, never added one
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM fusion_investigations WHERE episode_id = 'e3'"
            ).fetchone()[0]
            == 1
        )


def test_daily_budget_exhaustion_drops(inv_db: Path) -> None:
    after = _after_window()
    for k in range(REASON_DAILY_BUDGET):
        assert (
            evaluate_investigation(*_live(f"e{k}", TODAY, 5.0, ARCH), db_path=inv_db, now=after)
            == "shadow_investigate"
        )
    assert (
        evaluate_investigation(*_live("over", TODAY, 5.0, ARCH), db_path=inv_db, now=after)
        == "dropped_budget"
    )
    assert _shadow_count(inv_db) == REASON_DAILY_BUDGET
    assert _meter(inv_db) == float(REASON_DAILY_BUDGET)


def test_shadow_investigate_is_sticky_and_bumps_once(inv_db: Path) -> None:
    episode, state = _live("sticky", TODAY, 5.0, ARCH)
    after = _after_window()
    assert (
        evaluate_investigation(episode, state, db_path=inv_db, now=after)
        == "shadow_investigate"
    )
    for _ in range(4):  # re-fires stay terminal, never a second row or bump
        assert (
            evaluate_investigation(episode, state, db_path=inv_db, now=after)
            == "shadow_investigate"
        )
    assert _shadow_count(inv_db) == 1
    assert _meter(inv_db) == 1.0


def test_opening_reserve_never_exceeds_daily_budget() -> None:
    assert math.ceil(REASON_OPENING_RESERVE_FRAC * REASON_DAILY_BUDGET) <= REASON_DAILY_BUDGET
