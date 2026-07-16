"""Tests for research job progress event capture."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from ai.research.jobs import (
    _QUERY_TRUNCATE,
    MAX_JOB_EVENTS,
    ResearchJob,
    _jobs,
    _slim_progress_payload,
    append_progress_event,
    list_jobs,
)


def test_slim_progress_payload_truncates_queries() -> None:
    long_query = "x" * 200
    slim = _slim_progress_payload(
        {
            "key": "technicals",
            "round": 2,
            "queries": [long_query, "short query"],
            "follow_up": True,
            "nested": {"ignored": True},
        }
    )
    assert slim["key"] == "technicals"
    assert slim["round"] == 2
    assert slim["follow_up"] is True
    assert len(slim["queries"][0]) == _QUERY_TRUNCATE
    assert slim["queries"][1] == "short query"
    assert "nested" not in slim


def test_append_progress_event_caps_at_max() -> None:
    events: list[dict] = []
    for i in range(MAX_JOB_EVENTS + 25):
        append_progress_event(events, "planning", {"index": i})
    assert len(events) == MAX_JOB_EVENTS
    assert events[0]["index"] == 25
    assert events[-1]["index"] == MAX_JOB_EVENTS + 24
    assert events[-1]["phase"] == "planning"
    assert "ts" in events[-1]


def _seed_job(
    *,
    job_id: str,
    status: str = "queued",
    created_at: str | None = None,
    artifact_id: int | None = None,
) -> ResearchJob:
    job = ResearchJob(
        id=job_id,
        topic=f"Topic {job_id}",
        symbol="AAPL",
        depth="standard",
        status=status,
        created_at=created_at or datetime.now(UTC).isoformat(),
        artifact_id=artifact_id,
    )
    _jobs[job_id] = job
    return job


def test_list_jobs_returns_slim_summaries_newest_first() -> None:
    _jobs.clear()
    older = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    newer = datetime.now(UTC).isoformat()
    _seed_job(job_id="old123", status="completed", created_at=older, artifact_id=42)
    _seed_job(job_id="new456", status="running", created_at=newer, artifact_id=None)

    summaries = list_jobs(limit=10)

    assert [row["job_id"] for row in summaries] == ["new456", "old123"]
    assert summaries[0]["status"] == "running"
    assert summaries[0]["artifact_id"] is None
    assert summaries[1]["artifact_id"] == 42


def test_job_summary_includes_artifact_id_when_set() -> None:
    _jobs.clear()
    _seed_job(job_id="art789", status="running", artifact_id=99)

    summaries = list_jobs()

    assert summaries[0]["artifact_id"] == 99


def test_api_list_research_jobs() -> None:
    from fastapi.testclient import TestClient

    from api.main import app

    _jobs.clear()
    newer = datetime.now(UTC).isoformat()
    older = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    _seed_job(job_id="api001", status="running", created_at=older)
    _seed_job(job_id="api002", status="completed", created_at=newer, artifact_id=7)

    with TestClient(app, base_url="http://127.0.0.1") as client:
        resp = client.get("/api/research/jobs?limit=5")

    assert resp.status_code == 200
    body = resp.json()
    assert len(body["jobs"]) == 2
    assert body["jobs"][0]["job_id"] == "api002"
    assert body["jobs"][1]["job_id"] == "api001"


def test_api_get_research_job_numeric_events_limit() -> None:
    from fastapi.testclient import TestClient

    from api.main import app

    _jobs.clear()
    job = _seed_job(job_id="evt111", status="running")
    for i in range(10):
        append_progress_event(job.events, "planning", {"index": i})

    with TestClient(app, base_url="http://127.0.0.1") as client:
        resp = client.get("/api/research/jobs/evt111?events=3")

    assert resp.status_code == 200
    body = resp.json()
    assert len(body["events"]) == 3
    assert body["events"][0]["index"] == 7
    assert body["artifact_id"] is None

