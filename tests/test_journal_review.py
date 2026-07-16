"""Decision Journal J3 — calibration / learning review (surface-only).

Grades the agent's OWN predictions: predicted win% (score_json.win_prob at propose) vs the
REALIZED outcome, per strategy + per predicted-win% bucket, with Wilson CIs and an honest
``insufficient_data`` floor. Defends the calibration math, the reflection hook's lesson
seeding + caps, and the /calibration route — plus the honest no-op on an empty journal.

Surface-only: the review reads decision_journal and writes ONLY lesson memories; it never
touches a strategy/scorer. These tests assert that boundary (only MemoryService.add is called).
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from ai.journal_review import (
    MIN_SAMPLES,
    build_calibration_lessons,
    compute_calibration,
    run_journal_review,
)
from db import base as db_base
from db.decision_journal import (
    init_decision_journal_tables,
    record_decision,
    resolve_outcome,
    set_decision,
    set_entry_fill,
)

# --- Synthetic rows (compute_calibration is pure given rows) ------------------------------


def _row(
    strategy: str,
    win_prob: float,
    outcome: str,
    *,
    r: float | None = None,
) -> dict[str, Any]:
    """A journal row in the shape list_recent returns (score already parsed)."""
    return {
        "strategy_id": strategy,
        "outcome": outcome,
        "r_multiple": r,
        "score": {"win_prob": win_prob},
    }


def _grade_rows(strategy: str, win_prob: float, wins: int, losses: int) -> list[dict]:
    return [_row(strategy, win_prob, "win", r=1.5) for _ in range(wins)] + [
        _row(strategy, win_prob, "loss", r=-1.0) for _ in range(losses)
    ]


# --- Calibration math --------------------------------------------------------------------


def test_overconfident_group() -> None:
    # Predicted 70%, realized 6/15 = 40% → predicted sits above the Wilson CI → overconfident.
    rows = _grade_rows("dip_buy", 0.70, wins=6, losses=9)
    cal = compute_calibration(rows)

    assert cal["n_resolved"] == 15
    assert len(cal["by_strategy"]) == 1
    g = cal["by_strategy"][0]
    assert g["key"] == "dip_buy"
    assert g["n"] == 15
    assert g["wins"] == 6
    assert g["losses"] == 9
    assert g["predicted_win"] == 0.70
    assert g["realized_win"] == 0.40
    assert g["drift"] == round(0.40 - 0.70, 4)
    assert g["ci_low"] < g["realized_win"] < g["ci_high"]
    assert g["predicted_win"] > g["ci_high"]
    assert g["verdict"] == "overconfident"
    assert g["avg_r_multiple"] is not None
    # 0.70 predicted lands in the 60–80% bucket.
    assert [b["label"] for b in cal["by_bucket"]] == ["60–80%"]


def test_well_calibrated_group() -> None:
    # Predicted 60%, realized 9/15 = 60% → predicted inside the CI → well_calibrated.
    cal = compute_calibration(_grade_rows("swing", 0.60, wins=9, losses=6))
    g = cal["by_strategy"][0]
    assert g["realized_win"] == 0.60
    assert g["drift"] == 0.0
    assert g["verdict"] == "well_calibrated"


def test_insufficient_data_below_min_samples() -> None:
    # Only 5 resolved → below MIN_SAMPLES → honest insufficient_data, never a win rate verdict.
    cal = compute_calibration(_grade_rows("thin", 0.65, wins=2, losses=3))
    g = cal["by_strategy"][0]
    assert g["n"] == 5 < MIN_SAMPLES
    assert g["verdict"] == "insufficient_data"


def test_unresolved_rows_excluded() -> None:
    # open / never_filled / closed_incomplete have no realized outcome → excluded entirely.
    rows = _grade_rows("dip_buy", 0.70, wins=6, losses=9) + [
        _row("dip_buy", 0.70, "open"),
        _row("dip_buy", 0.70, "never_filled"),
        _row("dip_buy", 0.70, "closed_incomplete"),
    ]
    cal = compute_calibration(rows)
    assert cal["n_resolved"] == 15
    assert cal["by_strategy"][0]["n"] == 15


def test_missing_win_prob_excluded() -> None:
    # A resolved trade with no predicted win% can't be calibrated → dropped, not fabricated.
    rows = _grade_rows("dip_buy", 0.70, wins=6, losses=9) + [
        {"strategy_id": "dip_buy", "outcome": "win", "score": None},
    ]
    assert compute_calibration(rows)["n_resolved"] == 15


def test_empty_is_honest_noop() -> None:
    cal = compute_calibration([])
    assert cal == {
        "n_resolved": 0,
        "min_samples": MIN_SAMPLES,
        "by_strategy": [],
        "by_bucket": [],
    }


# --- Lesson synthesis + caps -------------------------------------------------------------


def test_lessons_seeded_only_on_notable_drift() -> None:
    over = compute_calibration(_grade_rows("dip_buy", 0.70, wins=6, losses=9))
    lessons = build_calibration_lessons(over)
    assert len(lessons) == 1
    text = lessons[0]
    assert "dip_buy" in text
    assert "overconfident" in text
    assert "predicted ~70%" in text
    assert "realized 40%" in text

    well = compute_calibration(_grade_rows("swing", 0.60, wins=9, losses=6))
    assert build_calibration_lessons(well) == []

    thin = compute_calibration(_grade_rows("thin", 0.65, wins=2, losses=3))
    assert build_calibration_lessons(thin) == []


def test_lessons_capped_and_ranked_by_drift() -> None:
    rows = (
        _grade_rows("a_small_miss", 0.70, wins=8, losses=7)  # drift ~ -0.17
        + _grade_rows("b_big_miss", 0.90, wins=2, losses=13)  # drift ~ -0.77
        + _grade_rows("c_mid_miss", 0.80, wins=4, losses=11)  # drift ~ -0.53
        + _grade_rows("d_extra", 0.85, wins=3, losses=12)  # drift ~ -0.65
    )
    lessons = build_calibration_lessons(compute_calibration(rows), max_lessons=2)
    assert len(lessons) == 2
    # Largest drift first.
    assert lessons[0].startswith("b_big_miss")
    assert lessons[1].startswith("d_extra")


# --- DB-backed: reflection hook + route --------------------------------------------------


def _seed_resolved(
    db: Path, *, strategy: str, win_prob: float, outcome: str
) -> None:
    """Drive one journal row through the real CONTEXT→DECISION→OUTCOME write path."""
    proposal_id = f"prop_{uuid.uuid4().hex[:8]}"
    record_decision(
        proposal_id=proposal_id,
        symbol="NVDA",
        side="buy",
        qty="10",
        order_type="limit",
        source=f"strategy:{strategy}",
        strategy_id=strategy,
        entry_ref_price="100",
        thesis="calibration seed",
        score_json=f'{{"win_prob": {win_prob}}}',
        plan_json='{"stop_pct": -3.0}',
        db_path=db,
    )
    set_decision(proposal_id, decision="approved", decided_by="phone", db_path=db)
    set_entry_fill(
        proposal_id,
        entry_execution_id="ex_entry",
        entry_fill_price="100",
        qty_filled="10",
        db_path=db,
    )
    exit_price = "110" if outcome == "win" else "95"
    resolve_outcome(
        proposal_id,
        exit_execution_id="ex_exit",
        exit_fill_price=exit_price,
        close_reason="target" if outcome == "win" else "stop",
        entry_time="2026-06-01T14:30:00+00:00",
        close_time="2026-06-03T14:30:00+00:00",
        db_path=db,
    )


@pytest.fixture
def journal_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    db = tmp_path / "journal.db"
    monkeypatch.setattr(db_base, "DEFAULT_DB_PATH", db)
    init_decision_journal_tables(db)
    return db


def _seed_grade(db: Path, strategy: str, win_prob: float, wins: int, losses: int) -> None:
    for _ in range(wins):
        _seed_resolved(db, strategy=strategy, win_prob=win_prob, outcome="win")
    for _ in range(losses):
        _seed_resolved(db, strategy=strategy, win_prob=win_prob, outcome="loss")


def test_reflection_hook_seeds_lesson_on_drift(journal_db: Path) -> None:
    import db.ai_tables as ai_tables

    _seed_grade(journal_db, "dip_buy", 0.70, wins=6, losses=9)
    result = run_journal_review(db_path=journal_db)

    assert result["n_resolved"] == 15
    assert result["lessons_seeded"] == 1
    # Judge-gated write (§4.3): the one lesson lands as a lesson memory in the store.
    lessons = ai_tables.list_memories(memory_type="lesson", db_path=journal_db)
    assert len(lessons) == 1
    assert lessons[0]["source"] == "reflection"
    assert lessons[0]["metadata"] == {"source": "journal_calibration"}
    assert "dip_buy" in lessons[0]["content"]


def test_reflection_hook_silent_on_insufficient(journal_db: Path) -> None:
    import db.ai_tables as ai_tables

    _seed_grade(journal_db, "thin", 0.65, wins=2, losses=3)
    result = run_journal_review(db_path=journal_db)
    assert result["lessons_seeded"] == 0
    assert ai_tables.list_memories(memory_type="lesson", db_path=journal_db) == []


def test_reflection_hook_noop_on_empty_journal(journal_db: Path) -> None:
    import db.ai_tables as ai_tables

    result = run_journal_review(db_path=journal_db)
    assert result["n_resolved"] == 0
    assert result["lessons_seeded"] == 0
    assert ai_tables.list_memories(memory_type="lesson", db_path=journal_db) == []


# --- Route (mounted on a fresh app; api.main untouched) ----------------------------------


@pytest.fixture
def journal_client(journal_db: Path) -> TestClient:
    from api.journal_routes import router

    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_calibration_route(journal_db: Path, journal_client: TestClient) -> None:
    _seed_grade(journal_db, "dip_buy", 0.70, wins=6, losses=9)
    resp = journal_client.get("/api/journal/calibration")
    assert resp.status_code == 200
    body = resp.json()
    assert body["n_resolved"] == 15
    assert body["min_samples"] == MIN_SAMPLES
    strat = {g["key"]: g for g in body["by_strategy"]}
    assert strat["dip_buy"]["verdict"] == "overconfident"
    assert [b["label"] for b in body["by_bucket"]] == ["60–80%"]


def test_calibration_route_empty(journal_client: TestClient) -> None:
    resp = journal_client.get("/api/journal/calibration")
    assert resp.status_code == 200
    body = resp.json()
    assert body["n_resolved"] == 0
    assert body["by_strategy"] == []
    assert body["by_bucket"] == []
