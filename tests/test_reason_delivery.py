"""Fusion Phase C5 — runner wiring, delivery, crash recovery, skip-reason API, alarms.

Everything here is inert in production (dark by REASON_EMIT_ENABLED, or inert by data);
the tests arm the pieces explicitly. FakeLLM/_seed_pending are copied minimally from
tests/test_reason.py (the house has no cross-test imports).
"""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from db import fusion as fusion_db
from db import lane_stats, llm_spend
from db.base import connect
from db.idea_outcomes import register_idea
from research.reason import run_reason_pass
from signals import pipeline_audit as pa

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
    """Arm the lane (the future ARM commit flips this flag in production)."""
    monkeypatch.setattr("config.idea_engine.REASON_EMIT_ENABLED", True)


class FakeLLM:
    """Deterministic, offline: returns queued responses, counts calls."""

    provider = "fake"
    _model = "fake-model-x"

    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)
        self.calls = 0

    async def acomplete(self, messages: list[Any], *, max_tokens: int = 1024) -> str:
        self.calls += 1
        if not self._responses:
            return ""
        return self._responses.pop(0)


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


def _t1(symbol: str = SYMBOL, *, investigate: bool = True) -> str:
    return json.dumps(
        {
            "candidates": [
                {
                    "symbol": symbol,
                    "investigate": investigate,
                    "null_hypothesis": "likely a hedge/roll — check OI and the ladder",
                    "angle_to_check": "expiry ladder",
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


def _notifications(db: Path, *, kind: str | None = None) -> list[dict[str, Any]]:
    from db.notifications import init_notifications_tables

    init_notifications_tables(db)
    with connect(db) as conn:
        if kind is None:
            rows = conn.execute("SELECT * FROM notifications ORDER BY created_at").fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM notifications WHERE kind = ? ORDER BY created_at", (kind,)
            ).fetchall()
    out: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        try:
            item["data"] = json.loads(item.get("data_json") or "{}")
        except (TypeError, json.JSONDecodeError):
            item["data"] = {}
        out.append(item)
    return out


# ------------------------------------------------------------- 1. dark job body


async def test_dark_job_body_builds_no_client_and_never_calls_pass(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import api.main as main_mod

    hits = {"reason": 0, "llm": 0}

    async def fake_pass(**_kw: Any) -> dict:
        hits["reason"] += 1
        return {"disabled": True}

    def fake_get_llm(*_a: Any, **_k: Any) -> Any:
        hits["llm"] += 1
        return object()

    monkeypatch.setattr("config.idea_engine.REASON_EMIT_ENABLED", False)
    monkeypatch.setattr("research.reason.run_reason_pass", fake_pass)
    monkeypatch.setattr("ai.llm.factory.get_llm", fake_get_llm)

    await main_mod._fusion_reason_once()

    assert hits == {"reason": 0, "llm": 0}  # dark: no client built, pass never entered


# ----------------------------------------------------- 2. armed delivery + honesty law


async def test_armed_pass_delivers_fusion_idea_notification(
    db: Path, enabled: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_pending(db)

    async def fake_push(title: str, body: str, *, data: Any = None, db_path: Any = None) -> int:
        return 1

    monkeypatch.setattr("signals.push.send_alert_push", fake_push)

    triage, reason = FakeLLM([_t1()]), FakeLLM([_v1_plan()])
    result = await run_reason_pass(db_path=db, now=NOW, llm_triage=triage, llm_reason=reason)

    assert result["counters"].get("v1_ok") == 1
    assert result["counters"].get("delivered") == 1

    notes = _notifications(db, kind="fusion_idea")
    assert len(notes) == 1
    note = notes[0]
    assert note["ref_type"] == "idea"
    assert "unproven" in note["body"].lower()
    assert "%" not in note["body"]  # no probability/win% ever reaches the phone copy
    # Honesty law: no win%/probability/score key anywhere in the delivered payload.
    for key in note["data"]:
        low = key.lower()
        assert "win" not in low and "prob" not in low and "score" not in low
    idea = _idea_by_origin(db, "reason_v1")
    assert idea is not None
    assert int(note["data"]["idea_id"]) == int(idea["id"])


# ------------------------------------------------ 3. push budget (deny + mark-on-success)


async def test_budget_exhausted_persists_card_and_suppresses_push(
    db: Path, enabled: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    from config.settings import alerts_config
    from db.earnings_alerts import mark_sent

    _seed_pending(db)
    # Exhaust every daily slot for the shared pool BEFORE the pass runs.
    for n in range(1, alerts_config()["max_push_per_day"] + 1):
        mark_sent(f"of_push:{MARKET_DAY}:{n}", db_path=db)

    pushed: list[int] = []

    async def fake_push(*_a: Any, **_k: Any) -> int:
        pushed.append(1)
        return 1

    monkeypatch.setattr("signals.push.send_alert_push", fake_push)

    triage, reason = FakeLLM([_t1()]), FakeLLM([_v1_plan()])
    result = await run_reason_pass(db_path=db, now=NOW, llm_triage=triage, llm_reason=reason)

    assert result["counters"].get("delivered") == 1  # inbox card still delivered
    assert result["counters"].get("push_denied") == 1  # phone push budget-denied
    assert len(_notifications(db, kind="fusion_idea")) == 1
    assert pushed == []  # no phone push fired


async def test_slot_marked_only_after_successful_delivery(
    db: Path, enabled: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    from db.earnings_alerts import was_sent

    async def fake_push(*_a: Any, **_k: Any) -> int:
        return 1

    monkeypatch.setattr("signals.push.send_alert_push", fake_push)

    _seed_pending(db)
    assert not was_sent(f"of_push:{MARKET_DAY}:1", db_path=db)
    triage, reason = FakeLLM([_t1()]), FakeLLM([_v1_plan()])
    await run_reason_pass(db_path=db, now=NOW, llm_triage=triage, llm_reason=reason)
    assert was_sent(f"of_push:{MARKET_DAY}:1", db_path=db)  # consumed on success


# ------------------------------------------------------- 4. crash-window idempotency


async def test_crash_recovery_reason_v1_registers_nothing_new(
    db: Path, enabled: None
) -> None:
    _seed_pending(db, anchor="245.00")
    # A crashed pass: idea registered (with episode_id) but NO reason_runs marker.
    idea_id = register_idea(
        origin="reason_v1", signal_family="fusion", symbol=SYMBOL, direction="long",
        entry_zone={"low": "244.50", "high": "246.00"}, stop="239.80", targets=["252.00"],
        thesis="t", bear_case="b", confidence_label="medium", plan_horizon="5d",
        grading_basis="plan", episode_id=EP_ID, dossier_ref=EP_ID, db_path=db,
    )
    assert idea_id is not None
    assert fusion_db.get_reason_run(EP_ID, db_path=db) is None  # marker lost in the crash

    triage, reason = FakeLLM([_t1()]), FakeLLM([_v1_plan()])
    result = await run_reason_pass(db_path=db, now=NOW, llm_triage=triage, llm_reason=reason)

    assert result["counters"].get("recovered_after_crash") == 1
    assert triage.calls == 0 and reason.calls == 0  # zero LLM
    assert llm_spend.day_spend(MARKET_DAY, db_path=db) == []  # zero spend
    assert _count_idea_by_origin(db, "reason_v1") == 1  # nothing new registered

    run = fusion_db.get_reason_run(EP_ID, db_path=db)
    assert run is not None
    assert run["v1_status"] == "ok"
    assert int(run["idea_id"]) == int(idea_id)
    assert run["detail"]["recovered_after_crash"] is True


async def test_crash_recovery_template_shadow_registers_nothing_new(
    db: Path, enabled: None
) -> None:
    _seed_pending(db, anchor="245.00")
    shadow_id = register_idea(
        origin="t1_dismissal", signal_family="fusion", symbol=SYMBOL, direction="long",
        entry_zone=None, stop=None, targets=None, grading_basis="horizon_return",
        entry_ref_price="245.00", plan_horizon="2d", synthetic=1,
        episode_id=EP_ID, dossier_ref=EP_ID, db_path=db,
    )
    assert shadow_id is not None

    triage, reason = FakeLLM([_t1()]), FakeLLM([_v1_plan()])
    result = await run_reason_pass(db_path=db, now=NOW, llm_triage=triage, llm_reason=reason)

    assert result["counters"].get("recovered_after_crash") == 1
    assert triage.calls == 0 and reason.calls == 0
    assert llm_spend.day_spend(MARKET_DAY, db_path=db) == []
    assert _count_idea_by_origin(db, "t1_dismissal") == 1
    assert _idea_by_origin(db, "reason_v1") is None

    run = fusion_db.get_reason_run(EP_ID, db_path=db)
    assert int(run["t1_investigate"]) == 0
    assert run["v1_status"] is None
    assert int(run["idea_id"]) == int(shadow_id)


# ------------------------------------------------------------ 5. skip-reason endpoint


def test_idea_action_endpoint(mutation_client: Any) -> None:
    from db.base import DEFAULT_DB_PATH
    from db.idea_outcomes import register_idea as _reg

    def _mk(symbol: str) -> int:
        idea_id = _reg(
            origin="reason_v1", signal_family="fusion", symbol=symbol, direction="long",
            entry_zone={"low": "244.50", "high": "246.00"}, stop="239.80",
            targets=["252.00"], thesis="t", bear_case="b", plan_horizon="5d",
            grading_basis="plan", db_path=DEFAULT_DB_PATH,
        )
        assert idea_id is not None
        return idea_id

    # happy path: taken
    i_taken = _mk("DELIVA")
    r = mutation_client.post(f"/api/fusion/ideas/{i_taken}/action", json={"action": "taken"})
    assert r.status_code == 200 and r.json()["action"] == "taken"

    # happy path: skipped + reason
    i_skip = _mk("DELIVB")
    r = mutation_client.post(
        f"/api/fusion/ideas/{i_skip}/action",
        json={"action": "skipped", "skip_reason": "no_capital"},
    )
    assert r.status_code == 200 and r.json()["skip_reason"] == "no_capital"

    # invalid reason → 400/422
    i_bad = _mk("DELIVC")
    r = mutation_client.post(
        f"/api/fusion/ideas/{i_bad}/action",
        json={"action": "skipped", "skip_reason": "bogus"},
    )
    assert r.status_code in (400, 422)

    # skip_reason paired with 'taken' → rejected
    i_pair = _mk("DELIVD")
    r = mutation_client.post(
        f"/api/fusion/ideas/{i_pair}/action",
        json={"action": "taken", "skip_reason": "no_capital"},
    )
    assert r.status_code in (400, 422)

    # unknown id → 404
    r = mutation_client.post("/api/fusion/ideas/99999999/action", json={"action": "taken"})
    assert r.status_code == 404

    # persisted forward-only
    with connect(DEFAULT_DB_PATH) as conn:
        row = conn.execute(
            "SELECT user_action, skip_reason FROM idea_outcomes WHERE id = ?", (i_skip,)
        ).fetchone()
    assert row["user_action"] == "skipped"
    assert row["skip_reason"] == "no_capital"


# ------------------------------------------------------------------- 6. lane alarms


def _bump(db: Path, counter: str, value: float) -> None:
    lane_stats.bump("fusion_reason", counter, value, date=MARKET_DAY, db_path=db)


def test_reason_parse_fail_rule_dark_is_silent(
    db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _bump(db, "v1_ok", 1)
    _bump(db, "v1_parse_fail", 9)  # a terrible rate
    monkeypatch.setattr("config.idea_engine.REASON_EMIT_ENABLED", False)
    assert pa.rule_reason_parse_fail(date.fromisoformat(MARKET_DAY), db) is None


def test_reason_parse_fail_rule_fires_above_threshold(
    db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _bump(db, "v1_ok", 3)
    _bump(db, "v1_parse_fail", 3)  # total 6 (>=5), rate 0.5 > 0.2
    monkeypatch.setattr("config.idea_engine.REASON_EMIT_ENABLED", True)
    msg = pa.rule_reason_parse_fail(date.fromisoformat(MARKET_DAY), db)
    assert msg is not None and "unusable-output" in msg


def test_reason_parse_fail_rule_silent_below_threshold(
    db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _bump(db, "v1_ok", 9)
    _bump(db, "v1_parse_fail", 1)  # total 10, rate 0.1 < 0.2
    monkeypatch.setattr("config.idea_engine.REASON_EMIT_ENABLED", True)
    assert pa.rule_reason_parse_fail(date.fromisoformat(MARKET_DAY), db) is None


def test_reason_parse_fail_rule_silent_thin_sample(
    db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _bump(db, "v1_parse_fail", 3)  # total 3 < 5 min attempts, even at 100%
    monkeypatch.setattr("config.idea_engine.REASON_EMIT_ENABLED", True)
    assert pa.rule_reason_parse_fail(date.fromisoformat(MARKET_DAY), db) is None


def test_reason_consecutive_failures_fires_on_bad_tail(
    db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for status in ("ok", "parse_fail", "timeout", "refusal"):
        llm_spend.record_spend(MARKET_DAY, "reason_v1", status, db_path=db)
    monkeypatch.setattr("config.idea_engine.REASON_EMIT_ENABLED", True)
    msg = pa.rule_reason_consecutive_failures(date.fromisoformat(MARKET_DAY), db)
    assert msg is not None and "failing in a row" in msg


def test_reason_consecutive_failures_silent_on_ok_mixed_tail(
    db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for status in ("parse_fail", "timeout", "ok"):
        llm_spend.record_spend(MARKET_DAY, "t1", status, db_path=db)
    monkeypatch.setattr("config.idea_engine.REASON_EMIT_ENABLED", True)
    assert pa.rule_reason_consecutive_failures(date.fromisoformat(MARKET_DAY), db) is None


def test_reason_consecutive_failures_dark_is_silent(
    db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for status in ("parse_fail", "timeout", "refusal"):
        llm_spend.record_spend(MARKET_DAY, "reason_v1", status, db_path=db)
    monkeypatch.setattr("config.idea_engine.REASON_EMIT_ENABLED", False)
    assert pa.rule_reason_consecutive_failures(date.fromisoformat(MARKET_DAY), db) is None


def test_reason_consecutive_failures_ignores_budget_denied(
    db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # budget_denied is NOT an attempt — the three real attempts are all failures.
    llm_spend.record_spend(MARKET_DAY, "reason_v1", "parse_fail", db_path=db)
    llm_spend.record_spend(MARKET_DAY, "reason_v1", "timeout", db_path=db)
    llm_spend.record_spend(MARKET_DAY, "reason_v1", "refusal", db_path=db)
    llm_spend.record_spend(MARKET_DAY, "reason_v1", "budget_denied", db_path=db)
    monkeypatch.setattr("config.idea_engine.REASON_EMIT_ENABLED", True)
    assert pa.rule_reason_consecutive_failures(date.fromisoformat(MARKET_DAY), db) is not None


def test_first_sentence_survives_decimal_prices() -> None:
    # Theses quote decimal prices; a bare-period split would truncate "452.5"
    # to "452." in founder-facing push copy.
    from research.reason import _first_sentence

    assert (
        _first_sentence("Flow clustered at the 452.5 strike. Second thought.")
        == "Flow clustered at the 452.5 strike."
    )
    assert _first_sentence("One line only, no period") == "One line only, no period"
    assert _first_sentence("First line\nsecond line") == "First line"


# ------------------------------- adversarial-review fixes (07-21): pinning tests


async def test_crash_recovery_spend_anchor_no_idea(db: Path, enabled: None) -> None:
    # The spend row is the persist-before-use anchor: a no_idea pass that crashed
    # AFTER its spend commit but BEFORE its marker left ONLY the spend row. The
    # restart pass must recover it — never re-call v1 (double spend) and never
    # register/deliver a phantom idea for an episode already judged no-idea.
    _seed_pending(db)
    llm_spend.record_spend(
        MARKET_DAY, "reason_v1", "ok", episode_id=EP_ID, db_path=db
    )

    triage, reason = FakeLLM([_t1()]), FakeLLM([_v1_plan()])
    result = await run_reason_pass(db_path=db, now=NOW, llm_triage=triage, llm_reason=reason)

    assert triage.calls == 0 and reason.calls == 0  # recovered pre-T1, zero LLM
    assert result["counters"].get("recovered_after_crash") == 1
    assert _idea_by_origin(db, "reason_v1") is None  # nothing registered
    assert _notifications(db, kind="fusion_idea") == []  # nothing delivered
    marker = fusion_db.get_reason_run(EP_ID, db_path=db)
    assert marker is not None
    assert int(marker["no_idea"]) == 1
    assert marker["detail"]["anchor"] == "llm_spend"
    assert marker["detail"]["ambiguous_ok"] is True
    rows = llm_spend.day_spend(MARKET_DAY, db_path=db)
    assert len(rows) == 1  # still exactly ONE spend row — no double spend


async def test_crash_recovery_spend_anchor_failure_status(db: Path, enabled: None) -> None:
    # Same window for a failure status: the shadow was lost in the crash — flagged,
    # never re-spent.
    _seed_pending(db)
    llm_spend.record_spend(
        MARKET_DAY, "reason_v1", "parse_fail", episode_id=EP_ID, db_path=db
    )

    triage, reason = FakeLLM([_t1()]), FakeLLM([_v1_plan()])
    await run_reason_pass(db_path=db, now=NOW, llm_triage=triage, llm_reason=reason)

    assert triage.calls == 0 and reason.calls == 0
    marker = fusion_db.get_reason_run(EP_ID, db_path=db)
    assert marker is not None
    assert marker["v1_status"] == "parse_fail"
    assert marker["detail"]["shadow_lost"] is True
    assert len(llm_spend.day_spend(MARKET_DAY, db_path=db)) == 1


async def test_null_hypothesis_result_probability_withheld(
    db: Path, enabled: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    # null_hypothesis_result is the one delivered free-text field the registration
    # wall never checks — a fabricated probability in it must be withheld from
    # every user surface (honesty red-team 07-08).
    _seed_pending(db)

    async def fake_push(title: str, body: str, *, data: Any = None, db_path: Any = None) -> int:
        return 1

    monkeypatch.setattr("signals.push.send_alert_push", fake_push)
    plan = json.loads(_v1_plan())
    plan["null_hypothesis_result"] = "roughly a 70% chance this is not a hedge"
    triage, reason = FakeLLM([_t1()]), FakeLLM([json.dumps(plan)])
    result = await run_reason_pass(db_path=db, now=NOW, llm_triage=triage, llm_reason=reason)

    assert result["counters"].get("delivered") == 1
    (note,) = _notifications(db, kind="fusion_idea")
    assert "70%" not in json.dumps(note["data"])
    assert note["data"]["null_hypothesis_result"].startswith("[withheld")


def test_rate_rule_counts_timeouts_and_register_rejected(
    db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Adversarial review: a flaky-timeout or wall-rejected lane must trip the rate
    # rule — "parses" is not "healthy".
    for key, n in (("v1_ok", 1), ("v1_timeout", 2), ("v1_register_rejected", 2)):
        lane_stats.bump("fusion_reason", key, n, date=MARKET_DAY, db_path=db)
    monkeypatch.setattr("config.idea_engine.REASON_EMIT_ENABLED", True)
    assert pa.rule_reason_parse_fail(date.fromisoformat(MARKET_DAY), db) is not None


def test_rate_rule_below_threshold_stays_silent(
    db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for key, n in (("v1_ok", 5), ("v1_timeout", 1)):
        lane_stats.bump("fusion_reason", key, n, date=MARKET_DAY, db_path=db)
    monkeypatch.setattr("config.idea_engine.REASON_EMIT_ENABLED", True)
    assert pa.rule_reason_parse_fail(date.fromisoformat(MARKET_DAY), db) is None


def test_arm_edit4_never_worked_survives(
    db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Simulate the ARM commit's edit 4 (fusion_reason joins ALARM_EXPECTED_LANES):
    # _never_worked_lanes must not KeyError — the counters-map entry is pre-staged.
    armed_lanes = frozenset(pa.ALARM_EXPECTED_LANES | {"fusion_reason"})
    monkeypatch.setattr(pa, "ALARM_EXPECTED_LANES", armed_lanes)
    result = pa._never_worked_lanes(date.fromisoformat(MARKET_DAY), db)
    assert isinstance(result, list)  # no KeyError — coverage intact for every lane
