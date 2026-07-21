"""Fusion Phase C3 — the LLM INVESTIGATION lane (T1 batch triage + REASON v1).

Governing law: ``IDEA_ENGINE_UNIFIED_DESIGN_2026-07-16.md`` component 4 + the "LLM
invocation design" section + Phase 3; ``FUSION_EVIDENCE_BUS_SPEC_2026-07-17.md`` §2.6/
§3-C. This is the LLM tier that rides the Fusion bus: C1 (``signals/fusion/investigate``)
admits ~5 episodes/day as ``fusion_investigations`` rows with decision
``shadow_investigate``; C2 (``signals/dossier``) builds the deterministic claim matrix;
C3 (here) turns an admitted episode into a full trade idea — or grades the dismissal.

**Ships DARK.** ``run_reason_pass`` returns a counted no-op unless
``config.idea_engine.REASON_EMIT_ENABLED`` is True (the C3↔C5 arming seam). Nothing in
production imports this module yet — the intake hook and the pipeline-audit alarm rules
are C5. (C1's intake gate asserts ``REASON_SHADOW_ONLY``; that is a DIFFERENT seam and is
never touched here.)

Load-bearing invariants:
  * **No trading toolset, ever** — outputs terminate at ``register_idea``. One structured
    completion per call (no tool belt — that is REASON v2, out of scope).
  * **Persist-before-use** — every call resolves to exactly one of
    ``{ok, parse_fail, refusal, timeout, budget_denied}`` and its ``llm_spend`` row is
    committed BEFORE the parsed content is used to register anything.
  * **No fabricated fallback** — parse ladder is validate → ONE repair retry (specific
    error echoed) → ``parse_fail``, no idea registered from the failed call. A parse-fail/
    refusal/timeout is instead shadow-registered against the CLASS TEMPLATE (distinct
    origin) so the model failing preferentially on hard setups can't bias survivors up.
  * **No probability field** — the v1 contract has no numeric win%/score; the pydantic
    models are ``extra="forbid"``. Free text still passes ``register_idea``'s built-in
    ``_has_fabricated_probability`` wall.
  * **[ADR-3]** — the ledger grades daily bars; the v1 contract requires whole-trading-day
    horizons ≥ 1 so a valid LLM answer can't die silently at the ledger door.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, ValidationError, field_validator, model_validator

import config.idea_engine as _cfg  # patchable seam (REASON_EMIT_ENABLED, budgets, timeout)
from ai.llm.base import LLMMessage
from ai.llm.factory import get_llm
from ai.llm.structured import extract_json_object, parse_json_object
from config.research_engine import resolve_role_model
from db import fusion as fusion_db
from db.base import DEFAULT_DB_PATH
from db.idea_outcomes import _validate_long_plan, _validate_short_plan, register_idea
from db.lane_stats import bump
from db.llm_spend import lane_spent, record_spend
from signals.dossier import build_and_store_dossier

logger = logging.getLogger(__name__)

_ET = ZoneInfo("America/New_York")

# llm_spend lanes (each has its own daily ceiling). lane_stats meters use one lane.
_LANE_T1 = "t1"
_LANE_V1 = "reason_v1"
_LANE_STATS = "fusion_reason"

# The shadow class-template horizon. horizon_return grades entry_ref → close at a fixed
# horizon (Phase 1's template mechanism); 2 trading days is the frozen v1 estimand.
_TEMPLATE_HORIZON = "2d"

# v1 horizon contract: whole trading days ≥ 1 ([ADR-3]). Stricter than the ledger guard
# on purpose — the prompt asks for "Nd" and sub-1d must never reach register_idea.
_HORIZON_RE = re.compile(r"^\s*(\d+)\s*d\s*$", re.IGNORECASE)


# --------------------------------------------------------------------- LLM contracts


class T1Candidate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    symbol: str
    investigate: bool
    null_hypothesis: str
    angle_to_check: str


class T1Batch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    candidates: list[T1Candidate]


class EntryZone(BaseModel):
    model_config = ConfigDict(extra="forbid")
    low: str
    high: str


class V1NoIdea(BaseModel):
    """The explicit no-idea variant — an event is a fact, an idea is a plan; a no_idea
    answer annotates reason_runs and NEVER registers an idea."""

    model_config = ConfigDict(extra="forbid")
    no_idea: Literal[True]
    reason: str

    @field_validator("reason")
    @classmethod
    def _reason_nonempty(cls, value: str) -> str:
        if not str(value).strip():
            raise ValueError("no_idea.reason must be non-empty")
        return value


class V1Plan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    symbol: str
    direction: Literal["long", "short"]
    entry_zone: EntryZone
    stop: str
    targets: list[str]
    horizon: str
    confidence: Literal["low", "medium", "high"]
    thesis: str
    bear_case: str
    null_hypothesis_result: str

    @model_validator(mode="after")
    def _check_contract(self) -> V1Plan:
        for name in ("thesis", "bear_case", "null_hypothesis_result"):
            if not str(getattr(self, name)).strip():
                raise ValueError(f"{name} must be non-empty")
        match = _HORIZON_RE.match(self.horizon)
        if match is None or int(match.group(1)) < 1:
            raise ValueError("horizon must be whole trading days >= 1, e.g. '2d' or '5d'")
        # Reuse the ledger's EXACT geometry validators (no duplication, no drift): a
        # geometrically-invalid plan is a contract failure → repair retry, never a silent
        # rejection at register_idea's door.
        validate = _validate_short_plan if self.direction == "short" else _validate_long_plan
        geometry = validate(
            {"low": self.entry_zone.low, "high": self.entry_zone.high},
            self.stop,
            list(self.targets),
        )
        if geometry is None:
            raise ValueError(
                "plan geometry invalid (long: stop<entry.low<=entry.high<targets ascending; "
                "short mirrored)"
            )
        return self


# ------------------------------------------------------------------------- prompts

_T1_SYSTEM = (
    "You are the T1 triage stage of a trading-idea investigator. For each candidate "
    "evidence stack you receive its symbol and a deterministic claim matrix (facts only). "
    "Decide whether the stack is worth a full investigation.\n"
    'For EACH candidate output one object: {"symbol", "investigate" (true/false), '
    '"null_hypothesis", "angle_to_check"}.\n'
    "- null_hypothesis: the SKEPTICAL default that would make this boring "
    "(e.g. 'likely a hedge/roll — check OI and the expiry ladder'); it forces the next "
    "stage to actually check rather than template-fill.\n"
    "- angle_to_check: the single most decisive thing to verify.\n"
    'Output a SINGLE JSON object {"candidates": [ ... ]} and NOTHING else. '
    "NEVER output probabilities, win rates, or percentages of success."
)

_V1_SYSTEM = (
    "You are REASON v1: turn ONE investigated evidence stack into a single, fully-planned "
    "trade idea, OR decline. You receive the deterministic claim matrix (facts), the list "
    "of claim ids, and T1's null hypothesis you must confront.\n"
    "If you propose an idea, output exactly:\n"
    '{"symbol", "direction" (long|short), "entry_zone": {"low","high"}, "stop", '
    '"targets": [1-3 prices], "horizon" (whole trading days, e.g. "2d","5d"), '
    '"confidence" (low|medium|high), "thesis", "bear_case", "null_hypothesis_result"}.\n'
    "Geometry: long → stop below entry.low, targets above entry.high ascending; "
    "short → mirrored.\n"
    'If there is no good idea, output exactly: {"no_idea": true, "reason": "..."}.\n'
    "Rules: cite claim ids for any factual assertion; address T1's null hypothesis "
    "explicitly in null_hypothesis_result; bear_case must be non-empty. Output JSON only. "
    "NEVER output a numeric probability, win rate, or percentage chance of success."
)

_REPAIR_PROMPT = (
    "Your previous response did not satisfy the required JSON contract.\n"
    "Error: {error}\n"
    "Reply with ONLY the corrected JSON object — no prose, no code fences."
)


# ------------------------------------------------------------------------- helpers


@dataclass
class _PassCtx:
    """Per-pass plumbing shared by the candidate processors."""

    market_day: str
    as_of_iso: str
    llm_reason: Any
    v1_model: str
    db_path: Path
    counters: dict[str, float]


def _model_name(llm: Any) -> str:
    return str(getattr(llm, "_model", "") or getattr(llm, "model", "") or getattr(llm, "provider", ""))


def _prompt_chars(messages: list[LLMMessage]) -> int:
    return sum(len(m.content or "") for m in messages)


def _normalize_now(now: datetime | None) -> datetime:
    if now is None:
        return datetime.now(_ET)
    if now.tzinfo is None:  # naive stamps are UTC by house convention, never ET
        return now.replace(tzinfo=timezone.utc)
    return now


def _chunks(seq: list[Any], size: int) -> list[list[Any]]:
    size = max(1, int(size))
    return [seq[i : i + size] for i in range(0, len(seq), size)]


def _validate(raw: str, model: type[BaseModel]) -> tuple[bool, Any, str]:
    """(ok, parsed, error). error is the specific validation message to echo on repair."""
    data = parse_json_object(raw, prefer_keys=set(model.model_fields))
    if data is None:
        return (False, None, "no JSON object found in the response")
    try:
        return (True, model.model_validate(data), "")
    except ValidationError as exc:
        return (False, None, str(exc))


async def _acomplete_timed(
    llm: Any, messages: list[LLMMessage], *, max_tokens: int
) -> tuple[bool, str, int]:
    """(timed_out, raw, latency_ms). Wraps acomplete in asyncio.wait_for(timeout)."""
    started = time.monotonic()
    try:
        raw = await asyncio.wait_for(
            llm.acomplete(messages, max_tokens=max_tokens),
            timeout=_cfg.REASON_CALL_TIMEOUT_S,
        )
        return (False, raw or "", int((time.monotonic() - started) * 1000))
    except (asyncio.TimeoutError, TimeoutError):
        return (True, "", int((time.monotonic() - started) * 1000))


async def _structured_pass(
    llm: Any,
    messages: list[LLMMessage],
    model: type[BaseModel],
    *,
    max_tokens: int,
    alt_model: type[BaseModel] | None = None,
    alt_status: str = "alt",
) -> tuple[str, Any, int, int]:
    """One structured completion with the repair ladder.

    Returns ``(status, parsed, latency_ms, completion_chars)`` where status is one of
    ``{ok, <alt_status>, parse_fail, refusal, timeout}``. ``alt_model`` (v1's no_idea) is a
    terminal declaration tried WITHOUT a repair turn.

    Refusal vs parse_fail (zero and dead are different from garbage): an empty/whitespace
    completion, or a non-empty completion with no JSON object at all, is a ``refusal`` (the
    model said nothing / declined in prose). A JSON object that is present but malformed or
    schema-invalid even after the repair turn is a ``parse_fail``.
    """
    timed_out, raw, latency = await _acomplete_timed(llm, messages, max_tokens=max_tokens)
    if timed_out:
        return ("timeout", None, latency, 0)
    if not raw.strip() or extract_json_object(raw) is None:
        return ("refusal", None, latency, len(raw))
    if alt_model is not None:
        ok_alt, parsed_alt, _ = _validate(raw, alt_model)
        if ok_alt:
            return (alt_status, parsed_alt, latency, len(raw))
    ok, parsed, error = _validate(raw, model)
    if ok:
        return ("ok", parsed, latency, len(raw))

    # ONE repair retry, the specific error echoed. Done via acomplete (NOT
    # parse_with_retry, whose retry path runs a SYNC completion) so the retry stays on the
    # async path and inside the timeout wrapper — no sync LLM call on the event loop.
    retry_messages = [
        *messages,
        LLMMessage(role="assistant", content=raw),
        LLMMessage(role="user", content=_REPAIR_PROMPT.format(error=error[:800])),
    ]
    timed_out2, raw2, latency2 = await _acomplete_timed(llm, retry_messages, max_tokens=max_tokens)
    total = latency + latency2
    if timed_out2:
        return ("timeout", None, total, len(raw))
    if alt_model is not None:
        ok_alt2, parsed_alt2, _ = _validate(raw2, alt_model)
        if ok_alt2:
            return (alt_status, parsed_alt2, total, len(raw2))
    ok2, parsed2, _ = _validate(raw2, model)
    if ok2:
        return ("ok", parsed2, total, len(raw2))
    return ("parse_fail", None, total, len(raw2))


def _t1_messages(
    batch: list[dict[str, Any]], dossiers: dict[str, dict[str, Any]]
) -> tuple[list[LLMMessage], int]:
    blocks = []
    for episode in batch:
        dossier = dossiers.get(str(episode["id"]), {})
        summary = dossier.get("summary") or "(no claims)"
        blocks.append(f"Candidate {episode.get('symbol') or '?'}:\n{summary}")
    messages = [
        LLMMessage(role="system", content=_T1_SYSTEM),
        LLMMessage(role="user", content="\n\n".join(blocks)),
    ]
    return messages, _prompt_chars(messages)


def _v1_messages(
    episode: dict[str, Any], candidate: T1Candidate, dossier: dict[str, Any]
) -> tuple[list[LLMMessage], int]:
    summary = dossier.get("summary") or "(no claims)"
    claim_ids = ", ".join(str(c.get("claim_id")) for c in dossier.get("claims") or [])
    user = (
        f"Symbol: {episode.get('symbol') or '?'}\n"
        f"T1 null hypothesis: {candidate.null_hypothesis}\n"
        f"Angle to check: {candidate.angle_to_check}\n"
        f"Claim ids: {claim_ids or '(none)'}\n\n"
        f"Claim matrix:\n{summary}"
    )
    messages = [
        LLMMessage(role="system", content=_V1_SYSTEM),
        LLMMessage(role="user", content=user),
    ]
    return messages, _prompt_chars(messages)


def _reason_row(
    episode: dict[str, Any], ctx: _PassCtx, **fields: Any
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "episode_id": str(episode["id"]),
        "market_day": ctx.market_day,
        "symbol": str(episode.get("symbol") or ""),
        "created_at": ctx.as_of_iso,
    }
    row.update(fields)
    return row


def _template_direction(episode: dict[str, Any]) -> str:
    """[ADR-6] the class template follows the STACK's aligned direction — the grader is
    fully direction-aware (signed ret, direction-consistent alpha benchmark), and a
    bear-aligned dismissal graded long would invert the sign of the dismissal-cohort
    report. 'mixed' (no aligned direction) defaults long: arbitrary but counted — slice
    by direction_state via the episode join when reading the cohort."""
    return "short" if "bear" in str(episode.get("direction_state") or "") else "long"


def _register_template_shadow(
    origin: str, episode: dict[str, Any], as_of_iso: str, db_path: Path
) -> tuple[int | None, str]:
    """Register a horizon_return CLASS-TEMPLATE shadow (synthetic). The anchor price is the
    only thing that can be missing — never fabricate one. Returns (idea_id|None, note)."""
    anchor = str(episode.get("anchor_price") or "").strip()
    if not anchor:
        return (None, "no_anchor_price")
    idea_id = register_idea(
        origin=origin,
        signal_family="fusion",
        symbol=str(episode.get("symbol") or ""),
        direction=_template_direction(episode),
        entry_zone=None,
        stop=None,
        targets=None,
        grading_basis="horizon_return",
        entry_ref_price=anchor,
        plan_horizon=_TEMPLATE_HORIZON,
        as_of=as_of_iso,
        synthetic=1,
        episode_id=str(episode["id"]),
        dossier_ref=str(episode["id"]),
        db_path=db_path,
    )
    return (idea_id, "registered" if idea_id is not None else "dedup_or_rejected")


def _register_v1_idea(
    episode: dict[str, Any], plan: V1Plan, null_hyp: str, as_of_iso: str, db_path: Path
) -> int | None:
    """Register a full REASON v1 plan. Symbol is the EPISODE's (ground truth), never the
    LLM's echoed field. thesis/bear_case pass register_idea's fabricated-probability wall."""
    return register_idea(
        origin="reason_v1",
        signal_family="fusion",
        symbol=str(episode.get("symbol") or ""),
        direction=plan.direction,
        entry_zone={"low": plan.entry_zone.low, "high": plan.entry_zone.high},
        stop=plan.stop,
        targets=list(plan.targets),
        thesis=plan.thesis,
        bear_case=plan.bear_case,
        confidence_label=plan.confidence,
        plan_horizon=plan.horizon,
        grading_basis="plan",
        as_of=as_of_iso,
        source_ref={
            "kind": "fusion",
            "id": str(episode["id"]),
            "lane": "reason_v1",
            "null_hypothesis": null_hyp,
        },
        episode_id=str(episode["id"]),
        dossier_ref=str(episode["id"]),
        synthetic=0,
        db_path=db_path,
    )


def _flush_counters(counters: dict[str, float], market_day: str, db_path: Path) -> None:
    for key, value in counters.items():
        if value:
            bump(_LANE_STATS, key, value, date=market_day, db_path=db_path)


# ---------------------------------------------------------------- candidate processing


async def _process_candidate(
    episode: dict[str, Any],
    candidate: T1Candidate,
    dossier: dict[str, Any],
    ctx: _PassCtx,
) -> None:
    counters = ctx.counters
    null_hyp = candidate.null_hypothesis
    angle = candidate.angle_to_check
    episode_id = str(episode["id"])

    # ---- T1 dismissal → 100% shadow-registered class template (the highest-selection-bias
    # boundary in the system). Distinct origin (estimand discipline). No v1 call. ----
    if not candidate.investigate:
        shadow_id, note = await asyncio.to_thread(
            _register_template_shadow, "t1_dismissal", episode, ctx.as_of_iso, ctx.db_path
        )
        counters["t1_dismissed"] += 1
        if shadow_id is None and note == "no_anchor_price":
            counters["t1_dismissal_no_ref"] += 1
        await asyncio.to_thread(
            fusion_db.put_reason_run,
            _reason_row(
                episode, ctx, t1_status="ok", t1_investigate=0,
                null_hypothesis=null_hyp, angle_to_check=angle,
                v1_status=None, idea_id=shadow_id, no_idea=0,
                detail_json={"stage": "t1_dismissal", "shadow": note},
            ),
            db_path=ctx.db_path,
        )
        return

    counters["t1_investigate"] += 1

    # ---- v1 budget gate. Denied AFTER T1 marked investigate → NOT a model failure, so NO
    # template shadow. The episode is still marked processed: the v1 budget is monotonic
    # within a day, so a later pass could never free it (documented choice, brief §6). ----
    v1_spent = await asyncio.to_thread(lane_spent, ctx.market_day, _LANE_V1, db_path=ctx.db_path)
    if v1_spent >= _cfg.REASON_V1_DAILY_BUDGET:
        await asyncio.to_thread(
            record_spend, ctx.market_day, _LANE_V1, "budget_denied",
            model=ctx.v1_model, episode_id=episode_id, detail="v1 over budget",
            db_path=ctx.db_path,
        )
        counters["v1_budget_denied"] += 1
        await asyncio.to_thread(
            fusion_db.put_reason_run,
            _reason_row(
                episode, ctx, t1_status="ok", t1_investigate=1,
                null_hypothesis=null_hyp, angle_to_check=angle,
                v1_status="budget_denied", idea_id=None, no_idea=0,
                detail_json={"stage": "v1_budget_denied"},
            ),
            db_path=ctx.db_path,
        )
        return

    messages, prompt_chars = _v1_messages(episode, candidate, dossier)
    status, parsed, latency, comp_chars = await _structured_pass(
        ctx.llm_reason, messages, V1Plan,
        max_tokens=_cfg.REASON_V1_MAX_TOKENS, alt_model=V1NoIdea, alt_status="no_idea",
    )
    # persist-before-use: the spend row lands before we act on the parsed content. no_idea
    # is a SUCCESSFUL call (llm_spend status 'ok'); the richer 'no_idea' lives in reason_runs.
    await asyncio.to_thread(
        record_spend, ctx.market_day, _LANE_V1,
        ("ok" if status in ("ok", "no_idea") else status),
        model=ctx.v1_model, latency_ms=latency, prompt_chars=prompt_chars,
        completion_chars=comp_chars, episode_id=episode_id, detail=f"v1 {status}",
        db_path=ctx.db_path,
    )
    counters["v1_calls"] += 1

    if status == "no_idea":
        counters["v1_no_idea"] += 1
        await asyncio.to_thread(
            fusion_db.put_reason_run,
            _reason_row(
                episode, ctx, t1_status="ok", t1_investigate=1,
                null_hypothesis=null_hyp, angle_to_check=angle,
                v1_status="no_idea", idea_id=None, no_idea=1,
                detail_json={"stage": "v1_no_idea", "reason": getattr(parsed, "reason", "")},
            ),
            db_path=ctx.db_path,
        )
        return

    if status in ("parse_fail", "refusal", "timeout"):
        counters[f"v1_{status}"] += 1
        shadow_id, note = await asyncio.to_thread(
            _register_template_shadow,
            "reason_v1_failed_template", episode, ctx.as_of_iso, ctx.db_path,
        )
        if shadow_id is not None:
            counters["v1_failed_template"] += 1
        elif note == "no_anchor_price":
            counters["v1_failed_template_no_ref"] += 1
        await asyncio.to_thread(
            fusion_db.put_reason_run,
            _reason_row(
                episode, ctx, t1_status="ok", t1_investigate=1,
                null_hypothesis=null_hyp, angle_to_check=angle,
                v1_status=status, idea_id=shadow_id, no_idea=0,
                detail_json={"stage": "v1_failed_template", "shadow": note},
            ),
            db_path=ctx.db_path,
        )
        return

    # status == "ok": a full, contract-valid plan.
    idea_id = await asyncio.to_thread(
        _register_v1_idea, episode, parsed, null_hyp, ctx.as_of_iso, ctx.db_path
    )
    if idea_id is not None:
        counters["v1_ok"] += 1
        await asyncio.to_thread(
            fusion_db.put_reason_run,
            _reason_row(
                episode, ctx, t1_status="ok", t1_investigate=1,
                null_hypothesis=null_hyp, angle_to_check=angle,
                v1_status="ok", idea_id=idea_id, no_idea=0,
                detail_json={
                    "stage": "v1_ok",
                    "confidence": parsed.confidence,
                    "horizon": parsed.horizon,
                    "null_hypothesis_result": parsed.null_hypothesis_result,
                },
            ),
            db_path=ctx.db_path,
        )
        return

    # register_idea refused a shape-valid plan — the only remaining causes are a fabricated
    # probability in thesis/bear_case (the 07-08 leak shape, walled at register_idea) or a
    # residual geometry edge. That is a model failure, so shadow it against the class template
    # (distinct origin) exactly like a parse failure — never a silent death at the ledger door.
    counters["v1_register_rejected"] += 1
    shadow_id, note = await asyncio.to_thread(
        _register_template_shadow,
        "reason_v1_failed_template", episode, ctx.as_of_iso, ctx.db_path,
    )
    if shadow_id is not None:
        counters["v1_failed_template"] += 1
    elif note == "no_anchor_price":
        counters["v1_failed_template_no_ref"] += 1
    await asyncio.to_thread(
        fusion_db.put_reason_run,
        _reason_row(
            episode, ctx, t1_status="ok", t1_investigate=1,
            null_hypothesis=null_hyp, angle_to_check=angle,
            v1_status="register_rejected", idea_id=shadow_id, no_idea=0,
            detail_json={"stage": "v1_register_rejected", "shadow": note},
        ),
        db_path=ctx.db_path,
    )


# -------------------------------------------------------------------------- entry point


async def run_reason_pass(
    *,
    db_path: Path = DEFAULT_DB_PATH,
    now: datetime | None = None,
    llm_triage: Any | None = None,
    llm_reason: Any | None = None,
) -> dict[str, Any]:
    """Process today's pending investigation episodes (job-shaped for C5).

    Pending = ``fusion_investigations`` shadow_investigate rows for today's market day with
    no ``reason_runs`` row. Each is dossier-built (C2), T1-triaged in batches of ≤8, then
    every investigate=True candidate gets one REASON v1 call. Returns a counters summary.
    """
    # DARK: the arming seam. A dark lane must not even write counters on every poll — return.
    if not _cfg.REASON_EMIT_ENABLED:
        return {"disabled": True}

    now = _normalize_now(now)
    market_day = now.astimezone(_ET).date().isoformat()
    as_of_iso = now.isoformat()

    await asyncio.to_thread(fusion_db.init_fusion_tables, db_path)
    pending = await asyncio.to_thread(
        fusion_db.list_pending_reason_episodes, market_day, db_path=db_path
    )
    counters: dict[str, float] = defaultdict(float)
    if not pending:
        return {"disabled": False, "market_day": market_day, "pending": 0, "counters": {}}

    if llm_triage is None:
        llm_triage = get_llm(model=resolve_role_model("triage"))
    if llm_reason is None:
        llm_reason = get_llm(model=resolve_role_model("reason"))
    t1_model = _model_name(llm_triage)
    ctx = _PassCtx(
        market_day=market_day, as_of_iso=as_of_iso, llm_reason=llm_reason,
        v1_model=_model_name(llm_reason), db_path=db_path, counters=counters,
    )

    # Build every pending dossier up front (deterministic, store-only reads).
    dossiers: dict[str, dict[str, Any]] = {}
    for episode in pending:
        dossier = await asyncio.to_thread(
            build_and_store_dossier, episode, db_path=db_path, now=now
        )
        dossiers[str(episode["id"])] = dossier

    for batch in _chunks(pending, _cfg.REASON_T1_BATCH_MAX):
        # ---- T1 budget gate. A denial is NOT a decision about the episodes — record the
        # denial row and leave the batch PENDING for a later pass (T1 budget caps retries). ----
        t1_spent = await asyncio.to_thread(lane_spent, market_day, _LANE_T1, db_path=db_path)
        if t1_spent >= _cfg.T1_DAILY_BUDGET:
            await asyncio.to_thread(
                record_spend, market_day, _LANE_T1, "budget_denied",
                model=t1_model, detail=f"t1 batch denied ({len(batch)} candidates)",
                db_path=db_path,
            )
            counters["t1_budget_denied"] += 1
            continue

        messages, prompt_chars = _t1_messages(batch, dossiers)
        status, parsed, latency, comp_chars = await _structured_pass(
            llm_triage, messages, T1Batch, max_tokens=_cfg.REASON_T1_MAX_TOKENS
        )
        # persist-before-use: the T1 spend row + status commit before any candidate is acted on.
        await asyncio.to_thread(
            record_spend, market_day, _LANE_T1, status,
            model=t1_model, latency_ms=latency, prompt_chars=prompt_chars,
            completion_chars=comp_chars, detail=f"batch of {len(batch)}",
            db_path=db_path,
        )
        counters["t1_calls"] += 1
        if status != "ok":
            counters[f"t1_{status}"] += 1
            # No triage result → the batch stays pending (no reason_runs rows), retried later.
            continue

        by_symbol = {c.symbol.strip().upper(): c for c in parsed.candidates}
        for episode in batch:
            candidate = by_symbol.get(str(episode.get("symbol") or "").strip().upper())
            if candidate is None:
                # The model omitted this candidate — leave it pending for a later pass.
                counters["t1_missing_candidate"] += 1
                continue
            await _process_candidate(episode, candidate, dossiers[str(episode["id"])], ctx)

    await asyncio.to_thread(_flush_counters, dict(counters), market_day, db_path)
    return {
        "disabled": False,
        "market_day": market_day,
        "pending": len(pending),
        "counters": dict(counters),
    }
