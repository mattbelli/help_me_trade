"""Scan loops: signal scan (rules + strategy scan → proposal intake), session-node
scan, regime macro refresh, regime sleeves. Extracted verbatim from ``api/main``
(strangler round 3); ``api/main``'s lifespan starts the loops, ``api/background_jobs``
registers their heartbeats.
"""

from __future__ import annotations

import asyncio
import logging
import os

from api.jobs import mark_alive
from signals.regime import refresh_regime_macro
from signals.regime_sleeves import run_regime_sleeves_scan
from signals.scan import run_signal_scan
from signals.session_node_strategy import run_session_node_scan
from signals.strategy_scan import run_strategy_scan
from trading.proposal_intake import propose_from_scan_fire

logger = logging.getLogger(__name__)


SIGNAL_SCAN_INTERVAL_SECONDS = max(30, int(os.environ.get("SIGNAL_SCAN_INTERVAL_SECONDS", "300")))
# Session-node reacts to SPY touching a level on the 1-min chart, so it runs on its
# OWN fast cadence (default 60s = the bar granularity) — decoupled from the heavy
# 5-min signal scan so we don't also run the expensive daily-bar scans every minute.
SESSION_NODE_SCAN_INTERVAL_SECONDS = max(
    20, int(os.environ.get("SESSION_NODE_SCAN_INTERVAL_SECONDS", "60"))
)


REGIME_REFRESH_INTERVAL_SECONDS = 24 * 3600
# Regime sleeves evaluate completed daily bars after the close — a ~10-min cadence is
# plenty (the scan self-gates to the post-close window and dedups per trading day).
REGIME_SLEEVES_SCAN_INTERVAL_SECONDS = max(
    60, int(os.environ.get("REGIME_SLEEVES_SCAN_INTERVAL_SECONDS", "600"))
)


async def _signal_scan_loop() -> None:
    while True:
        try:
            triggered = await run_signal_scan()
            if triggered:
                logger.info(
                    "Signal scan triggered: %s",
                    [t["rule_id"] for t in triggered],
                )
            for item in triggered:
                if item["action"].get("type") == "trade":
                    try:
                        await propose_from_scan_fire(item)
                    except Exception:
                        logger.exception(
                            "Agent trade proposal failed for rule %s",
                            item.get("rule_id"),
                        )
                else:
                    from signals.notify import notify

                    await notify(
                        kind="rule_triggered",
                        title=f"{item['rule_name']} — signal",
                        body=str(item["action"]),
                        severity="info",
                        ref_type="rule",
                        ref_id=item["rule_id"],
                        action_url="/alerts",
                        data={
                            "rule_id": item["rule_id"],
                            "rule_name": item["rule_name"],
                            "action": item["action"],
                        },
                        push=False,
                    )
            try:
                await run_strategy_scan()
            except Exception:
                logger.exception("Dip-strategy scan failed")
        except Exception:
            logger.exception("Signal scan loop iteration failed")
        else:
            mark_alive("signal_scan")
        await asyncio.sleep(SIGNAL_SCAN_INTERVAL_SECONDS)


async def _session_node_loop() -> None:
    """Session-node scan on its own ~1-min cadence (separate from the 5-min signal
    scan), so a node touch is proposed within ~1 min instead of up to 5. Cost is tiny:
    one small SPY 1-min fetch + trigger detection per cycle. Propose-only as always."""
    while True:
        try:
            await run_session_node_scan()
        except Exception:
            logger.exception("Session-node scan failed")
        else:
            mark_alive("session_node")
        await asyncio.sleep(SESSION_NODE_SCAN_INTERVAL_SECONDS)


async def _regime_sleeves_loop() -> None:
    """Regime-local sleeves (generals_dip + vol_event) — propose-only, post-close.

    The scan itself gates to the after-close window, dedups per trading day, and
    no-ops while both sleeves are disabled (their shipped state)."""
    while True:
        try:
            acted = await run_regime_sleeves_scan()
            if acted:
                logger.info("Regime-sleeves scan acted: %s", acted)
            mark_alive("regime_sleeves")
        except Exception:
            logger.exception("Regime-sleeves scan failed")
        await asyncio.sleep(REGIME_SLEEVES_SCAN_INTERVAL_SECONDS)


async def _regime_refresh_loop() -> None:
    while True:
        try:
            n = await refresh_regime_macro()
            if n:
                logger.info("Regime macro refresh upserted %d rows", n)
        except Exception:
            logger.exception("Regime refresh loop iteration failed")
        else:
            mark_alive("regime_refresh")
        # The macro substrate (own store, data/macro.db) rides the same daily
        # cadence but fails independently — a substrate outage must not unmark
        # regime_refresh and page the sleeves' freshness gate.
        try:
            from signals.macro_substrate import refresh_macro_substrate

            await refresh_macro_substrate()
        except Exception:
            logger.exception("Macro substrate refresh failed")
        await asyncio.sleep(REGIME_REFRESH_INTERVAL_SECONDS)
