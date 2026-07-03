"""Tests for research job progress event capture."""

from __future__ import annotations

from ai.research.jobs import (
    _QUERY_TRUNCATE,
    MAX_JOB_EVENTS,
    _slim_progress_payload,
    append_progress_event,
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
