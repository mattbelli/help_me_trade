"""The five nightly research jobs + their registration — one source of gating truth.

Extracted from ``api/main.py`` (R1a, References/RESEARCH_WORKER_DESIGN_2026-07-18.md)
so the same job bodies can be registered by whichever process owns the night
shift: the backend today, the standalone ``hmt-research-worker`` after the R1b
split (``RESEARCH_WORKER_EXTERNAL=1``). This module must never import
``api.main`` — that boots the whole app.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from datetime import date, datetime, timedelta

from ai.reflection.scheduler import REFLECTION_INTERVAL_SECONDS, reflection_once
from api.jobs import Job, register_job
from brokers.errors import BrokerError
from brokers.factory import get_broker, get_market_data_provider
from brokers.market_clock import market_session as _market_session
from db.lane_stats import bump, day
from db.momentum_bars import latest_universe_bar_date
from db.strategies import list_strategies
from signals.momentum import refresh_universe_bars
from signals.universe import all_watchlist_symbols, profile_universe
from signals.volume_profile import build_profile, profile_dir
from trading.market_hours import _ET

logger = logging.getLogger(__name__)

# "1" = the standalone research worker (tools/research_worker.py, R1b) owns the
# five nightly research jobs and the API must not register them — same
# atomic-ownership pattern as OPTIONS_FLOW_EXTERNAL_COLLECTOR (both processes
# read the same .env).
RESEARCH_WORKER_EXTERNAL = os.environ.get(
    "RESEARCH_WORKER_EXTERNAL", "0"
).strip() not in ("0", "false", "no")

OPTIONS_FLOW_INTERVAL_SECONDS = float(os.environ.get("OPTIONS_FLOW_INTERVAL_SECONDS", "1800"))
OPTIONS_FLOW_ENABLED = os.environ.get("OPTIONS_FLOW_ENABLED", "0").strip() not in (
    "0",
    "false",
    "no",
)
SETUP_SCAN_NIGHTLY_ENABLED = os.environ.get("SETUP_SCAN_NIGHTLY_ENABLED", "1").strip() not in (
    "0",
    "false",
    "no",
)
# Earliest ET clock time (fractional hours) for the nightly setup scan — after
# the close so the day's daily bar is final.
SETUP_SCAN_NIGHTLY_AFTER_ET = float(os.environ.get("SETUP_SCAN_NIGHTLY_AFTER_ET", "17.5"))
MOMENTUM_BARS_REFRESH_INTERVAL_SECONDS = 24 * 3600
VOLUME_PROFILE_REFRESH_INTERVAL_SECONDS = 24 * 3600
_PROFILE_FRESH_S = 20 * 3600
_PROFILE_BUILD_MIN_INTERVAL_S = 0.6  # ~100/min, safely under Schwab's ~120/min


# ---------------------------------------------------------------------------
# Options-flow EOD scan (Phase 1c): nightly UV-1/UV-2 into options_flow_events.
# Env-gated OFF by default (serv-only; PC order-flow-RAM incident). Pull-only —
# no notifications here (Phase 2 convergence watch).
# ---------------------------------------------------------------------------
_OPTIONS_FLOW_EOD_LANE = "options_flow_eod"
_options_flow_ran_dates: set[date] = set()
_options_flow_non_alpaca_logged = False


def _should_options_flow_scan(today_et: date) -> bool:
    if today_et in _options_flow_ran_dates:
        return False
    # Restart-safe half of the dedup: the lane_stats day-counter survives a
    # process restart; the in-memory set stays as the fast path.
    try:
        if day(_OPTIONS_FLOW_EOD_LANE, today_et.isoformat()).get("ran", 0) >= 1:
            _options_flow_ran_dates.add(today_et)
            return False
    except Exception:
        logger.exception(
            "options_flow EOD: lane_stats dedup read failed — in-memory dedup only"
        )
    return True


def _mark_options_flow_scanned(today_et: date) -> None:
    _options_flow_ran_dates.add(today_et)
    # Bound memory: keep only today and yesterday.
    stale = [d for d in _options_flow_ran_dates if d < today_et - timedelta(days=1)]
    for d in stale:
        _options_flow_ran_dates.discard(d)
    iso = today_et.isoformat()
    bump(_OPTIONS_FLOW_EOD_LANE, "ran", 1, date=iso)
    # bump() is fail-soft by law — verify the restart-safe marker actually landed.
    try:
        landed = day(_OPTIONS_FLOW_EOD_LANE, iso).get("ran", 0) >= 1
    except Exception:
        landed = False
    if not landed:
        logger.error(
            "options_flow EOD: lane_stats 'ran' marker did not land — "
            "a same-day restart may re-scan"
        )


async def _options_flow_eod_once() -> None:
    """After-close EOD options-flow scan; once per ET day; fail-soft."""
    global _options_flow_non_alpaca_logged
    try:
        if _market_session() not in ("post", "closed", "overnight"):
            return

        today_et = datetime.now(_ET).date()
        if not _should_options_flow_scan(today_et):
            return

        try:
            broker = get_broker("alpaca")  # options data lives only on Alpaca
        except Exception as exc:
            logger.warning("options_flow EOD: Alpaca broker unavailable: %s", exc)
            return

        if not hasattr(broker, "get_option_bars"):
            if not _options_flow_non_alpaca_logged:
                logger.warning(
                    "options_flow EOD: broker has no get_option_bars "
                    "(options data requires Alpaca); skipping"
                )
                _options_flow_non_alpaca_logged = True
            return

        from signals.options_flow.scan import run_eod_scan

        symbols = all_watchlist_symbols()
        event_date = today_et.isoformat()
        # Off-loop: sync broker HTTP + SQLite across the whole watchlist.
        summary = await asyncio.to_thread(
            run_eod_scan, broker, symbols=symbols, event_date=event_date
        )
        _mark_options_flow_scanned(today_et)
        logger.info(
            "options_flow EOD: date=%s underlyings=%s events=%s daily_rows=%s",
            event_date,
            summary.get("underlyings"),
            summary.get("events"),
            summary.get("daily_rows"),
        )

        # Outcome filler (calibration): stamp underlying forward returns on every
        # flow event whose horizons have elapsed — today's tape gets its intraday
        # horizons now that the session's bars exist.
        try:
            from signals.options_flow.outcomes import fill_outcomes

            oc = await asyncio.to_thread(fill_outcomes, broker)
            logger.info("options_flow outcomes: %s", oc)
        except Exception:
            logger.exception("options_flow outcomes: fill failed")
    except Exception:
        logger.exception("options_flow EOD: unexpected failure")


# ---------------------------------------------------------------------------
# Nightly setup scan: keeps the idea-outcomes calibration ledger fed without a
# human remembering to run it. Weekday evenings after the close; once per ET
# day. The artifact topic doubles as the restart-safe "already ran" marker.
# ---------------------------------------------------------------------------
_setup_scan_ran_dates: set[date] = set()


def _setup_scan_nightly_topic(today_et: date) -> str:
    return f"Setup scan (nightly) — {today_et.isoformat()}"


def _setup_scan_already_ran(today_et: date) -> bool:
    if today_et in _setup_scan_ran_dates:
        return True
    from db.ai_tables import artifact_exists_by_topic

    # Exact-match indexed lookup, not a bounded list scan: a high artifact volume
    # could push the day's marker past the limit=20 window (the Jul-14 triple-run).
    topic = _setup_scan_nightly_topic(today_et)
    if artifact_exists_by_topic(topic):
        _setup_scan_ran_dates.add(today_et)
        return True
    return False


def _count_scan_ideas(result: object) -> tuple[int, int]:
    """(structure, conviction) idea counts from a run_setup_scan result.

    Ideas live at ``result["report"]["ideas"]`` (NOT ``result["ideas"]`` — the
    banner bug that logged 0). Each idea carries an ``origin`` stamped server-side;
    ``origin == "structure"`` is a structure setup, anything else is a conviction
    (LLM-nominated) idea."""
    ideas = None
    if isinstance(result, dict):
        report = result.get("report")
        if isinstance(report, dict):
            ideas = report.get("ideas")
    if not isinstance(ideas, list):
        return 0, 0
    structure = sum(1 for i in ideas if isinstance(i, dict) and i.get("origin") == "structure")
    conviction = sum(1 for i in ideas if isinstance(i, dict) and i.get("origin") != "structure")
    return structure, conviction


async def _setup_scan_nightly_once() -> None:
    now_et = datetime.now(_ET)
    if now_et.weekday() >= 5:
        return
    if now_et.hour + now_et.minute / 60 < SETUP_SCAN_NIGHTLY_AFTER_ET:
        return
    today_et = now_et.date()
    if _setup_scan_already_ran(today_et):
        return

    from ai.research.setup_scan import run_setup_scan

    logger.info("Nightly setup scan starting: %s", today_et)
    result = await run_setup_scan(topic=_setup_scan_nightly_topic(today_et))
    _setup_scan_ran_dates.add(today_et)
    for d in [d for d in _setup_scan_ran_dates if d < today_et - timedelta(days=1)]:
        _setup_scan_ran_dates.discard(d)
    structure, conviction = _count_scan_ideas(result)
    logger.info(
        "Nightly setup scan done: %d structure + %d conviction idea(s)",
        structure,
        conviction,
    )
    try:
        bump("setup_scan", "ran", 1)  # heartbeat — the silent-lane rule keys on this
        bump("setup_scan", "registered", structure + conviction)
        bump("setup_scan", "conviction_ok", conviction)
        # Vetoed shadows are registered inside run_setup_scan and never surface in
        # its return, so the count isn't visible from here — bump 0 to materialize
        # the counter row (add-semantics keep any in-scan writer's real count intact).
        bump("setup_scan", "vetoed", 0)
        # Conservation v1 counters (pipeline_audit.conservation_gap): the
        # candidate-level partition from the scan's own journal — structure +
        # rejected_by_validation + ranker_discarded + no_structure + no_setup
        # must equal candidates_assessed. Conviction is a second lane over the
        # no_structure pool, deliberately not part of this partition.
        scan = {}
        if isinstance(result, dict):
            report = result.get("report")
            if isinstance(report, dict) and isinstance(report.get("scan"), dict):
                scan = report["scan"]
        if scan:
            bump("setup_scan", "candidates_assessed", len(scan.get("candidates") or []))
            bump("setup_scan", "structure", structure)
            bump("setup_scan", "rejected_by_validation", len(scan.get("rejected") or []))
            bump("setup_scan", "ranker_discarded", len(scan.get("ranker_discarded") or []))
            bump("setup_scan", "no_structure", len(scan.get("no_structure") or []))
            bump("setup_scan", "no_setup", len(scan.get("no_setup") or []))
    except Exception:
        logger.exception("lane_stats bump failed (setup_scan)")
    artifact_id = result.get("artifact_id") if isinstance(result, dict) else None
    try:
        from signals.notify import notify

        await notify(
            kind="scan_ready",
            title=(
                f"Nightly setup scan done — {structure} structure + {conviction} conviction idea(s)"
            ),
            body=f"{today_et.isoformat()} · tracked in idea outcomes",
            severity="info",
            ref_type="research",
            ref_id=str(artifact_id) if artifact_id is not None else None,
            action_url=(f"/research?id={artifact_id}" if artifact_id is not None else None),
            push=False,
        )
    except Exception:
        logger.exception("scan_ready notify failed")


# ---------------------------------------------------------------------------
# Daily momentum-universe bar refresh (gated on any momentum* strategy enabled).
# ---------------------------------------------------------------------------
_momentum_boot_freshness_checked = False


def _momentum_strategy_enabled() -> bool:
    return any(
        s.get("enabled") and str(s.get("id", "")).startswith("momentum") for s in list_strategies()
    )


def _last_completed_weekday(now_et: datetime) -> date:
    """Most recent weekday whose daily bar should exist by now. Holiday-naive:
    a market holiday fails the freshness check and costs one redundant refresh
    — fail-open toward refreshing, never toward skipping."""
    d = now_et.date()
    if now_et.hour < 18:  # today's bar isn't reliably final before the evening
        d -= timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


async def _momentum_bars_refresh_once() -> int:
    global _momentum_boot_freshness_checked
    if not _momentum_strategy_enabled():
        return 0
    if not _momentum_boot_freshness_checked:
        # run_on_start boot pass: skip the full S&P 500 batch refetch when the
        # persisted bars are already current (design §4b).
        _momentum_boot_freshness_checked = True
        try:
            latest = await asyncio.to_thread(latest_universe_bar_date)
            if latest is not None and date.fromisoformat(latest[:10]) >= (
                _last_completed_weekday(datetime.now(_ET))
            ):
                logger.info(
                    "Momentum bars refresh: boot pass skipped — bars current through %s",
                    latest,
                )
                return 0
        except Exception:
            logger.exception("Momentum bars boot freshness check failed — refreshing")
    return await refresh_universe_bars()


# ---------------------------------------------------------------------------
# Daily volume-profile precompute (per-symbol file-mtime freshness, paced for
# the provider's rate limit — never burst).
# ---------------------------------------------------------------------------
def _profile_is_fresh(symbol: str) -> bool:
    fp = profile_dir() / f"{symbol}.json"
    try:
        return fp.is_file() and (time.time() - fp.stat().st_mtime) < _PROFILE_FRESH_S
    except OSError:
        return False


async def _volume_profile_refresh_once() -> int:
    symbols = [s for s in profile_universe() if not _profile_is_fresh(s)]
    if not symbols:
        return 0
    try:
        provider = get_market_data_provider()
    except BrokerError as exc:
        logger.warning("Volume profile refresh: provider unavailable: %s", exc)
        return 0
    built = 0
    logger.info("Volume profile precompute: %d symbols to build", len(symbols))
    for i, sym in enumerate(symbols):
        prof = await build_profile(provider, sym)
        if prof is not None:
            built += 1
        if i % 100 == 0 and i:
            logger.info("Volume profile precompute progress: %d/%d", i, len(symbols))
        await asyncio.sleep(_PROFILE_BUILD_MIN_INTERVAL_S)
    logger.info("Volume profile precompute done: built %d/%d", built, len(symbols))
    return built


# ---------------------------------------------------------------------------
# Daily reflection — at most once per ET day (an LLM run costs real money),
# restart-safe via the lane_stats day counter: any ownership overlap or
# same-day restart would otherwise double-charge.
# ---------------------------------------------------------------------------
_REFLECTION_LANE = "reflection"


async def _reflection_once_daily() -> None:
    today_iso = datetime.now(_ET).date().isoformat()
    if day(_REFLECTION_LANE, today_iso).get("ran", 0) >= 1:
        return
    await reflection_once()
    bump(_REFLECTION_LANE, "ran", 1, date=today_iso)


def register_research_jobs() -> None:
    """Register the five nightly research jobs into THIS process's registry.

    Called by the backend when RESEARCH_WORKER_EXTERNAL is unset, by the
    standalone worker when it is set — the job definitions (gates, intervals,
    dedup) live here either way, so ownership can flip without drift.
    """
    register_job(
        Job(
            name="setup_scan_nightly",
            fn=_setup_scan_nightly_once,
            interval_s=900.0,
            enabled=lambda: SETUP_SCAN_NIGHTLY_ENABLED,
        )
    )
    register_job(
        Job(
            name="options_flow",
            fn=_options_flow_eod_once,
            interval_s=float(OPTIONS_FLOW_INTERVAL_SECONDS),
            enabled=lambda: OPTIONS_FLOW_ENABLED,
        )
    )
    # run_on_start=True preserves the pre-R1a boot-first loop semantics for the
    # two 24h jobs — sleep-first registration would mean "never runs" on a box
    # that restarts more often than daily (design §4b).
    register_job(
        Job(
            name="momentum_bars_refresh",
            fn=_momentum_bars_refresh_once,
            interval_s=float(MOMENTUM_BARS_REFRESH_INTERVAL_SECONDS),
            run_on_start=True,
        )
    )
    register_job(
        Job(
            name="volume_profile_refresh",
            fn=_volume_profile_refresh_once,
            interval_s=float(VOLUME_PROFILE_REFRESH_INTERVAL_SECONDS),
            run_on_start=True,
        )
    )
    register_job(
        Job(
            name="reflection",
            fn=_reflection_once_daily,
            interval_s=float(REFLECTION_INTERVAL_SECONDS),
        )
    )
