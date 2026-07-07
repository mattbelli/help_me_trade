"""5b founder judging — grade submit, re-grade, summary math + gate, auth.

The API routes use the frozen DEFAULT_DB_PATH default arg (conftest forces this
to a throwaway temp DB, never production). Seeds + assertions therefore go
through that same default DB — the fixture clears `idea_outcomes` per test for
isolation.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from db.base import DEFAULT_DB_PATH, connect
from db.idea_outcomes import (
    GATE_MIN_GRADED,
    get_summary,
    grade_outcome,
    init_idea_outcomes_tables,
    register_ideas,
    update_outcome,
)


def _seed_idea(
    *,
    artifact_id: int,
    idx: int = 0,
    symbol: str = "AAPL",
    status: str = "won",
    origin: str = "structure",
) -> int:
    """Register one idea, force it to a resolved status; return its row id."""
    register_ideas(
        artifact_id,
        [
            {
                "symbol": symbol,
                "direction": "long",
                "entry_zone": {"low": "100", "high": "105"},
                "stop": "95",
                "targets": ["110"],
                "origin": origin,
            }
        ],
    )
    with connect(DEFAULT_DB_PATH) as conn:
        row = conn.execute(
            "SELECT id FROM idea_outcomes WHERE artifact_id = ? AND idea_index = ?",
            (artifact_id, idx),
        ).fetchone()
    outcome_id = int(row["id"])
    update_outcome(outcome_id, status=status)
    return outcome_id


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch):
    """TestClient with a cleared idea table and auth ON (founder token)."""
    init_idea_outcomes_tables(DEFAULT_DB_PATH)
    with connect(DEFAULT_DB_PATH) as conn:
        conn.execute("DELETE FROM idea_outcomes")
        conn.commit()
    token = "founder-secret"
    monkeypatch.setattr("api.auth.API_TOKEN", token)

    from api.main import app

    with TestClient(app, base_url="http://127.0.0.1") as c:
        c.headers.update({"X-API-Token": token})
        yield c


_GOOD_GRADE = {
    "take_it": 4,
    "entry_honest": 5,
    "stop_survivable": 4,
    "thesis_quality": 3,
    "note": "clean setup",
}


def test_grade_resolved_idea_persists(client) -> None:
    oid = _seed_idea(artifact_id=1, status="won")

    resp = client.post(f"/api/ideas/outcomes/{oid}/grade", json=_GOOD_GRADE)
    assert resp.status_code == 200
    verdict = resp.json()["verdict"]
    assert verdict["take_it"] == 4
    assert verdict["note"] == "clean setup"
    assert "graded_at" in verdict

    outcomes = client.get("/api/ideas/outcomes").json()
    graded_row = next(r for r in outcomes["resolved"] if r["id"] == oid)
    assert graded_row["verdict"]["entry_honest"] == 5


def test_grade_non_resolved_rejected_400(client) -> None:
    for status in ("pending", "triggered", "invalidated", "data_gap"):
        oid = _seed_idea(artifact_id=10, status=status, symbol="MSFT")
        resp = client.post(f"/api/ideas/outcomes/{oid}/grade", json=_GOOD_GRADE)
        assert resp.status_code == 400, status
        with connect(DEFAULT_DB_PATH) as conn:
            conn.execute("DELETE FROM idea_outcomes WHERE id = ?", (oid,))
            conn.commit()


def test_grade_vetoed_lane_rejected(client) -> None:
    oid = _seed_idea(artifact_id=20, status="won", origin="structure_vetoed")
    resp = client.post(f"/api/ideas/outcomes/{oid}/grade", json=_GOOD_GRADE)
    assert resp.status_code == 400


def test_grade_missing_row_404(client) -> None:
    resp = client.post("/api/ideas/outcomes/999999/grade", json=_GOOD_GRADE)
    assert resp.status_code == 404


def test_grade_invalid_score_400(client) -> None:
    oid = _seed_idea(artifact_id=30, status="won")
    resp = client.post(
        f"/api/ideas/outcomes/{oid}/grade", json={**_GOOD_GRADE, "take_it": 9}
    )
    assert resp.status_code == 400
    resp2 = client.post(
        f"/api/ideas/outcomes/{oid}/grade",
        json={"take_it": 3, "entry_honest": 3, "stop_survivable": 3},
    )
    assert resp2.status_code == 400


def test_regrade_overwrites(client) -> None:
    oid = _seed_idea(artifact_id=40, status="lost")
    client.post(f"/api/ideas/outcomes/{oid}/grade", json=_GOOD_GRADE)
    client.post(
        f"/api/ideas/outcomes/{oid}/grade",
        json={
            "take_it": 2,
            "entry_honest": 2,
            "stop_survivable": 2,
            "thesis_quality": 2,
        },
    )
    grading = get_summary()["grading"]
    assert grading["graded"] == 1  # not double-counted
    assert grading["mean_take_it"] == 2.0


def test_summary_math_and_gate(client) -> None:
    for i in range(GATE_MIN_GRADED):
        oid = _seed_idea(artifact_id=100 + i, status="won")
        client.post(f"/api/ideas/outcomes/{oid}/grade", json=_GOOD_GRADE)
    grading = get_summary()["grading"]
    assert grading["graded"] == GATE_MIN_GRADED
    assert grading["mean_take_it"] == 4.0
    assert grading["numbers_wrong"] == 0
    assert grading["gate_pass"] is True


def test_gate_fails_below_threshold(client) -> None:
    oid = _seed_idea(artifact_id=200, status="won")
    client.post(f"/api/ideas/outcomes/{oid}/grade", json=_GOOD_GRADE)
    grading = get_summary()["grading"]
    assert grading["graded"] == 1
    assert grading["gate_pass"] is False


def test_gate_fails_on_numbers_wrong(client) -> None:
    for i in range(GATE_MIN_GRADED):
        oid = _seed_idea(artifact_id=300 + i, status="won")
        grade = dict(_GOOD_GRADE)
        if i == 0:
            grade["entry_honest"] = 1  # one numbers-wrong fail
        client.post(f"/api/ideas/outcomes/{oid}/grade", json=grade)
    grading = get_summary()["grading"]
    assert grading["graded"] == GATE_MIN_GRADED
    assert grading["numbers_wrong"] == 1
    assert grading["gate_pass"] is False


def test_vetoed_grade_never_counts_in_gate(client) -> None:
    # A vetoed row cannot be graded via the API; even a directly-written verdict
    # on the journal lane must stay out of the founder-facing gate.
    oid = _seed_idea(artifact_id=400, status="won", origin="structure_vetoed")
    assert (
        grade_outcome(
            oid,
            {
                "take_it": 5,
                "entry_honest": 5,
                "stop_survivable": 5,
                "thesis_quality": 5,
            },
        )
        == "not_gradeable"
    )
    assert get_summary()["grading"]["graded"] == 0


def test_grade_requires_auth(client) -> None:
    oid = _seed_idea(artifact_id=500, status="won")

    # Wrong token -> 401 (overrides the fixture's valid default header).
    resp = client.post(
        f"/api/ideas/outcomes/{oid}/grade",
        json=_GOOD_GRADE,
        headers={"X-API-Token": "not-the-founder"},
    )
    assert resp.status_code == 401

    # No token header at all -> 401.
    del client.headers["X-API-Token"]
    resp2 = client.post(f"/api/ideas/outcomes/{oid}/grade", json=_GOOD_GRADE)
    assert resp2.status_code == 401
