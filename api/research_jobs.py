"""The research jobs + their registration — one source of gating truth.

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
from datetime import date, datetime, timedelta, timezone
from typing import Any

from ai.reflection.scheduler import REFLECTION_INTERVAL_SECONDS, reflection_once
from api.jobs import Job, register_job
from brokers.errors import BrokerError
from brokers.factory import get_broker, get_market_data_provider
from config.idea_engine import (
    FUSION_DAY_VOLUME_LATCH_MAX_MIN,
    FUSION_DAY_VOLUME_REFRESH_S,
)
from db.lane_stats import bump, day
from db.momentum_bars import latest_universe_bar_date
from db.strategies import list_strategies
from signals.momentum import refresh_universe_bars
from signals.universe import all_watchlist_symbols, profile_universe
from signals.volume_profile import build_profile, profile_dir
from trading.market_calendar import is_trading_day
from trading.market_hours import _ET, _SESSION_OPEN, is_after_close

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
        # Scan an ET date only once ITS OWN session is history. The old gate
        # admitted the `market_session()` label "overnight", which spans ET
        # midnight (20:00 → 04:00): the ~00:1x ET tick scanned the ET day that
        # had just BEGUN, found nothing (events=0, daily_rows=0), and then
        # consumed the once-per-ET-day dedup slot — so the real post-close run
        # was skipped as already-ran and options_flow_daily, the UV-2
        # unusualness DENOMINATOR, froze at 2026-07-17 for five sessions.
        # is_after_close() is half-day aware; is_trading_day() keeps weekends
        # and holidays out (is_after_close alone reads them as after-close).
        now_et = datetime.now(_ET)
        today_et = now_et.date()
        if not is_trading_day(today_et) or not is_after_close(now_et):
            return
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
        # Consume the day's slot only if the scan actually wrote its baseline
        # rows. A run that logs success and writes nothing is the failure shape
        # that hid this bug for five sessions — /health and the dead-man both
        # read it as alive — so an empty result now RETRIES on the next tick
        # (bounded: after-close on a trading day only, every
        # OPTIONS_FLOW_INTERVAL_SECONDS until ET midnight) instead of silently
        # burning the day.
        daily_rows = int(summary.get("daily_rows") or 0)
        if daily_rows:
            _mark_options_flow_scanned(today_et)
        else:
            logger.warning(
                "options_flow EOD: date=%s wrote NO daily rows — leaving the day "
                "unmarked so the next tick retries",
                event_date,
            )
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
# day. The lane_stats day counter is the restart-safe "already ran" marker; the
# artifact topic is a second layer, not the primary one (see below).
# ---------------------------------------------------------------------------
_SETUP_SCAN_LANE = "setup_scan"
_setup_scan_ran_dates: set[date] = set()


def _setup_scan_nightly_topic(today_et: date) -> str:
    return f"Setup scan (nightly) — {today_et.isoformat()}"


def _setup_scan_already_ran(today_et: date) -> bool:
    if today_et in _setup_scan_ran_dates:
        return True
    # Restart-safe marker — same lane_stats day-counter the options-flow lane
    # uses above, and the load-bearing check. The artifact-topic probe below
    # only holds while the scan's own topic survives, and until 07-26 it did
    # not: the completing writer overwrote `topic` with the model's free-text
    # title, so the marker vanished the moment the scan finished and the next
    # post-restart tick re-ran the whole thing (the 07-04/07-14/07-21/07-23/
    # 07-24 multi-runs, each duplicating ideas into the calibration ledger).
    try:
        if day(_SETUP_SCAN_LANE, today_et.isoformat()).get("ran", 0) >= 1:
            _setup_scan_ran_dates.add(today_et)
            return True
    except Exception:
        logger.exception(
            "setup scan: lane_stats dedup read failed — falling back to the "
            "artifact-topic marker"
        )

    from db.ai_tables import artifact_exists_by_topic

    # Exact-match indexed lookup, not a bounded list scan: a high artifact volume
    # could push the day's marker past the limit=20 window (the Jul-14 triple-run).
    topic = _setup_scan_nightly_topic(today_et)
    if artifact_exists_by_topic(topic):
        _setup_scan_ran_dates.add(today_et)
        return True
    return False


def _mark_setup_scan_ran(today_et: date) -> None:
    _setup_scan_ran_dates.add(today_et)
    # Bound memory: keep only today and yesterday.
    for d in [d for d in _setup_scan_ran_dates if d < today_et - timedelta(days=1)]:
        _setup_scan_ran_dates.discard(d)
    iso = today_et.isoformat()
    # Doubles as the lane heartbeat the silent-lane alarm keys on.
    bump(_SETUP_SCAN_LANE, "ran", 1, date=iso)
    # bump() is fail-soft by law — verify the restart-safe marker actually landed.
    try:
        landed = day(_SETUP_SCAN_LANE, iso).get("ran", 0) >= 1
    except Exception:
        landed = False
    if not landed:
        logger.error(
            "setup scan: lane_stats 'ran' marker did not land — "
            "a same-day restart may re-run the scan"
        )


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
    _mark_setup_scan_ran(today_et)
    structure, conviction = _count_scan_ideas(result)
    logger.info(
        "Nightly setup scan done: %d structure + %d conviction idea(s)",
        structure,
        conviction,
    )
    try:
        # "ran" is bumped by _mark_setup_scan_ran above — it is the dedup marker
        # AND the heartbeat the silent-lane rule keys on, so it must land exactly
        # once per completed scan.
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
# Today's cumulative volume producer (fusion_day_volume read-cache) — the
# numerator behind the rvol_ex_prints dot. Market-hours only; the intake never
# fetches, it only reads the cache (the fusion_atr_cache/fusion_spot_cache
# doctrine).
# ---------------------------------------------------------------------------
def _latched_offset(
    prior: tuple[str, Any, Any] | None, today: str
) -> tuple[int | None, str | None]:
    """This symbol's already-latched ``(offset, offset_ts)`` for *today*.

    ``(None, None)`` when there is no row, the row is yesterday's, the offset
    was never latched, or either field is unusable — every one of those means
    "no trustworthy offset", never "an offset of zero"."""
    if prior is None or prior[0] != today:
        return None, None
    offset, offset_ts = prior[1], prior[2]
    if offset is None or not offset_ts:
        return None, None
    try:
        return int(offset), str(offset_ts)
    except (TypeError, ValueError):
        return None, None


async def _fusion_day_volume_refresh_once(*, now: datetime | None = None) -> int:
    """``now`` is the clock seam — the latch window is a wall-clock decision,
    so tests have to be able to stand at 09:31 and at 11:00."""
    from db.base import DEFAULT_DB_PATH
    from db.base import connect as _db_connect
    from db.fusion import init_fusion_tables, put_day_volumes
    from signals.universe import open_episode_symbols

    symbols = sorted(set(profile_universe()) | set(open_episode_symbols()))
    if not symbols:
        return 0
    # PINNED to alpaca: serv runs MARKET_DATA_PROVIDER=hybrid, which can route
    # to Schwab — a 200-symbol snapshot burst there trips the Akamai WAF, and
    # Schwab must never become a hard dependency of a fusion lane.
    try:
        provider = get_market_data_provider("alpaca")
    except BrokerError as exc:
        logger.warning("Day-volume refresh: alpaca provider unavailable: %s", exc)
        return 0
    fetcher = getattr(provider, "aget_day_volumes", None)
    if fetcher is None:
        logger.warning("Day-volume refresh: provider has no aget_day_volumes")
        return 0

    volumes = await fetcher(symbols)
    if not volumes:
        return 0

    now = now or datetime.now(timezone.utc)
    now_et = now.astimezone(_ET)
    today = now_et.date().isoformat()
    minutes_since_open = (now_et.hour * 60 + now_et.minute) - (
        _SESSION_OPEN[0] * 60 + _SESSION_OPEN[1]
    )
    # Only the first minutes of the session may latch an offset. Later than
    # that, a symbol arriving with no row for today (a mid-session episode on a
    # name outside profile_universe) would latch its ENTIRE day so far as
    # "premarket" and publish ~0.0x RVOL for the rest of the day — a fabricated
    # low on exactly the fresh-burst population this lane exists to describe.
    can_latch = 0 <= minutes_since_open <= FUSION_DAY_VOLUME_LATCH_MAX_MIN
    # as_of_ts is the FETCH instant, not the daily bar's ``t``: a daily bar is
    # stamped at the session's START, so fencing freshness on it would make
    # every row read as hours-stale and the reading would never publish. The
    # bar stamp stays available on the provider call for callers who want bar
    # provenance; this cache wants "when was this counter read".
    fetched_at = now.isoformat()
    init_fusion_tables(DEFAULT_DB_PATH)
    with _db_connect(DEFAULT_DB_PATH) as conn:
        stored = {
            str(row["symbol"]): (
                str(row["market_day"] or ""),
                row["premarket_offset"],
                row["offset_ts"],
            )
            for row in conn.execute(
                "SELECT symbol, market_day, premarket_offset, offset_ts "
                "FROM fusion_day_volume"
            ).fetchall()
        }

    rows: list[tuple[str, str, str | None, str | None, str, str]] = []
    unlatched = 0
    for symbol, (volume, _bar_ts) in volumes.items():
        prior = stored.get(symbol)
        prior_offset, prior_offset_ts = _latched_offset(prior, today)
        # Alpaca's snapshot serves the PREVIOUS session's dailyBar until a
        # symbol's first trade of the day, so a thin name can latch yesterday's
        # full-day total and then "shrink" when today's bar starts. A volume
        # below the latched offset is that, and the offset is not salvageable.
        regressed = prior_offset is not None and prior_offset > volume
        if prior is not None and prior[0] == today and not regressed:
            offset, offset_ts = (
                (str(prior_offset), prior_offset_ts)
                if prior_offset is not None
                else (None, None)
            )
        else:
            # The day's first in-window sample is this symbol's premarket
            # offset. It necessarily absorbs the first minutes of RTH volume
            # too (the job wakes on its own cadence, not on the bell) — bounded
            # by the latch window and deliberately on the conservative side: an
            # offset that is too HIGH makes the published RVOL too LOW.
            offset = str(volume) if can_latch else None
            offset_ts = fetched_at if can_latch else None
        if offset is None:
            unlatched += 1
        rows.append((symbol, str(volume), offset, offset_ts, today, fetched_at))
    if unlatched:
        logger.info(
            "Day-volume refresh: %d/%d symbols have no latched offset today "
            "(now %d min after the open) — their RVOL reads unavailable",
            unlatched,
            len(rows),
            minutes_since_open,
        )
    return put_day_volumes(rows, db_path=DEFAULT_DB_PATH)


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
    """Register the research jobs into THIS process's registry.

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
            name="fusion_day_volume_refresh",
            fn=_fusion_day_volume_refresh_once,
            interval_s=float(FUSION_DAY_VOLUME_REFRESH_S),
            market_hours_only=True,
        )
    )
    register_job(
        Job(
            name="reflection",
            fn=_reflection_once_daily,
            interval_s=float(REFLECTION_INTERVAL_SECONDS),
        )
    )
