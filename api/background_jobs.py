"""The background-job registry: every periodic job + heartbeat the backend runs,
registered once into ``api/jobs`` (which drives them and feeds ``/health``).

Extracted verbatim from ``api/main`` (strangler round 3). This module is the
manifest of what runs: the ``*_jobs`` modules own the runners, this file names
them, their cadence and their enable flags. ``api/main``'s lifespan calls
``_register_background_jobs()`` (both the full and the test-mode path).
"""

from __future__ import annotations

import logging
import os

from api.earnings_jobs import (
    _EARNINGS_ALERTS_ENABLED,
    EARNINGS_ALERT_INTERVAL_SECONDS,
    EARNINGS_REFRESH_INTERVAL_SECONDS,
    EARNINGS_RELEASE_POLL_SECONDS,
    _earnings_alert_once,
)
from api.flow_jobs import _OPTIONS_FLOW_EXTERNAL_COLLECTOR, OPTIONS_FLOW_POLL_SECONDS, _flow_runner
from api.fusion_jobs import (
    _fusion_atr_refresh_once,
    _fusion_intake_once,
    _fusion_levels_refresh_once,
    _fusion_lifecycle_eod_once,
    _fusion_lifecycle_once,
    _fusion_reason_once,
)
from api.jobs import register_job, register_watchdog_job
from api.ops_jobs import (
    _FILINGS_FEED_ENABLED,
    _book_deadman_once,
    _book_deepseek_once,
    _book_matt_once,
    _db_retention_once,
    _filings_congress_once,
    _filings_edgar_once,
)
from api.poll_jobs import POLL_INTERVAL_SECONDS
from api.reports_routes import _MORNING_REPORT_ENV_ENABLED, MORNING_REPORT_INTERVAL_SECONDS
from api.research_jobs import OPTIONS_FLOW_ENABLED as _OPTIONS_FLOW_ENABLED
from api.research_jobs import RESEARCH_WORKER_EXTERNAL as _RESEARCH_WORKER_EXTERNAL
from api.research_jobs import register_research_jobs
from api.scan_jobs import (
    REGIME_REFRESH_INTERVAL_SECONDS,
    REGIME_SLEEVES_SCAN_INTERVAL_SECONDS,
    SESSION_NODE_SCAN_INTERVAL_SECONDS,
    SIGNAL_SCAN_INTERVAL_SECONDS,
)
from api.scanner_routes import _RVOL_ALERTS_ENABLED, RVOL_ALERT_INTERVAL_SECONDS, _rvol_alert_once
from api.schwab_jobs import (
    _SCHWAB_HEALTH_ALERTS_ENABLED,
    SCHWAB_HEALTH_INTERVAL_SECONDS,
    SCHWAB_SNAPSHOT_INTERVAL_SECONDS,
    _schwab_health_once,
    _schwab_snapshot_once,
)
from api.tape_jobs import _EQUITY_TAPE_FLUSH_SECONDS
from api.trade_jobs import (
    _ALERT_SYMBOL_INTERVAL_SECONDS,
    _IDEA_TRACKER_INTERVAL_MARKET_SECONDS,
    _PROPOSAL_SWEEP_INTERVAL_SECONDS,
    _TRADE_SYNC_INTERVAL_SECONDS,
)
from signals.social_buzz import _LOOP_INTERVAL_SECONDS as _SOCIAL_BUZZ_INTERVAL_SECONDS

_NEWS_STREAM_ENABLED = os.environ.get("NEWS_STREAM_ENABLED", "1").strip() not in (
    "0",
    "false",
    "no",
)
logger = logging.getLogger(__name__)


# The nightly setup scan lives in api/research_jobs.py (R1a) so the research
# worker can own it after the R1b split.
_background_jobs_registered = False




def _register_background_jobs() -> None:
    global _background_jobs_registered
    if _background_jobs_registered:
        return
    _background_jobs_registered = True

    from api import jobs as jobs_mod
    from config.idea_engine import FUSION_POLL_SECONDS

    noop = jobs_mod._heartbeat_noop

    heartbeat_jobs = [
        ("poll", POLL_INTERVAL_SECONDS, None),
        ("signal_scan", SIGNAL_SCAN_INTERVAL_SECONDS, None),
        ("session_node", SESSION_NODE_SCAN_INTERVAL_SECONDS, None),
        ("alert_symbol", _ALERT_SYMBOL_INTERVAL_SECONDS, None),
        ("trade_sync", _TRADE_SYNC_INTERVAL_SECONDS, None),
        ("proposal_sweeper", _PROPOSAL_SWEEP_INTERVAL_SECONDS, None),
        ("idea_tracker", _IDEA_TRACKER_INTERVAL_MARKET_SECONDS, None),
        ("earnings_refresh", float(EARNINGS_REFRESH_INTERVAL_SECONDS), None),
        ("earnings_release", float(EARNINGS_RELEASE_POLL_SECONDS), None),
        ("regime_refresh", float(REGIME_REFRESH_INTERVAL_SECONDS), None),
        (
            "reports_scheduler",
            float(MORNING_REPORT_INTERVAL_SECONDS),
            lambda: _MORNING_REPORT_ENV_ENABLED,
        ),
        ("equity_tape_flush", _EQUITY_TAPE_FLUSH_SECONDS, None),
        ("regime_sleeves", float(REGIME_SLEEVES_SCAN_INTERVAL_SECONDS), None),
        ("social_buzz", float(_SOCIAL_BUZZ_INTERVAL_SECONDS), None),
        # Marked alive by the news websocket on every receive/idle tick — a
        # stale heartbeat means the socket is actually dead, not quiet news.
        ("news_stream", 300.0, lambda: _NEWS_STREAM_ENABLED),
    ]
    for name, interval_s, enabled in heartbeat_jobs:
        register_job(
            jobs_mod.Job(
                name=name,
                fn=noop,
                interval_s=interval_s,
                enabled=enabled,
                heartbeat_only=True,
            )
        )

    register_job(
        jobs_mod.Job(
            name="rvol_alert",
            fn=_rvol_alert_once,
            interval_s=float(RVOL_ALERT_INTERVAL_SECONDS),
            enabled=lambda: _RVOL_ALERTS_ENABLED,
        )
    )
    register_job(
        jobs_mod.Job(
            name="options_flow_intraday",
            fn=_flow_runner.intraday_once,
            interval_s=float(OPTIONS_FLOW_POLL_SECONDS),
            enabled=lambda: _OPTIONS_FLOW_ENABLED and not _OPTIONS_FLOW_EXTERNAL_COLLECTOR,
        )
    )
    register_job(
        jobs_mod.Job(
            name="filings_edgar",
            fn=_filings_edgar_once,
            interval_s=600.0,
            enabled=lambda: _FILINGS_FEED_ENABLED,
        )
    )
    register_job(
        jobs_mod.Job(
            name="filings_congress",
            fn=_filings_congress_once,
            interval_s=21_600.0,
            enabled=lambda: _FILINGS_FEED_ENABLED,
        )
    )
    register_job(
        jobs_mod.Job(
            name="earnings_alert",
            fn=_earnings_alert_once,
            interval_s=float(EARNINGS_ALERT_INTERVAL_SECONDS),
            enabled=lambda: _EARNINGS_ALERTS_ENABLED,
        )
    )
    register_job(
        jobs_mod.Job(
            name="schwab_health",
            fn=_schwab_health_once,
            interval_s=float(SCHWAB_HEALTH_INTERVAL_SECONDS),
            enabled=lambda: _SCHWAB_HEALTH_ALERTS_ENABLED,
        )
    )
    register_job(
        jobs_mod.Job(
            name="schwab_snapshot",
            fn=_schwab_snapshot_once,
            interval_s=float(SCHWAB_SNAPSHOT_INTERVAL_SECONDS),
        )
    )
    register_job(
        jobs_mod.Job(
            name="db_retention",
            fn=_db_retention_once,
            interval_s=3600.0,
        )
    )
    register_job(
        jobs_mod.Job(
            name="book_deepseek",
            fn=_book_deepseek_once,
            interval_s=300.0,
        )
    )
    register_job(
        jobs_mod.Job(
            name="book_matt",
            fn=_book_matt_once,
            interval_s=300.0,
        )
    )
    register_job(
        jobs_mod.Job(
            name="book_deadman",
            fn=_book_deadman_once,
            interval_s=300.0,
        )
    )
    from signals.pipeline_audit import pipeline_audit_once

    register_job(
        jobs_mod.Job(
            name="fusion_intake",
            fn=_fusion_intake_once,
            interval_s=float(FUSION_POLL_SECONDS),
            market_hours_only=True,
        )
    )
    register_job(
        jobs_mod.Job(
            name="fusion_atr_refresh",
            fn=_fusion_atr_refresh_once,
            interval_s=300.0,
        )
    )
    register_job(
        jobs_mod.Job(
            name="fusion_levels_refresh",
            fn=_fusion_levels_refresh_once,
            interval_s=300.0,
        )
    )
    register_job(
        jobs_mod.Job(
            name="fusion_reason",
            fn=_fusion_reason_once,
            interval_s=120.0,
            market_hours_only=True,
        )
    )
    register_job(
        jobs_mod.Job(
            name="fusion_lifecycle",
            fn=_fusion_lifecycle_once,
            interval_s=60.0,
            market_hours_only=True,
        )
    )
    register_job(
        jobs_mod.Job(
            name="fusion_lifecycle_eod",
            fn=_fusion_lifecycle_eod_once,
            interval_s=300.0,
        )
    )
    register_job(
        jobs_mod.Job(
            name="pipeline_audit",
            fn=pipeline_audit_once,
            interval_s=900.0,
        )
    )
    # The five nightly research jobs (setup scan, flow EOD, volume profiles,
    # momentum bars, reflection) register through ONE shared entry point so the
    # standalone research worker can own them after the R1b split — same
    # ownership seam as the collectors.
    if _RESEARCH_WORKER_EXTERNAL:
        logger.info(
            "research jobs: RESEARCH_WORKER_EXTERNAL=1 — the standalone research "
            "worker owns the night shift; not registering the five research jobs"
        )
    else:
        register_research_jobs()
    register_watchdog_job()
