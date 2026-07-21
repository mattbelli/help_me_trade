"""Fusion Phase C3 — the LLM investigation lane (research/reason.py).

Hermetic: a scripted fake LLM (queued canned responses, call-counting) is injected as
llm_triage/llm_reason, so no network. Episodes are seeded as C1 shadow_investigate rows +
their fusion_episodes; the runner is DARK unless REASON_EMIT_ENABLED is monkeypatched True.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from db import fusion as fusion_db
from db import lane_stats, llm_spend
from db.base import connect
from research.reason import run_reason_pass

MARKET_DAY = "2026-07-20"  # a Monday trading day
NOW = datetime(2026, 7, 20, 18, 0, tzinfo=timezone.utc)  # 14:00 ET on MARKET_DAY
SYMBOL = "TSLA"
EP_ID = "ep1"


# --------------------------------------------------------------------------- fixtures


@pytest.fixture
def db(tmp_path: Path) -> Path:
    path = tmp_path / "fusion.db"
    fusion_db.init_fusion_tables(path)
    return path


@pytest.fixture
def enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """Arm the lane (C5 flips this flag in production)."""
    monkeypatch.setattr("config.idea_engine.REASON_EMIT_ENABLED", True)


class FakeLLM:
    """Deterministic, offline: returns queued responses, counts calls. delay>0 lets a call
    outlive a patched REASON_CALL_TIMEOUT_S so asyncio.wait_for trips a timeout."""

    provider = "fake"
    _model = "fake-model-x"

    def __init__(self, responses: list[str], *, delay: float = 0.0) -> None:
        self._responses = list(responses)
        self.delay = delay
        self.calls = 0

    async def acomplete(self, messages: list[Any], *, max_tokens: int = 1024) -> str:
        self.calls += 1
        if self.delay:
            await asyncio.sleep(self.delay)
        if not self._responses:
            return ""  # empty completion → refusal
        return self._responses.pop(0)


# ---------------------------------------------------------------------------- seeding


def _seed_pending(
    db: Path,
    *,
    ep_id: str = EP_ID,
    symbol: str = SYMBOL,
    anchor: str | None = "245.00",
    market_day: str = MARKET_DAY,
    score: float = 5.0,
    direction_state: str = "mixed",
) -> None:
    """One admitted episode (evidence dot + shadow_investigate admission)."""
    ts = f"{market_day}T18:00:00+00:00"
    with connect(db) as conn:
        conn.execute(
            """
            INSERT INTO fusion_episodes(
                id, symbol, market_day, opened_at, updated_at, anchor_price, score,
                direction_state
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (ep_id, symbol, market_day, ts, ts, anchor, float(score), direction_state),
        )
        conn.execute(
            """
            INSERT INTO evidence_dots(
                id, episode_id, source_store, source_event_id, source_ingest_seq,
                symbol, ts, component, actor_group, dot_kind, direction,
                raw_value, points, detail_json, stamped, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                f"{ep_id}_d1", ep_id, "options_flow", f"{ep_id}_d1", 0, symbol, ts,
                "option_sweep", "options_actor", "event", "bull", "250000", 2.5, "{}", 1, ts,
            ),
        )
        conn.execute(
            """
            INSERT INTO fusion_investigations(
                episode_id, market_day, symbol, decided_at, decision, score, threshold
            ) VALUES (?, ?, ?, ?, 'shadow_investigate', ?, ?)
            """,
            (ep_id, market_day, symbol, ts, float(score), 1.0),
        )
        conn.commit()


# --------------------------------------------------------------------- canned responses


def _t1(
    symbol: str = SYMBOL,
    *,
    investigate: bool = True,
    null: str = "likely a hedge/roll — check OI and the expiry ladder",
    angle: str = "expiry ladder",
) -> str:
    return json.dumps(
        {
            "candidates": [
                {
                    "symbol": symbol,
                    "investigate": investigate,
                    "null_hypothesis": null,
                    "angle_to_check": angle,
                }
            ]
        }
    )


def _v1_plan(symbol: str = SYMBOL, horizon: str = "5d") -> str:
    return json.dumps(
        {
            "symbol": symbol,
            "direction": "long",
            "entry_zone": {"low": "244.50", "high": "246.00"},
            "stop": "239.80",
            "targets": ["252.00"],
            "horizon": horizon,
            "confidence": "medium",
            "thesis": "ask-side call sweep per evidence:0 with level support holding.",
            "bear_case": "the block may be a hedge; put skew could roll against it.",
            "null_hypothesis_result": "checked the expiry ladder — not a pure hedge.",
        }
    )


def _v1_no_idea(reason: str = "contested tape — nothing actionable") -> str:
    return json.dumps({"no_idea": True, "reason": reason})


# ------------------------------------------------------------------------ query helpers


def _idea_by_origin(db: Path, origin: str) -> dict[str, Any] | None:
    with connect(db) as conn:
        try:
            row = conn.execute(
                "SELECT * FROM idea_outcomes WHERE origin = ? ORDER BY id DESC LIMIT 1",
                (origin,),
            ).fetchone()
        except Exception:
            return None
    return dict(row) if row is not None else None


def _count_idea_by_origin(db: Path, origin: str) -> int:
    with connect(db) as conn:
        try:
            return int(
                conn.execute(
                    "SELECT COUNT(*) FROM idea_outcomes WHERE origin = ?", (origin,)
                ).fetchone()[0]
            )
        except Exception:
            return 0


# ------------------------------------------------------------------------------- tests


async def test_disabled_flag_is_a_counted_noop(db: Path) -> None:
    _seed_pending(db)
    triage, reason = FakeLLM([_t1()]), FakeLLM([_v1_plan()])
    result = await run_reason_pass(db_path=db, now=NOW, llm_triage=triage, llm_reason=reason)

    assert result == {"disabled": True}
    assert triage.calls == 0 and reason.calls == 0
    assert fusion_db.get_reason_run(EP_ID, db_path=db) is None
    assert llm_spend.day_spend(MARKET_DAY, db_path=db) == []
    assert _idea_by_origin(db, "reason_v1") is None


async def test_happy_path_registers_reason_v1(db: Path, enabled: None) -> None:
    _seed_pending(db, anchor="245.00")
    triage, reason = FakeLLM([_t1(investigate=True)]), FakeLLM([_v1_plan(horizon="5d")])
    result = await run_reason_pass(db_path=db, now=NOW, llm_triage=triage, llm_reason=reason)

    assert result["disabled"] is False
    assert result["counters"].get("v1_ok") == 1

    idea = _idea_by_origin(db, "reason_v1")
    assert idea is not None
    assert idea["episode_id"] == EP_ID
    assert idea["dossier_ref"] == EP_ID
    assert idea["confidence_label"] == "medium"  # stored as the string
    assert idea["symbol"] == SYMBOL
    assert idea["direction"] == "long"
    assert int(idea["synthetic"]) == 0

    spends = {(s["lane"], s["status"]) for s in llm_spend.day_spend(MARKET_DAY, db_path=db)}
    assert ("t1", "ok") in spends
    assert ("reason_v1", "ok") in spends

    run = fusion_db.get_reason_run(EP_ID, db_path=db)
    assert run is not None
    assert run["t1_status"] == "ok"
    assert int(run["t1_investigate"]) == 1
    assert run["v1_status"] == "ok"
    assert int(run["idea_id"]) == int(idea["id"])
    assert int(run["no_idea"]) == 0
    assert run["null_hypothesis"]  # T1's null persisted


async def test_t1_dismissal_shadow_registers_and_skips_v1(db: Path, enabled: None) -> None:
    _seed_pending(db, anchor="245.00")
    triage, reason = FakeLLM([_t1(investigate=False)]), FakeLLM([])
    result = await run_reason_pass(db_path=db, now=NOW, llm_triage=triage, llm_reason=reason)

    assert reason.calls == 0  # dismissed → no v1 call
    assert result["counters"].get("t1_dismissed") == 1

    idea = _idea_by_origin(db, "t1_dismissal")
    assert idea is not None
    assert int(idea["synthetic"]) == 1
    assert idea["grading_basis"] == "horizon_return"
    assert idea["episode_id"] == EP_ID
    assert _idea_by_origin(db, "reason_v1") is None

    run = fusion_db.get_reason_run(EP_ID, db_path=db)
    assert int(run["t1_investigate"]) == 0
    assert run["v1_status"] is None
    assert int(run["idea_id"]) == int(idea["id"])


async def test_v1_parse_fail_shadows_template_and_bumps_counter(db: Path, enabled: None) -> None:
    _seed_pending(db, anchor="245.00")
    triage = FakeLLM([_t1(investigate=True)])
    # Both the first turn and the repair turn return a valid JSON OBJECT of the wrong shape
    # (a bare string with no object would classify as refusal, not parse_fail).
    reason = FakeLLM(['{"unexpected": "keys"}', '{"still": "wrong"}'])
    result = await run_reason_pass(db_path=db, now=NOW, llm_triage=triage, llm_reason=reason)

    assert reason.calls == 2  # first + exactly one repair retry
    assert result["counters"].get("v1_parse_fail") == 1
    assert _idea_by_origin(db, "reason_v1") is None

    failed = _idea_by_origin(db, "reason_v1_failed_template")
    assert failed is not None
    assert int(failed["synthetic"]) == 1
    assert failed["grading_basis"] == "horizon_return"

    spends = {(s["lane"], s["status"]) for s in llm_spend.day_spend(MARKET_DAY, db_path=db)}
    assert ("reason_v1", "parse_fail") in spends

    meters = lane_stats.day("fusion_reason", MARKET_DAY, db_path=db)
    assert meters.get("v1_parse_fail") == 1.0
    assert meters.get("v1_failed_template") == 1.0

    run = fusion_db.get_reason_run(EP_ID, db_path=db)
    assert run["v1_status"] == "parse_fail"


async def test_v1_timeout_shadows_template(
    db: Path, enabled: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("config.idea_engine.REASON_CALL_TIMEOUT_S", 0.05)
    _seed_pending(db, anchor="245.00")
    triage = FakeLLM([_t1(investigate=True)])
    reason = FakeLLM([_v1_plan()], delay=0.5)  # outlives the tiny timeout
    result = await run_reason_pass(db_path=db, now=NOW, llm_triage=triage, llm_reason=reason)

    assert result["counters"].get("v1_timeout") == 1
    assert reason.calls == 1  # timeout does not trigger the repair turn
    assert _idea_by_origin(db, "reason_v1") is None
    assert _idea_by_origin(db, "reason_v1_failed_template") is not None

    spends = {(s["lane"], s["status"]) for s in llm_spend.day_spend(MARKET_DAY, db_path=db)}
    assert ("reason_v1", "timeout") in spends
    assert fusion_db.get_reason_run(EP_ID, db_path=db)["v1_status"] == "timeout"


async def test_v1_budget_denied_marks_processed_without_shadow(
    db: Path, enabled: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("config.idea_engine.REASON_V1_DAILY_BUDGET", 1)
    _seed_pending(db, anchor="245.00")
    llm_spend.record_spend(MARKET_DAY, "reason_v1", "ok", db_path=db)  # seed to the ceiling
    triage, reason = FakeLLM([_t1(investigate=True)]), FakeLLM([_v1_plan()])
    result = await run_reason_pass(db_path=db, now=NOW, llm_triage=triage, llm_reason=reason)

    assert reason.calls == 0  # denied before the call
    assert result["counters"].get("v1_budget_denied") == 1
    # A budget denial is NOT a model failure → no shadow of any kind.
    assert _idea_by_origin(db, "reason_v1") is None
    assert _idea_by_origin(db, "reason_v1_failed_template") is None

    v1_spends = [s for s in llm_spend.day_spend(MARKET_DAY, db_path=db) if s["lane"] == "reason_v1"]
    assert any(s["status"] == "budget_denied" for s in v1_spends)
    # The denial row must NOT self-count against the budget.
    assert llm_spend.lane_spent(MARKET_DAY, "reason_v1", db_path=db) == 1

    run = fusion_db.get_reason_run(EP_ID, db_path=db)  # episode marked processed
    assert run is not None
    assert run["v1_status"] == "budget_denied"


async def test_v1_no_idea_registers_nothing(db: Path, enabled: None) -> None:
    _seed_pending(db, anchor="245.00")
    triage, reason = FakeLLM([_t1(investigate=True)]), FakeLLM([_v1_no_idea()])
    result = await run_reason_pass(db_path=db, now=NOW, llm_triage=triage, llm_reason=reason)

    assert result["counters"].get("v1_no_idea") == 1
    assert _idea_by_origin(db, "reason_v1") is None
    assert _idea_by_origin(db, "reason_v1_failed_template") is None

    run = fusion_db.get_reason_run(EP_ID, db_path=db)
    assert int(run["no_idea"]) == 1
    assert run["v1_status"] == "no_idea"
    assert run["idea_id"] is None

    spends = {(s["lane"], s["status"]) for s in llm_spend.day_spend(MARKET_DAY, db_path=db)}
    assert ("reason_v1", "ok") in spends  # a successful call, just no plan


async def test_sub_1d_horizon_is_a_contract_failure(db: Path, enabled: None) -> None:
    _seed_pending(db, anchor="245.00")
    triage = FakeLLM([_t1(investigate=True)])
    # A sub-1d horizon must be rejected by the CONTRACT (repair ladder → parse_fail), never
    # reaching register_idea's [ADR-3] guard.
    reason = FakeLLM([_v1_plan(horizon="4h"), _v1_plan(horizon="0d")])
    result = await run_reason_pass(db_path=db, now=NOW, llm_triage=triage, llm_reason=reason)

    assert reason.calls == 2
    assert result["counters"].get("v1_parse_fail") == 1
    assert _idea_by_origin(db, "reason_v1") is None
    assert fusion_db.get_reason_run(EP_ID, db_path=db)["v1_status"] == "parse_fail"


async def test_bear_aligned_dismissal_shadow_is_short(db: Path, enabled: None) -> None:
    """[ADR-6]: the class template follows the stack's aligned direction — a bear stack's
    dismissal graded long would invert the sign of the dismissal-cohort report."""
    _seed_pending(db, anchor="245.00", direction_state="aligned(bear)")
    triage, reason = FakeLLM([_t1(investigate=False)]), FakeLLM([])
    await run_reason_pass(db_path=db, now=NOW, llm_triage=triage, llm_reason=reason)

    idea = _idea_by_origin(db, "t1_dismissal")
    assert idea is not None
    assert idea["direction"] == "short"
    assert int(idea["synthetic"]) == 1


async def test_second_pass_is_idempotent(db: Path, enabled: None) -> None:
    _seed_pending(db, anchor="245.00")
    first = await run_reason_pass(
        db_path=db, now=NOW,
        llm_triage=FakeLLM([_t1(investigate=True)]), llm_reason=FakeLLM([_v1_plan()]),
    )
    assert first["pending"] == 1
    assert first["counters"].get("v1_ok") == 1

    triage2, reason2 = FakeLLM([_t1(investigate=True)]), FakeLLM([_v1_plan()])
    second = await run_reason_pass(db_path=db, now=NOW, llm_triage=triage2, llm_reason=reason2)

    assert second["pending"] == 0  # reason_runs marker → nothing to pick up
    assert triage2.calls == 0 and reason2.calls == 0
    assert _count_idea_by_origin(db, "reason_v1") == 1  # no double registration
