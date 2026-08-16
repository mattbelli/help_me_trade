"""Trade-side loops: broker trade sync, proposal sweeper (expiry + phone push),
idea tracker (outcome transitions → notify), and the alert-symbol hub subscriber.
Extracted verbatim from ``api/main`` (strangler round 3); ``api/main``'s lifespan
starts them, ``api/background_jobs`` registers their heartbeats.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime

from api.hub import market_hub
from api.jobs import mark_alive
from api.poll_jobs import _poll_ext_quotes_for_alerts
from brokers.market_clock import market_session as _market_session
from db.chart_features import list_price_alerts
from trading.market_hours import _ET
from trading.sync import async_sync_all_enabled_brokers

logger = logging.getLogger(__name__)


_TRADE_SYNC_INTERVAL_SECONDS = 3600.0
_ALERT_SYMBOL_INTERVAL_SECONDS = 30.0
_PROPOSAL_SWEEP_INTERVAL_SECONDS = 60.0
_IDEA_TRACKER_INTERVAL_MARKET_SECONDS = 1800.0
_IDEA_TRACKER_INTERVAL_OFF_SECONDS = 3600.0


async def _trade_sync_loop() -> None:
    """Sync trade ledger from enabled brokers on startup, then periodically."""
    try:
        results = await async_sync_all_enabled_brokers()
        for r in results:
            logger.info("Trade sync on connect: %s", r)
        mark_alive("trade_sync")
    except Exception:
        logger.exception("Initial trade sync failed")
    while True:
        await asyncio.sleep(_TRADE_SYNC_INTERVAL_SECONDS)
        try:
            results = await async_sync_all_enabled_brokers()
            for r in results:
                logger.info("Periodic trade sync: %s", r)
            mark_alive("trade_sync")
        except Exception:
            logger.exception("Periodic trade sync failed")


async def _proposal_sweeper_loop() -> None:
    """Expire unanswered proposals, retry undelivered pushes, reconcile orphans."""
    from trading.proposals import (
        reconcile_orphaned_approvals,
        retry_undelivered_pushes,
        sweep_expired_proposals,
    )

    async def _run_pass() -> None:
        expired = await sweep_expired_proposals()
        retried = await retry_undelivered_pushes()
        reconciled = await reconcile_orphaned_approvals()
        logger.debug(
            "Proposal sweeper: expired=%d retried=%d reconciled=%d",
            expired,
            retried,
            reconciled,
        )
        if expired:
            logger.info("Proposal sweeper expired %d proposals", expired)
        mark_alive("proposal_sweeper")

    try:
        await _run_pass()
    except Exception:
        logger.exception("Initial proposal sweep failed")
    while True:
        await asyncio.sleep(_PROPOSAL_SWEEP_INTERVAL_SECONDS)
        try:
            await _run_pass()
        except Exception:
            logger.exception("Periodic proposal sweep failed")


def _idea_tracker_sleep_seconds() -> float:
    now_et = datetime.now(_ET)
    if now_et.weekday() < 5 and 9 <= now_et.hour < 17:
        return _IDEA_TRACKER_INTERVAL_MARKET_SECONDS
    return _IDEA_TRACKER_INTERVAL_OFF_SECONDS


async def _notify_idea_transition(t: dict) -> None:
    """One card per idea status change. Triggered pushes to the phone (price
    just entered a zone the founder may want to act on); resolutions are
    inbox-only via INBOX_ONLY_KINDS."""
    from signals.notify import notify

    # fusion_mech is the silent-journal lane: registered for calibration only,
    # so its transitions never mint bell rows (founder 07-21 — 11 leaked cards
    # in one shadow day). Delivery for fusion ideas arrives via the reason lane
    # at the swing-lane ARM round, not tracker bells.
    if str(t.get("origin") or "") == "fusion_mech":
        return

    symbol = str(t.get("symbol") or "?")
    status = str(t.get("status") or "")
    zone = f"{t.get('entry_low')}–{t.get('entry_high')}"
    if status == "triggered":
        kind = "idea_triggered"
        title = f"{symbol} idea triggered — entered zone {zone}"
        body = f"stop {t.get('stop')} · origin {t.get('origin') or 'setup_scan'}"
    else:
        kind = "idea_resolved"
        detail = {
            "won": "target hit",
            "lost": "stopped out",
            "expired": "timed out",
            "invalidated": "stop hit before entry",
        }.get(status, status)
        title = f"{symbol} idea {status} — {detail}"
        body = f"zone {zone} · stop {t.get('stop')} · origin {t.get('origin') or 'setup_scan'}"
    await notify(
        kind=kind,
        title=title,
        body=body,
        severity="info",
        ref_type="idea_outcome",
        ref_id=str(t.get("id")),
        group_key=f"idea:{symbol}",
        data={"symbol": symbol, "status": status},
        push=(status == "triggered"),
    )


async def _idea_tracker_loop() -> None:
    """Shadow-track setup_scan idea outcomes against daily bars."""
    from research.idea_tracker import sweep_open_ideas

    async def _run_pass() -> None:
        transitions: list[dict] = []
        counts = await asyncio.to_thread(sweep_open_ideas, transitions=transitions)
        if any(counts.get(k, 0) for k in counts if k != "examined"):
            logger.info("Idea tracker sweep: %s", counts)
        for t in transitions:
            try:
                await _notify_idea_transition(t)
            except Exception:
                logger.exception("idea transition notify failed: %s", t)
        mark_alive("idea_tracker")

    try:
        await _run_pass()
    except Exception:
        logger.exception("Initial idea tracker sweep failed")
    while True:
        await asyncio.sleep(_idea_tracker_sleep_seconds())
        try:
            await _run_pass()
        except Exception:
            logger.exception("Periodic idea tracker sweep failed")


async def _alert_symbol_loop() -> None:
    while True:
        try:
            symbols = {str(a["symbol"]).upper() for a in list_price_alerts(active_only=True)}
            # Pin the watchlists too: the tape detector (blocks/dark pool) and
            # confluence only see subscribed symbols — "things we are watching"
            # must stream server-side, not just while a browser has them open.
            watch = set(symbols)
            try:
                from config.settings import get_watchlists_state

                for wl in get_watchlists_state().get("lists") or []:
                    watch.update(str(s).upper() for s in (wl.get("symbols") or []))
            except Exception:
                logger.debug("watchlist pin failed", exc_info=True)
            await market_hub.ensure_symbols(watch)
            # Outside regular hours the Schwab equity stream is silent, so alerts
            # would never fire overnight. Poll extended quotes server-side for the
            # alert symbols and push them through apply_extended (which evaluates
            # the alerts) — independent of whether any client has the watchlist open.
            if symbols and _market_session() in ("pre", "post", "overnight"):
                await _poll_ext_quotes_for_alerts(symbols)
        except Exception:
            logger.exception("Alert symbol loop iteration failed")
        else:
            mark_alive("alert_symbol")
        await asyncio.sleep(_ALERT_SYMBOL_INTERVAL_SECONDS)
