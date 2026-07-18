from __future__ import annotations

from datetime import date
from pathlib import Path

from db.base import connect as _connect
from db.idea_outcomes import (
    JOURNAL_ORIGINS,
    get_summary,
    grade_outcome,
    init_idea_outcomes_tables,
    list_recent_open,
    list_sweepable,
)
from research.idea_tracker import NEXT_SESSION_ORIGINS, _first_evaluable_date
from signals.fusion.registrar import register_stack


def _episode(**overrides) -> dict:
    base = {
        "id": "ep-TSLA-2026-07-16",
        "symbol": "TSLA",
        "market_day": "2026-07-16",
        "opened_at": "2026-07-16T14:00:00+00:00",
        "anchor_dot_id": "dot-1",
        "anchor_price": "100.00",
        "anchor_atr14": "2.50",
        "anchor_direction": "bull",
        "signature": "option_sweep,dark_pool",
        "market_shock": 0,
    }
    base.update(overrides)
    return base


_DOTS = [{"id": "dot-1", "ts": "2026-07-16T14:00:00+00:00"}]
_STATE = {"score": 3.2, "group_scores": {"options_actor": 3.2}, "contested": False}


def _rows(db: Path) -> list[dict]:
    init_idea_outcomes_tables(db)
    with _connect(db) as conn:
        return [
            dict(r)
            for r in conn.execute(
                "SELECT * FROM idea_outcomes WHERE origin = 'fusion_mech'"
            ).fetchall()
        ]


def test_registers_frozen_long_plan_with_cohort_stamp(tmp_path: Path) -> None:
    db = tmp_path / "ideas.db"
    register_stack(_episode(), _DOTS, _STATE, db_path=db)
    rows = _rows(db)
    assert len(rows) == 1
    row = rows[0]
    assert row["direction"] == "long"
    assert row["entry_low"] == "100.00" and row["entry_high"] == "100.00"
    assert row["stop"] == "97.50"
    assert row["targets_json"] == '["105.00"]'
    assert row["user_action"] == "skipped"
    assert row["as_of"] == "2026-07-16T14:00:00+00:00"
    assert '"score_at_registration": 3.2' in row["source_ref"]
    assert '"signature": "option_sweep,dark_pool"' in row["source_ref"]


def test_short_plan_inverts(tmp_path: Path) -> None:
    db = tmp_path / "ideas.db"
    register_stack(_episode(anchor_direction="bear"), _DOTS, _STATE, db_path=db)
    row = _rows(db)[0]
    assert row["direction"] == "short"
    assert row["stop"] == "102.50"
    assert row["targets_json"] == '["95.00"]'


def test_unstampable_episode_registers_nothing(tmp_path: Path) -> None:
    db = tmp_path / "ideas.db"
    register_stack(_episode(anchor_atr14=None), _DOTS, _STATE, db_path=db)
    register_stack(_episode(anchor_direction=None), _DOTS, _STATE, db_path=db)
    assert _rows(db) == []


def test_same_symbol_day_registers_once(tmp_path: Path) -> None:
    db = tmp_path / "ideas.db"
    register_stack(_episode(), _DOTS, _STATE, db_path=db)
    register_stack(_episode(), _DOTS, _STATE, db_path=db)  # restart replay
    assert len(_rows(db)) == 1


def test_journal_lane_excluded_from_headline_but_graded(tmp_path: Path) -> None:
    db = tmp_path / "ideas.db"
    register_stack(_episode(), _DOTS, _STATE, db_path=db)
    row = _rows(db)[0]
    assert "fusion_mech" in JOURNAL_ORIGINS
    # Headline summary counts exclude the journal row; by_origin still sees it.
    summary = get_summary(db_path=db)
    assert summary["counts"] == {} or "pending" not in summary["counts"]
    assert "fusion_mech" in summary["by_origin"]
    # Never in the founder-facing recent list.
    assert list_recent_open(db_path=db) == []
    # Still fully sweepable — journal rows GRADE mechanically like any other.
    assert [r["id"] for r in list_sweepable(db_path=db)] == [row["id"]]
    # Never founder-gradeable — even once RESOLVED (a gradeable status), so
    # it's the origin clause that refuses, not the pending-status branch.
    with _connect(db) as conn:
        conn.execute(
            "UPDATE idea_outcomes SET status = 'won' WHERE id = ?", (row["id"],)
        )
        conn.commit()
    assert grade_outcome(int(row["id"]), {"grade": "good"}, db_path=db) == "not_gradeable"


def test_fusion_mech_grades_from_next_session() -> None:
    assert "fusion_mech" in NEXT_SESSION_ORIGINS
    # Mid-session registration (Thu 2026-07-16 14:00 UTC = 10:00 ET) — a normal
    # origin grades same-day; fusion_mech must start the NEXT session (Fri).
    ts = "2026-07-16T14:00:00+00:00"
    assert _first_evaluable_date(ts) == date(2026, 7, 16)
    assert _first_evaluable_date(ts, origin="fusion_mech") == date(2026, 7, 17)
    # Friday registration rolls to Monday.
    assert _first_evaluable_date(
        "2026-07-17T14:00:00+00:00", origin="fusion_mech"
    ) == date(2026, 7, 20)
