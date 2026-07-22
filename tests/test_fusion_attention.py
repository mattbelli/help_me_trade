"""Round-4 attention tiers + flow hot lane (FUSION_ATTENTION_TIERS_ROUND4_SPEC).

Pins the ladder contract: promote instantly on qualifying lifecycle state,
strengthen-never-weaken reason, demote only after quiet trading days
(hysteresis), watchlist always first in the merged flow universe, and
promoted_at (the per-symbol flow-baseline floor) never rewritten.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from db.base import connect
from db.fusion import init_fusion_tables
from db.idea_outcomes import init_idea_outcomes_tables, register_idea, update_outcome
from db.managed_positions import record_open_position
from signals.fusion.attention import (
    attention_tier,
    hot_lane_rows,
    merge_hot_lane,
    sweep_hot_lane,
)

_UTC = timezone.utc
NOW = datetime(2026, 7, 22, 15, 0, tzinfo=_UTC)  # Wed, trading day


@pytest.fixture
def db(tmp_path: Path) -> Path:
    path = tmp_path / "t.db"
    init_fusion_tables(path)
    init_idea_outcomes_tables(path)
    return path


def _mint_idea(db: Path, symbol: str, lifecycle_state: str) -> int:
    idea_id = register_idea(
        origin="reason_v1", signal_family="fusion", symbol=symbol,
        entry_zone={"low": "100", "high": "100"}, stop="95", targets=["110"],
        plan_horizon="9d", as_of="2026-07-21T14:00:00+00:00",
        episode_id=f"ep-{symbol}", lifecycle_state="born", db_path=db,
    )
    assert idea_id is not None
    if lifecycle_state != "born":
        assert update_outcome(idea_id, lifecycle_state=lifecycle_state, db_path=db)
    return idea_id


def test_attention_tier_derivation() -> None:
    assert attention_tier(hot_reason="position", thesis_gross=0.0, noise_floor=1.0) == "live"
    assert attention_tier(hot_reason=None, thesis_gross=1.5, noise_floor=1.0) == "poll"
    assert attention_tier(hot_reason=None, thesis_gross=0.2, noise_floor=1.0) == "scan"


def test_sweep_promotes_by_ladder_and_born_never_qualifies(db: Path) -> None:
    _mint_idea(db, "ARM", "armed")
    _mint_idea(db, "TRG", "trigger_fired")
    _mint_idea(db, "LIV", "live")
    _mint_idea(db, "BRN", "born")  # born = not yet armed — no promotion

    counters = sweep_hot_lane(NOW, db)
    assert counters["hot_promoted"] == 3
    rows = {r["symbol"]: r["reason"] for r in hot_lane_rows(db)}
    assert rows == {"LIV": "position", "TRG": "idea_triggered", "ARM": "idea_armed"}
    # Lane ordering: strongest reason first.
    assert [r["symbol"] for r in hot_lane_rows(db)] == ["LIV", "TRG", "ARM"]


def test_open_position_promotes_and_outranks_idea_reason(db: Path) -> None:
    _mint_idea(db, "AAA", "armed")
    record_open_position(
        account_id="acct", symbol="AAA", strategy_id="s1", qty="10",
        exit_mode="manual", params={}, db_path=db,
    )
    sweep_hot_lane(NOW, db)
    rows = {r["symbol"]: r["reason"] for r in hot_lane_rows(db)}
    assert rows["AAA"] == "position"  # strongest reason wins


def test_requalify_preserves_promoted_at_and_demotion_waits(db: Path) -> None:
    _mint_idea(db, "AAA", "armed")
    sweep_hot_lane(NOW, db)
    first = hot_lane_rows(db)[0]

    # Requalify later the same week: promoted_at (the per-symbol flow-baseline
    # floor) must NOT move; last_qualified_at must.
    later = datetime(2026, 7, 23, 15, 0, tzinfo=_UTC)
    counters = sweep_hot_lane(later, db)
    assert counters["hot_requalified"] == 1
    row = hot_lane_rows(db)[0]
    assert row["promoted_at"] == first["promoted_at"]
    assert row["last_qualified_at"] > first["last_qualified_at"]

    # Idea leaves the qualifying set → quiet, but within the hysteresis window
    # (3 trading days) the row STAYS...
    with connect(db) as conn:
        conn.execute("UPDATE idea_outcomes SET lifecycle_state = 'closed'")
        conn.commit()
    within = datetime(2026, 7, 27, 15, 0, tzinfo=_UTC)  # Mon: 2 trading days quiet
    counters = sweep_hot_lane(within, db)
    assert counters["hot_demoted"] == 0
    assert len(hot_lane_rows(db)) == 1

    # ...and past it the row demotes (Thu 07-30 = 5 trading days > 3).
    beyond = datetime(2026, 7, 30, 15, 0, tzinfo=_UTC)
    counters = sweep_hot_lane(beyond, db)
    assert counters["hot_demoted"] == 1
    assert hot_lane_rows(db) == []


def test_merge_hot_lane_watchlist_first_no_dupes_fail_soft(db: Path, tmp_path: Path) -> None:
    _mint_idea(db, "HOT1", "live")
    _mint_idea(db, "WL2", "armed")  # also on the watchlist — must not duplicate
    sweep_hot_lane(NOW, db)

    merged = merge_hot_lane(["WL1", "WL2"], db)
    assert merged == ["WL1", "WL2", "HOT1"]  # watchlist order untouched, lane appended

    # Fail-soft: unreadable DB serves the watchlist unchanged, never raises.
    bad = tmp_path / "nope" / "missing.db"
    assert merge_hot_lane(["WL1"], bad) == ["WL1"]


def test_reason_weakens_when_stronger_state_ends(db: Path) -> None:
    """Reason tracks CURRENT truth (Opus review 07-22): rank is the
    cap-eviction order, so when the position closes but an armed idea remains,
    the row must fall back to idea_armed — a stale 'position' rank would let
    an armed-grade name outrank genuinely live names forever."""
    _mint_idea(db, "AAA", "armed")
    record_open_position(
        account_id="acct", symbol="AAA", strategy_id="s1", qty="10",
        exit_mode="manual", params={}, db_path=db,
    )
    sweep_hot_lane(NOW, db)
    assert hot_lane_rows(db)[0]["reason"] == "position"

    with connect(db) as conn:
        conn.execute("UPDATE managed_positions SET status = 'closed'")
        conn.commit()
    later = datetime(2026, 7, 23, 15, 0, tzinfo=_UTC)
    sweep_hot_lane(later, db)
    row = hot_lane_rows(db)[0]
    assert row["reason"] == "idea_armed"  # weakened to truth, still a member


def test_cap_pressure_clips_hot_lane_tail_never_watchlist(db: Path, monkeypatch) -> None:
    """End-to-end ladder under OPTIONS_FLOW_MAX_UNDERLYINGS pressure: the
    merged universe feeds eligible_underlyings, which clips the TAIL — hot
    promotions are dropped (with the warning) before any watchlist name."""
    import signals.options_flow.scan as scan

    _mint_idea(db, "HOTA", "live")
    _mint_idea(db, "HOTB", "armed")
    sweep_hot_lane(NOW, db)

    merged = merge_hot_lane(["WL1", "WL2"], db)
    assert merged == ["WL1", "WL2", "HOTA", "HOTB"]

    monkeypatch.setattr(scan, "OPTIONS_FLOW_MAX_UNDERLYINGS", 3)
    capped = scan.eligible_underlyings(merged)
    assert capped == ["WL1", "WL2", "HOTA"]  # weakest promotion clipped first


def test_hot_lane_table_never_grows_unbounded_on_resweep(db: Path) -> None:
    _mint_idea(db, "AAA", "armed")
    for _ in range(3):
        sweep_hot_lane(NOW, db)
    conn = sqlite3.connect(db)
    try:
        n = conn.execute("SELECT count(*) FROM flow_hot_lane").fetchone()[0]
    finally:
        conn.close()
    assert n == 1
