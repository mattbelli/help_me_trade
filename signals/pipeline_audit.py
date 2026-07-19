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
from statistics import median
from zoneinfo import ZoneInfo

from config.idea_engine import (
    ALARM_EXPECTED_LANES,
    ALARM_NEVER_WORKED_GRACE_DAYS,
    ALARM_RATE_MEDIAN_DAYS,
)
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
RATE_BASELINE_MIN_DAYS = 3
RATE_COLLAPSE_FRACTION = 0.5
_MAX_BASELINE_CALENDAR_DAYS = 3650

# attempt counters (the lane tried) → output counters (ANY >0 proves wiring).
# setup_scan's vetoed shadows count as output — a healthy scan can qualify zero
# recommended ideas for days (quiet ≠ dead, the same conflation the silent
# rules guard), but a wired one still lands vetoed registrations.
_EXPECTED_LANE_COUNTERS: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "setup_scan": (("ran",), ("registered", "vetoed")),
    "conviction_pass": (("ok", "parse_fail"), ("ok",)),
    "idea_tracker": (
        ("swept", "examined", "graded", "data_gap", "errors"),
        ("graded",),
    ),
    "path_a_register": (
        ("registered", "deduped_or_rejected", "failures"),
        ("registered",),
    ),
}

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


def _trailing_counter_values(
    lane: str, counter: str, today: date, *, db_path: Path
) -> list[float]:
    """Up to N prior observed trading-day values, newest first.

    A day with any counters for the lane is an observation; an absent target
    counter on such a day is zero. Completely absent days do not consume the
    history window.
    """
    values: list[float] = []
    d = today - timedelta(days=1)
    scanned = 0
    while (
        len(values) < ALARM_RATE_MEDIAN_DAYS
        and scanned < _MAX_BASELINE_CALENDAR_DAYS
    ):
        if is_trading_day(d):
            counters = lane_stats.day(lane, d.isoformat(), db_path=db_path)
            if counters:
                values.append(counters.get(counter, 0.0))
        d -= timedelta(days=1)
        scanned += 1
    return values


def _trailing_counter_median(
    lane: str, counter: str, today: date, *, db_path: Path
) -> float | None:
    """Trailing observed-day median for one persisted lane counter."""
    values = _trailing_counter_values(lane, counter, today, db_path=db_path)
    return float(median(values)) if values else None


def _collapsed_vs_baseline(
    lane: str, counter: str, today: date, *, db_path: Path
) -> tuple[bool, float | None]:
    values = _trailing_counter_values(lane, counter, today, db_path=db_path)
    if len(values) < RATE_BASELINE_MIN_DAYS:
        return False, None
    baseline = float(median(values))
    if not is_trading_day(today):
        # A lane can't "collapse today" on a day it was never expected to run —
        # without this, every rule with a formed baseline false-pages all
        # weekend (today's 0 vs the weekday median).
        return False, baseline
    current = lane_stats.day(lane, today.isoformat(), db_path=db_path).get(counter, 0.0)
    return baseline > 0 and current < baseline * RATE_COLLAPSE_FRACTION, baseline


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
    """setup_scan heartbeat collapsed vs its trailing observed-day median.

    Keys on the ``ran`` heartbeat, never on ``registered`` — a scan that runs
    healthily and qualifies 0 ideas is quiet, not dead (review finding: the
    two must never conflate)."""
    collapsed, baseline = _collapsed_vs_baseline(
        "setup_scan", "ran", today, db_path=db_path
    )
    if baseline is not None:
        if not collapsed:
            return None
        current = lane_stats.day(
            "setup_scan", today.isoformat(), db_path=db_path
        ).get("ran", 0.0)
        return (
            f"setup_scan run heartbeat collapsed to {current:.0f} today versus "
            f"trailing median {baseline:g} — the nightly scan lane may be dead"
        )

    # New-deploy floor: retain the old fixed streak until three observed
    # history days exist, so an unseeded install cannot page on day one.
    for d in _trailing_trading_days(today, SETUP_SCAN_SILENT_DAYS):
        counters = lane_stats.day("setup_scan", d.isoformat(), db_path=db_path)
        if counters.get("ran", 0.0) > 0:
            return None
    if not _lane_alive_recently("setup_scan", "ran", db_path=db_path):
        return None  # never ran — a fresh lane, not a dead one
    return (
        f"setup_scan did not run for {SETUP_SCAN_SILENT_DAYS} consecutive "
        "trading days — the nightly scan lane is dead"
    )


def rule_idea_tracker_silent(today: date, db_path: Path) -> str | None:
    """idea_tracker heartbeat collapsed vs its trailing observed-day median.

    Keys on the ``swept`` heartbeat (bumped once per completed sweep) — never on
    ``graded``, which is legitimately lumpy on 5-21 trading-day horizons (review
    HIGH: a healthy-but-quiet grader must not page as dead, and without a
    heartbeat a truly dead sweep is indistinguishable from a quiet one)."""
    collapsed, baseline = _collapsed_vs_baseline(
        "idea_tracker", "swept", today, db_path=db_path
    )
    if baseline is not None:
        if not collapsed:
            return None
        current = lane_stats.day(
            "idea_tracker", today.isoformat(), db_path=db_path
        ).get("swept", 0.0)
        return (
            f"idea_tracker sweep heartbeat collapsed to {current:.0f} today versus "
            f"trailing median {baseline:g} — the grader loop may not be running"
        )

    for d in _trailing_trading_days(today, IDEA_TRACKER_SILENT_DAYS):
        counters = lane_stats.day("idea_tracker", d.isoformat(), db_path=db_path)
        if counters.get("swept", 0.0) > 0:
            return None
    if not _lane_alive_recently("idea_tracker", "swept", db_path=db_path):
        return None
    return (
        f"idea_tracker sweep heartbeat absent for {IDEA_TRACKER_SILENT_DAYS} "
        "market days — the grader loop is not running"
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


def _has_lifetime_value(
    lane: str, counters: tuple[str, ...], *, db_path: Path
) -> bool:
    return any(
        lane_stats.series(lane, counter, 1_000_000, db_path=db_path)
        for counter in counters
    )


def _has_lifetime_output(
    lane: str, counters: tuple[str, ...], *, db_path: Path
) -> bool:
    return any(
        value > 0
        for counter in counters
        for _, value in lane_stats.series(lane, counter, 1_000_000, db_path=db_path)
    )


def _attempt_day_count(
    lane: str, counters: tuple[str, ...], *, db_path: Path
) -> int:
    """Distinct dates on which the lane recorded ANY attempt counter."""
    days: set[str] = set()
    for counter in counters:
        for day, _ in lane_stats.series(lane, counter, 1_000_000, db_path=db_path):
            days.add(day)
    return len(days)


def _engine_deploy_proxy(*, db_path: Path) -> date | None:
    """Earliest expected-lane attempt, used when no explicit deploy row exists.

    ``lane_stats.series`` is newest-first (ORDER BY date DESC), so the OLDEST
    observation is the LAST element — reading [0] here silently turned the
    proxy into "latest activity" and killed the grace path (review catch)."""
    observed: list[date] = []
    for lane in ALARM_EXPECTED_LANES:
        attempt_counters, _ = _EXPECTED_LANE_COUNTERS[lane]
        for counter in attempt_counters:
            points = lane_stats.series(lane, counter, 1_000_000, db_path=db_path)
            if points:
                try:
                    observed.append(date.fromisoformat(points[-1][0]))
                except ValueError:
                    continue
    return min(observed) if observed else None


def _trading_days_elapsed(start: date, end: date) -> int:
    elapsed = 0
    d = start + timedelta(days=1)
    while d <= end:
        if is_trading_day(d):
            elapsed += 1
        d += timedelta(days=1)
    return elapsed


def _never_worked_lanes(today: date, db_path: Path) -> list[tuple[str, tuple[str, ...]]]:
    """Expected lanes with zero lifetime output after enough evidence to judge.

    Two paths to "enough evidence": the lane ATTEMPTED on ≥ grace distinct days
    (so one quiet first day never pages — real-DB review catch: setup_scan's
    only history day had ran=1/output=0 and would have nagged from day one), or
    the lane never attempted at all AND grace trading days have passed since
    the engine's earliest observed activity (miswired-from-birth jobs)."""
    deploy_proxy = _engine_deploy_proxy(db_path=db_path)
    grace_elapsed = (
        deploy_proxy is not None
        and _trading_days_elapsed(deploy_proxy, today)
        >= ALARM_NEVER_WORKED_GRACE_DAYS
    )
    broken: list[tuple[str, tuple[str, ...]]] = []
    for lane in sorted(ALARM_EXPECTED_LANES):
        attempt_counters, output_counters = _EXPECTED_LANE_COUNTERS[lane]
        if _has_lifetime_output(lane, output_counters, db_path=db_path):
            continue
        attempt_days = _attempt_day_count(lane, attempt_counters, db_path=db_path)
        if attempt_days >= ALARM_NEVER_WORKED_GRACE_DAYS or (
            attempt_days == 0 and grace_elapsed
        ):
            broken.append((lane, output_counters))
    return broken


def _never_worked_message(lane: str, output_counters: tuple[str, ...]) -> str:
    outputs = "/".join(output_counters)
    return (
        f"expected lane has never produced lifetime output: {lane}.{outputs}"
        " — verify wiring before treating silence as healthy"
    )


def rule_never_worked(today: date, db_path: Path) -> str | None:
    """Expected lanes that have attempted work but never produced output."""
    broken = _never_worked_lanes(today, db_path)
    if not broken:
        return None
    return "; ".join(_never_worked_message(*lane) for lane in broken)


def conservation_gap(today: date, db_path: Path) -> str | None:
    """Conservation check v1: every assessed nightly-scan candidate must reach
    exactly one counted candidate-level disposition. An imbalance is a new
    uncounted drop path.

    The partition (written by ``api/research_jobs._setup_scan_nightly_once`` from the
    scan's own journal): structure (final validated) + rejected_by_validation +
    ranker_discarded + no_structure + no_setup == candidates_assessed.
    Conviction ideas are deliberately EXCLUDED — they are a second lane over the
    no_structure pool, not a candidate-level disposition (counting them would
    double-count). Vetoed candidates already land in no_setup."""
    counters = lane_stats.day("setup_scan", today.isoformat(), db_path=db_path)
    assessed = counters.get("candidates_assessed", 0.0)
    if assessed <= 0:
        return None  # no scan today — nothing to reconcile
    accounted = (
        counters.get("structure", 0.0)
        + counters.get("rejected_by_validation", 0.0)
        + counters.get("ranker_discarded", 0.0)
        + counters.get("no_structure", 0.0)
        + counters.get("no_setup", 0.0)
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
    try:
        persisted = await notify(
            kind=kind,
            title=title,
            body=body,
            severity=severity,
            push=True,
            group_key=f"pipeline_audit_{rule_id}",
            db_path=db_path,
        )
    except Exception:
        logger.exception("pipeline_audit alarm notify failed: rule=%s", rule_id)
        return
    if not persisted:
        return
    # Dedup counter keyed on the AUDIT date, not the live clock — read and
    # write must use the same key (prover NIT: a late-night run crossing the
    # UTC/ET boundary must not re-page).
    lane_stats.bump(
        _AUDIT_LANE,
        f"alarm_fired:{rule_id}",
        date=today.isoformat(),
        db_path=db_path,
    )


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
    try:
        never_worked = _never_worked_lanes(today, db_path)
    except Exception:
        logger.exception("pipeline_audit never-worked check failed")
        return
    for lane, output_counter in never_worked:
        rule_id = f"never_worked:{lane}"
        await _fire_alarm(
            rule_id,
            today,
            db_path,
            severity="warning",
            title=f"Idea Engine lane alarm: {rule_id}",
            body=_never_worked_message(lane, output_counter),
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
            # Pinned to the canary's day: register_idea's default as_of is UTC
            # now, whose DATE drifts past today after 20:00 ET — the verify
            # pass probes this row by (origin, date) and must find it.
            as_of=f"{today.isoformat()}T12:00:00+00:00",
            db_path=db_path,
        )
    except Exception:
        logger.exception("canary register_idea failed")

    # Send a real push and record its receipt on success. KNOWN LIMIT (review):
    # send_alert_push returns a token-send count, not a device-render ack — this
    # receipt proves the SEND PIPELINE is alive (config, tokens, transport
    # accept), which covers every canary-detectable failure except APNs/Expo
    # accepting a push the device never renders. A true device-side ack needs a
    # mobile round-trip endpoint — Phase 5 push work, recorded on the flywheel
    # card. The verify pass turns a missing receipt into a critical page.
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

    lane_stats.bump(_CANARY_LANE, "launched", date=today.isoformat(), db_path=db_path)
    lane_stats.bump(
        _CANARY_LANE,
        "sent" if (sent and sent > 0) else "send_failed",
        date=today.isoformat(),
        db_path=db_path,
    )


async def _canary_verify(today: date, db_path: Path) -> None:
    launched = lane_stats.day(_CANARY_LANE, today.isoformat(), db_path=db_path).get(
        "launched", 0.0
    )
    if launched <= 0:
        return  # never launched today (e.g. a restart after 15:30) — nothing to assert

    # Ledger leg: the canary must have landed its idea_outcomes row. A green
    # push receipt alone lets a dead ledger hide behind a healthy transport —
    # register errors are swallowed at launch by design (fail-soft), so the
    # verify pass is where their absence must page (GPT-5.6 audit finding 3).
    ledger_ok = True
    try:
        from db.idea_outcomes import has_registration_on

        ledger_ok = has_registration_on("canary", today.isoformat(), db_path=db_path)
    except Exception:
        logger.exception("canary ledger-leg check failed")
    if not ledger_ok:
        await _fire_alarm(
            "canary_ledger_row_missing",
            today,
            db_path,
            severity="critical",
            title="Idea Engine canary ledger row missing",
            body=(
                f"Canary {_canary_id(today)} launched but no idea_outcomes row "
                "registered — the ledger write path may be dead."
            ),
            kind="ops_canary",
        )

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
