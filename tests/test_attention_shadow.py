"""Jev shadow (db/attention_shadow.py) + its wiring in the attention investigator.

Offline: the investigator's LLM and the Jev seam are both faked at their module
boundary, and every DB write lands in tmp_path. The point of these tests is the two
properties that matter — the shadow is idempotent and gradable, and it can never
change what the delivery lane does.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from ai.llm import system_one as so
from db import attention_shadow as shadow
from research import attention_investigator as investigator


def _fake_response() -> so.SystemOneResponse:
    return so.SystemOneResponse(
        model="jev-1.13.0",
        answers={
            "activity_unusual": so.NoulAnswer(noul=0.81),
            "explained_by_supplied_fact": so.NoulAnswer(noul=0.2),
            "materiality": so.ScoreAnswer(
                score=1.8,
                probabilities={"0": 0.0, "1": 0.2, "2": 0.8},
                confidence=0.8,
                legend={"0": "routine", "1": "notable", "2": "worth a look"},
            ),
            "evidence_thin": so.NoulAnswer(noul=0.1),
            "driver": so.ChoiceAnswer(
                choice="options_flow", probabilities={"options_flow": 0.9}, confidence=0.9
            ),
            "next_check": so.ChoiceAnswer(
                choice="news", probabilities={"news": 0.7}, confidence=0.7
            ),
        },
        usage={"input_tokens": 500, "output_tokens": 70},
        latency_ms=420,
    )


# --- The table -----------------------------------------------------------------


def test_record_and_get_round_trip(tmp_path: Path):
    db = tmp_path / "t.db"
    shadow.record(
        "abcd",
        "2026-09-15",
        status="ok",
        model="jev-1.13.0",
        latency_ms=420,
        input_tokens=500,
        output_tokens=70,
        prompt_chars=1052,
        text_model_ok=True,
        answers={
            "activity_unusual": 0.81,
            "materiality": 1.8,
            "materiality_norm": 0.9,
            "driver": "options_flow",
            "driver_confidence": 0.9,
            "evidence_thin": 0.1,
            "next_check": "news",
        },
        db_path=db,
    )

    row = shadow.get("ABCD", "2026-09-15", db_path=db)
    assert row is not None
    assert row["symbol"] == "ABCD"  # normalized to upper
    assert row["status"] == "ok"
    assert row["model"] == "jev-1.13.0"
    assert row["input_tokens"] == 500
    assert row["text_model_ok"] == 1
    assert row["materiality_norm"] == pytest.approx(0.9)
    assert row["driver"] == "options_flow"
    assert row["ts"]


def test_record_is_idempotent_per_name_day(tmp_path: Path):
    """A retried name-day overwrites its own row instead of stacking duplicates."""
    db = tmp_path / "t.db"
    for attempt in (1, 2, 3):
        shadow.record(
            "ABCD", "2026-09-15", status="ok", text_model_ok=False,
            input_tokens=attempt * 100, db_path=db,
        )

    row = shadow.get("ABCD", "2026-09-15", db_path=db)
    assert row["input_tokens"] == 300
    assert shadow.summary(db_path=db)["total"] == 1


def test_unavailable_and_error_statuses_keep_their_detail(tmp_path: Path):
    db = tmp_path / "t.db"
    shadow.record("AAA", "2026-09-15", status="unavailable", detail="no key", db_path=db)
    shadow.record("BBB", "2026-09-15", status="error", detail="boom", db_path=db)

    assert shadow.get("AAA", "2026-09-15", db_path=db)["detail"] == "no key"
    assert shadow.get("BBB", "2026-09-15", db_path=db)["status"] == "error"
    assert shadow.summary(db_path=db)["by_status"] == {"unavailable": 1, "error": 1}


def test_unknown_status_is_rejected_by_the_schema(tmp_path: Path):
    """The CHECK constraint is the guard: an ok/error/unavailable typo can't land."""
    db = tmp_path / "t.db"
    shadow.record("AAA", "2026-09-15", status="probably_fine", db_path=db)
    assert shadow.get("AAA", "2026-09-15", db_path=db) is None  # swallowed, not stored


def test_record_never_propagates_a_write_failure(tmp_path: Path, monkeypatch):
    """Fail-soft law: this hangs off a delivery lane, so a DB fault must not surface."""

    def boom(*_args: Any, **_kwargs: Any) -> Any:
        raise RuntimeError("disk on fire")

    monkeypatch.setattr(shadow, "_connect", boom)
    shadow.record("AAA", "2026-09-15", status="ok", db_path=tmp_path / "t.db")  # must not raise


def test_string_values_are_not_silently_coerced_to_scores(tmp_path: Path):
    db = tmp_path / "t.db"
    shadow.record(
        "AAA", "2026-09-15", status="ok",
        answers={"materiality": "very material", "driver": "", "activity_unusual": None},
        db_path=db,
    )
    row = shadow.get("AAA", "2026-09-15", db_path=db)
    assert row["materiality"] is None  # not 0.0 — absent, never fabricated
    assert row["activity_unusual"] is None
    assert row["driver"] is None


# --- Grading join --------------------------------------------------------------


def _seed_outcome(db: Path, symbol: str, session: str, excess: float) -> None:
    from db.attention import init_db
    from db.base import connect

    init_db(db)
    with connect(db) as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO attention_outcomes
              (symbol, session, angles, quintile, bucket, anchor, entry, move_2h,
               move_close, excess_2h, direction, horizon_complete, graded_at)
            VALUES (?, ?, 2, 3, 0, 'first_lit', 41.2, 0.03, 0.05, ?, 1, 1, '2026-09-17T00:00:00Z')
            """,
            (symbol, session, excess),
        )
        conn.commit()


def test_graded_pairs_joins_predictions_to_realized_outcomes(tmp_path: Path):
    db = tmp_path / "t.db"
    shadow.record(
        "ABCD", "2026-09-15", status="ok",
        answers={"materiality_norm": 0.9, "driver": "options_flow"}, db_path=db,
    )
    shadow.record("ZZZZ", "2026-09-15", status="ok", answers={"materiality_norm": 0.1}, db_path=db)
    _seed_outcome(db, "ABCD", "2026-09-15", 0.031)
    # ZZZZ has no outcome row yet — it must not appear.

    pairs = shadow.graded_pairs(db_path=db)
    assert len(pairs) == 1
    assert pairs[0]["symbol"] == "ABCD"
    assert pairs[0]["materiality_norm"] == pytest.approx(0.9)
    assert pairs[0]["excess_2h"] == pytest.approx(0.031)


def test_summary_reports_coverage_and_refuses_to_compute_a_hit_rate(tmp_path: Path):
    db = tmp_path / "t.db"
    shadow.record("AAA", "2026-09-15", status="ok", text_model_ok=False, db_path=db)
    shadow.record("BBB", "2026-09-15", status="ok", text_model_ok=True, db_path=db)
    shadow.record("CCC", "2026-09-15", status="error", db_path=db)

    out = shadow.summary(db_path=db)
    assert out["by_status"] == {"ok": 2, "error": 1}
    assert out["total"] == 3
    day = out["coverage"][0]
    assert day["session"] == "2026-09-15"
    assert day["shadow"] == 3
    assert day["answered"] == 2
    # The number that matters: Jev judged where the prose model returned nothing.
    assert day["recovered_from_text_failure"] == 1
    assert "no hit rate" in out["note"].lower()


# --- The shadow call itself ----------------------------------------------------


def _patch_jev(monkeypatch, *, response: Any = None, configured: bool = True):
    async def fake_asystem_one(state: Any, questions: dict[str, Any], **kwargs: Any) -> Any:
        if isinstance(response, Exception):
            raise response
        assert questions, "the shadow must ask something"
        return response if response is not None else _fake_response()

    monkeypatch.setattr(so, "is_configured", lambda: configured)
    monkeypatch.setattr(so, "asystem_one", fake_asystem_one)


def test_shadow_records_a_gradable_row(tmp_path: Path):
    db = tmp_path / "t.db"
    monkeypatch = pytest.MonkeyPatch()
    _patch_jev(monkeypatch)
    try:
        asyncio.run(
            investigator._jev_shadow(
                "ABCD", "2026-09-15", "facts here", text_model_ok=True, db_path=db
            )
        )
    finally:
        monkeypatch.undo()

    row = shadow.get("ABCD", "2026-09-15", db_path=db)
    assert row["status"] == "ok"
    assert row["model"] == "jev-1.13.0"
    assert row["latency_ms"] == 420
    assert row["input_tokens"] == 500
    assert row["output_tokens"] == 70
    assert row["prompt_chars"] == len("facts here")
    assert row["activity_unusual"] == pytest.approx(0.81)
    assert row["materiality"] == pytest.approx(1.8)
    assert row["materiality_norm"] == pytest.approx(0.9)  # 1.8 / (3 levels - 1)
    assert row["driver"] == "options_flow"
    assert row["driver_confidence"] == pytest.approx(0.9)
    assert row["next_check"] == "news"
    assert row["text_model_ok"] == 1


def test_shadow_records_unavailable_without_calling_the_api(tmp_path: Path):
    db = tmp_path / "t.db"
    monkeypatch = pytest.MonkeyPatch()
    called = {"n": 0}

    async def fake_asystem_one(*_a: Any, **_k: Any) -> Any:
        called["n"] += 1
        return _fake_response()

    monkeypatch.setattr(so, "is_configured", lambda: False)
    monkeypatch.setattr(so, "asystem_one", fake_asystem_one)
    try:
        asyncio.run(
            investigator._jev_shadow("A", "s", "f", text_model_ok=True, db_path=db)
        )
    finally:
        monkeypatch.undo()

    assert called["n"] == 0
    assert shadow.get("A", "s", db_path=db)["status"] == "unavailable"


def test_shadow_swallows_a_provider_failure(tmp_path: Path):
    db = tmp_path / "t.db"
    monkeypatch = pytest.MonkeyPatch()
    _patch_jev(monkeypatch, response=so.SystemOneHTTPError(429, "rate limited"))
    try:
        asyncio.run(
            investigator._jev_shadow("A", "s", "f", text_model_ok=False, db_path=db)
        )
    finally:
        monkeypatch.undo()

    row = shadow.get("A", "s", db_path=db)
    assert row["status"] == "error"
    assert "rate limited" in row["detail"]
    assert row["text_model_ok"] == 0


def test_shadow_bumps_lane_meters(tmp_path: Path):
    from db.base import connect

    db = tmp_path / "t.db"
    monkeypatch = pytest.MonkeyPatch()
    _patch_jev(monkeypatch)
    try:
        asyncio.run(
            investigator._jev_shadow("A", "s", "f", text_model_ok=False, db_path=db)
        )
    finally:
        monkeypatch.undo()

    with connect(db) as conn:
        rows = {
            r["counter"]: r["value"]
            for r in conn.execute(
                "SELECT counter, value FROM lane_stats WHERE lane = ?", (shadow.LANE,)
            )
        }
    assert rows["ok"] == 1
    assert rows["text_model_failed_recovered"] == 1


# --- Wiring: the shadow must never change the lane -----------------------------


def _run_investigator(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, note: str, shadow_raises: bool = False
) -> dict[str, Any] | None:
    """Drive investigate_once with every boundary faked. Returns the lane result."""
    from ai.llm import factory as llm_factory
    from db import attention as attention_db
    from signals import notify as notify_mod

    marks: list[dict[str, Any]] = []

    class FakeLLM:
        async def acomplete(self, messages: Any, *, max_tokens: int = 1024) -> str:
            return note

    async def fake_notify(**_kwargs: Any) -> None:
        return None

    monkeypatch.setattr(attention_db, "next_pending", lambda **_k: {"symbol": "ABCD", "session": "2026-09-15"})
    monkeypatch.setattr(
        attention_db, "mark_queue_item", lambda sym, session, **kw: marks.append({"sym": sym, **kw})
    )
    monkeypatch.setattr(llm_factory, "get_llm", lambda **_k: FakeLLM())
    monkeypatch.setattr(notify_mod, "notify", fake_notify)
    monkeypatch.setattr(investigator, "_facts", lambda item, db_path: "measured facts")

    if shadow_raises:
        async def exploding(*_a: Any, **_k: Any) -> Any:
            raise RuntimeError("shadow exploded")

        monkeypatch.setattr(so, "asystem_one", exploding)
        monkeypatch.setattr(so, "is_configured", lambda: True)
    else:
        _patch_jev(monkeypatch)

    result = asyncio.run(investigator.investigate_once(db_path=tmp_path / "t.db"))
    return {"result": result, "marks": marks}


def test_shadow_runs_after_a_successful_note(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    out = _run_investigator(tmp_path, monkeypatch, note="A real note about ABCD.")

    assert out["result"]["note"] == "A real note about ABCD."
    assert out["marks"][0]["status"] == "done"
    row = shadow.get("ABCD", "2026-09-15", db_path=tmp_path / "t.db")
    assert row["status"] == "ok"
    assert row["text_model_ok"] == 1


def test_shadow_still_runs_when_the_prose_model_returns_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """The empty-note branch is the whole point: a typed answer cannot be blank."""
    out = _run_investigator(tmp_path, monkeypatch, note="")

    assert out["result"] is None  # the lane still fails, exactly as before
    assert out["marks"][0]["status"] == "pending"
    row = shadow.get("ABCD", "2026-09-15", db_path=tmp_path / "t.db")
    assert row["status"] == "ok"
    assert row["text_model_ok"] == 0


def test_a_broken_shadow_cannot_break_the_lane(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    out = _run_investigator(
        tmp_path, monkeypatch, note="A real note.", shadow_raises=True
    )

    assert out["result"]["note"] == "A real note."  # delivered regardless
    assert out["marks"][0]["status"] == "done"
    row = shadow.get("ABCD", "2026-09-15", db_path=tmp_path / "t.db")
    assert row["status"] == "error"


def test_no_shadow_when_the_facts_could_not_be_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Nothing to judge: no state means no row, rather than an empty judgment."""
    from db import attention as attention_db

    monkeypatch.setattr(
        attention_db, "next_pending", lambda **_k: {"symbol": "ABCD", "session": "2026-09-15"}
    )
    monkeypatch.setattr(attention_db, "mark_queue_item", lambda *a, **k: None)

    def boom(_item: Any, _db_path: Any) -> str:
        raise RuntimeError("facts unreadable")

    monkeypatch.setattr(investigator, "_facts", boom)
    _patch_jev(monkeypatch)

    assert asyncio.run(investigator.investigate_once(db_path=tmp_path / "t.db")) is None
    assert shadow.get("ABCD", "2026-09-15", db_path=tmp_path / "t.db") is None
