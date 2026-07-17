"""The silence-killer layer — rate alarms, a canary, and conservation checks.

A chain whose job is pushing things looks healthy pushing nothing; this module
makes that impossible. Every rule reads the persisted ``lane_stats`` counters
(never in-memory state a restart loses), compares against a calendar-aware
baseline, and pages once per (rule, day). The canary physically exercises the
push transport and asserts a delivery receipt; its absence is a critical page,
and an external cron (``tools/idea_engine_deadman.sh``) guards the case where
this in-process auditor is itself dead.

Pure rule functions + one async job body (``pipeline_audit_once``, registered
in ``api/main.py`` at a 900s interval). All state lives in ``lane_stats``.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from db import lane_stats
from db.base import DEFAULT_DB_PATH
from db.push_deliveries import has_sent_delivery, record_delivery
from signals.notify import notify
from trading.market_calendar import is_trading_day

logger = logging.getLogger(__name__)

_ET = ZoneInfo("America/New_York")

# --- Rate-rule thresholds (law, not deployment toggles — kept local) ----------
PARSE_FAIL_RATE_MAX = 0.5  # conviction pass parse-fail fraction that is alarming
PARSE_FAIL_STREAK_DAYS = 2  # ...for this many consecutive trading days
SETUP_SCAN_SILENT_DAYS = 3  # setup_scan registered==0 for N consecutive weekdays
IDEA_TRACKER_SILENT_DAYS = 2  # tracker graded+examined==0 for N market days
SILENCE_LIFE_LOOKBACK = 15  # a silent lane only alarms if it was alive this recently

# --- Canary timing ------------------------------------------------------------
CANARY_LAUNCH_HOUR_ET = 15  # launch the daily canary at/after 15:00 ET
CANARY_VERIFY_AFTER_MIN = 30  # receipt asserted within 30 min → verify from 15:30

_AUDIT_LANE = "pipeline_audit"  # where alarm-dedup counters live
_CANARY_LANE = "canary"


# ---------------------------------------------------------------------------
# Calendar helpers
# ---------------------------------------------------------------------------
def _trailing_trading_days(today: date, n: int) -> list[date]:
    """The ``n`` most recent trading days ending at ``today`` (inclusive)."""
    out: list[date] = []
    d = today
    while len(out) < n:
        if is_trading_day(d):
            out.append(d)
        d -= timedelta(days=1)
    return out


def _lane_alive_recently(
    lane: str, counter: str, *, db_path: Path
) -> bool:
    """True if the lane produced any non-zero value in its recent history.

    Guards the zero-count rules: a fresh install (no rows ever) must not read as
    a dead lane. Only a lane that was demonstrably alive and then went silent
    alarms.
    """
    points = lane_stats.series(lane, counter, SILENCE_LIFE_LOOKBACK, db_path=db_path)
    return any(value > 0 for _, value in points)


# ---------------------------------------------------------------------------
# Pure rate rules — each returns an alarm message, or None when healthy/unknown
# ---------------------------------------------------------------------------
def rule_conviction_parse_fail(today: date, db_path: Path) -> str | None:
    """Conviction parse-fail rate > 0.5 for 2 consecutive trading days."""
    for d in _trailing_trading_days(today, PARSE_FAIL_STREAK_DAYS):
        counters = lane_stats.day("conviction_pass", d.isoformat(), db_path=db_path)
        fails = counters.get("parse_fail", 0.0)
        ok = counters.get("ok", 0.0)
        total = fails + ok
        if total <= 0:
            return None  # no attempts that day → nothing to conclude
        if fails / total <= PARSE_FAIL_RATE_MAX:
            return None
    return (
        f"conviction parse-fail rate > {PARSE_FAIL_RATE_MAX:.0%} for "
        f"{PARSE_FAIL_STREAK_DAYS} consecutive trading days — the parser lane is rotting"
    )


def rule_setup_scan_silent(today: date, db_path: Path) -> str | None:
    """setup_scan registered == 0 for 3 consecutive weekdays (was recently alive)."""
    for d in _trailing_trading_days(today, SETUP_SCAN_SILENT_DAYS):
        counters = lane_stats.day("setup_scan", d.isoformat(), db_path=db_path)
        if counters.get("registered", 0.0) > 0:
            return None
    if not _lane_alive_recently("setup_scan", "registered", db_path=db_path):
        return None  # never produced — a fresh lane, not a dead one
    return (
        f"setup_scan registered 0 ideas for {SETUP_SCAN_SILENT_DAYS} consecutive "
        "trading days — the nightly scan lane is silent"
    )


def rule_idea_tracker_silent(today: date, db_path: Path) -> str | None:
    """idea_tracker graded+examined == 0 for 2 market days (was recently alive)."""
    for d in _trailing_trading_days(today, IDEA_TRACKER_SILENT_DAYS):
        counters = lane_stats.day("idea_tracker", d.isoformat(), db_path=db_path)
        if counters.get("graded", 0.0) + counters.get("examined", 0.0) > 0:
            return None
    alive = _lane_alive_recently(
        "idea_tracker", "graded", db_path=db_path
    ) or _lane_alive_recently("idea_tracker", "examined", db_path=db_path)
    if not alive:
        return None
    return (
        f"idea_tracker graded+examined 0 for {IDEA_TRACKER_SILENT_DAYS} market days "
        "— the grader lane is dead"
    )


def rule_path_a_register_failures(today: date, db_path: Path) -> str | None:
    """Any path_a_register failure today (registration is fail-soft, so a failure
    is pure signal, never noise)."""
    counters = lane_stats.day("path_a_register", today.isoformat(), db_path=db_path)
    failures = counters.get("failures", 0.0)
    if failures <= 0:
        return None
    return (
        f"path_a_register failures = {failures:.0f} today — a proposal ledger write "
        "is failing (proposals still fire; science is losing rows)"
    )


def rule_max_open_age(today: date, db_path: Path) -> str | None:
    """Oldest un-graded idea older than IDEA_TTL + RESOLVE_TTL + 3 → dead grader."""
    from research.idea_tracker import IDEA_TTL_DAYS, RESOLVE_TTL_DAYS

    ceiling = IDEA_TTL_DAYS + RESOLVE_TTL_DAYS + 3
    counters = lane_stats.day("idea_tracker", today.isoformat(), db_path=db_path)
    max_age = counters.get("max_open_age_days", 0.0)
    if max_age <= ceiling:
        return None
    return (
        f"oldest open idea is {max_age:.0f} trading days (> {ceiling}) — the grader "
        "is not resolving matured ideas"
    )


def conservation_gap(today: date, db_path: Path) -> str | None:
    """Conservation check v1: every assessed nightly-scan candidate must reach
    exactly one counted disposition. An imbalance is a new uncounted drop path."""
    counters = lane_stats.day("setup_scan", today.isoformat(), db_path=db_path)
    assessed = counters.get("candidates_assessed", 0.0)
    if assessed <= 0:
        return None  # no scan today / counters not yet wired — nothing to reconcile
    accounted = (
        counters.get("structure", 0.0)
        + counters.get("conviction", 0.0)
        + counters.get("vetoed", 0.0)
        + counters.get("rejected_by_validation", 0.0)
    )
    if abs(accounted - assessed) < 1e-9:
        return None
    return (
        f"nightly scan conservation imbalance: {accounted:.0f} accounted != "
        f"{assessed:.0f} candidates_assessed — a new uncounted drop path"
    )


RATE_RULES: list[tuple[str, object]] = [
    ("conviction_parse_fail", rule_conviction_parse_fail),
    ("setup_scan_silent", rule_setup_scan_silent),
    ("idea_tracker_silent", rule_idea_tracker_silent),
    ("path_a_register_failures", rule_path_a_register_failures),
    ("max_open_age", rule_max_open_age),
]


# ---------------------------------------------------------------------------
# Alarm firing — deduped once per (rule, ET day) via a lane_stats counter
# ---------------------------------------------------------------------------
async def _fire_alarm(
    rule_id: str,
    today: date,
    db_path: Path,
    *,
    severity: str,
    title: str,
    body: str,
    kind: str = "ops_alert",
) -> None:
    fired = lane_stats.day(_AUDIT_LANE, today.isoformat(), db_path=db_path).get(
        f"alarm_fired:{rule_id}", 0.0
    )
    if fired > 0:
        return  # already paged for this rule today
    await notify(
        kind=kind,
        title=title,
        body=body,
        severity=severity,
        push=True,
        group_key=f"pipeline_audit_{rule_id}",
        db_path=db_path,
    )
    lane_stats.bump(_AUDIT_LANE, f"alarm_fired:{rule_id}", db_path=db_path)


async def _run_rate_rules(today: date, db_path: Path) -> None:
    for rule_id, fn in RATE_RULES:
        try:
            msg = fn(today, db_path)  # type: ignore[operator]
        except Exception:
            logger.exception("pipeline_audit rule %s check failed", rule_id)
            continue
        if msg:
            await _fire_alarm(
                rule_id,
                today,
                db_path,
                severity="warning",
                title=f"Idea Engine lane alarm: {rule_id}",
                body=msg,
            )


# ---------------------------------------------------------------------------
# Canary — traverse register → push → delivery receipt, once per trading day
# ---------------------------------------------------------------------------
def _canary_id(today: date) -> str:
    return f"canary:{today.isoformat()}"


async def _canary_launch(today: date, db_path: Path) -> None:
    canary_id = _canary_id(today)

    # Register a synthetic idea in the canary namespace (fail-soft, lazy import —
    # register_idea is a sibling slice). Summaries filter WHERE synthetic = 0, so
    # this row is invisible to every calibration/summary read path.
    try:
        from db.idea_outcomes import register_idea

        register_idea(
            origin="canary",
            signal_family="canary",
            symbol="SPY",
            direction="long",
            entry_zone={"low": "1.00", "high": "1.01"},
            stop="0.90",
            targets=["1.10"],
            thesis="synthetic liveness canary",
            bear_case="synthetic",
            plan_horizon="5d",
            grading_basis="plan",
            synthetic=1,
            db_path=db_path,
        )
    except Exception:
        logger.exception("canary register_idea failed")

    # Send a real push and record its receipt on success. A missing receipt (no
    # device, push disabled, transport down) is exactly the failure the verify
    # pass turns into a critical page.
    sent = 0
    try:
        from signals.push import send_alert_push

        sent = await send_alert_push(
            "Idea Engine canary",
            f"Daily liveness canary {canary_id}",
            data={"type": "ops_canary", "canary_id": canary_id},
            db_path=db_path,
        )
    except Exception:
        logger.exception("canary push failed")

    if sent and sent > 0:
        try:
            record_delivery(
                canary_id, "founder", "ops_canary", "sent", db_path=db_path
            )
        except Exception:
            logger.exception("canary receipt record failed")

    lane_stats.bump(_CANARY_LANE, "launched", db_path=db_path)
    lane_stats.bump(
        _CANARY_LANE, "sent" if (sent and sent > 0) else "send_failed", db_path=db_path
    )


async def _canary_verify(today: date, db_path: Path) -> None:
    launched = lane_stats.day(_CANARY_LANE, today.isoformat(), db_path=db_path).get(
        "launched", 0.0
    )
    if launched <= 0:
        return  # never launched today (e.g. a restart after 15:30) — nothing to assert
    if has_sent_delivery(_canary_id(today), db_path=db_path):
        return  # receipt present — the transport round-tripped
    await _fire_alarm(
        "canary_receipt_missing",
        today,
        db_path,
        severity="critical",
        title="Idea Engine canary receipt missing",
        body=(
            f"No push_deliveries receipt for {_canary_id(today)} within "
            f"{CANARY_VERIFY_AFTER_MIN} min of launch — the push transport may be dead."
        ),
        kind="ops_canary",
    )


async def _run_canary(now_et: datetime, db_path: Path) -> None:
    today = now_et.date()
    if not is_trading_day(today):
        return  # no canary on weekends/holidays — its absence is expected
    minutes = now_et.hour * 60 + now_et.minute
    launch_at = CANARY_LAUNCH_HOUR_ET * 60
    launched = lane_stats.day(_CANARY_LANE, today.isoformat(), db_path=db_path).get(
        "launched", 0.0
    )
    if minutes >= launch_at and launched <= 0:
        await _canary_launch(today, db_path)
    if minutes >= launch_at + CANARY_VERIFY_AFTER_MIN:
        await _canary_verify(today, db_path)


async def _run_conservation_check(today: date, db_path: Path) -> None:
    try:
        msg = conservation_gap(today, db_path)
    except Exception:
        logger.exception("pipeline_audit conservation check failed")
        return
    if msg:
        await _fire_alarm(
            "conservation_v1",
            today,
            db_path,
            severity="warning",
            title="Idea Engine conservation imbalance",
            body=msg,
        )


# ---------------------------------------------------------------------------
# Job body
# ---------------------------------------------------------------------------
async def run_audit(now_et: datetime, db_path: Path = DEFAULT_DB_PATH) -> None:
    """One audit pass: rate rules, conservation check, canary. Each section is
    independently fail-soft so one dead check never blinds the others."""
    try:
        await _run_rate_rules(now_et.date(), db_path)
    except Exception:
        logger.exception("pipeline_audit rate rules failed")
    try:
        await _run_conservation_check(now_et.date(), db_path)
    except Exception:
        logger.exception("pipeline_audit conservation section failed")
    try:
        await _run_canary(now_et, db_path)
    except Exception:
        logger.exception("pipeline_audit canary failed")


async def pipeline_audit_once() -> None:
    await run_audit(datetime.now(_ET), DEFAULT_DB_PATH)
