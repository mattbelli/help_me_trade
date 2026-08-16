"""FastAPI app: poll Alpaca, store snapshots, serve JSON + simple HTML."""

from __future__ import annotations

import asyncio
import logging
import os
import time
from contextlib import asynccontextmanager
from datetime import date, datetime, timezone

from fastapi import Body, FastAPI, HTTPException, Query, Request, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from ai.llm.factory import clear_llm_cache
from ai.rules.engine import evaluate_on_snapshot
from api.ai_routes import router as ai_router
from api.alert_drawing_routes import router as alert_drawing_router
from api.alpaca_guard import _require_alpaca
from api.auth import (
    auth_enabled,
    frontend_origins,
    verify_admin_token,
    verify_websocket_auth,
)
from api.bars_routes import _BARS_PREWARM_INTERVAL, _bars_prewarm_loop
from api.bars_routes import router as bars_router
from api.device_routes import router as device_router
from api.fill_orphan_routes import router as fill_orphan_router
from api.flow_routes import router as flow_router
from api.fusion_jobs import (
    _fusion_atr_refresh_once,
    _fusion_intake_once,
    _fusion_levels_refresh_once,
    _fusion_lifecycle_eod_once,
    _fusion_lifecycle_once,
    _fusion_reason_once,
)
from api.fusion_routes import router as fusion_router
from api.jobs import (
    jobs_health,
    mark_alive,
    newest_backup_age_s,
    off_box_pull_age_s,
    register_job,
    register_watchdog_job,
    start_all,
)
from api.journal_routes import router as journal_router
from api.loop_probe import LoopProbe
from api.middleware import SecurityMiddleware
from api.notification_routes import router as notification_router
from api.orders_routes import router as orders_router
from api.performance_routes import router as performance_router
from api.poll_health import poll_health
from api.reports_routes import (
    _MORNING_REPORT_ENV_ENABLED,
    MORNING_REPORT_INTERVAL_SECONDS,
    _reports_scheduler_loop,
)
from api.reports_routes import router as reports_router
from api.research_jobs import OPTIONS_FLOW_ENABLED as _OPTIONS_FLOW_ENABLED
from api.research_jobs import RESEARCH_WORKER_EXTERNAL as _RESEARCH_WORKER_EXTERNAL
from api.research_jobs import register_research_jobs
from api.scan_routes import router as scan_router
from api.scanner_routes import _rvol_alert_once
from api.scanner_routes import router as scanner_router
from api.schwab_routes import router as schwab_router
from api.social_routes import router as social_router
from api.status_page import router as status_page_router
from api.strategy_lab_routes import router as strategy_lab_router
from api.strategy_routes import router as strategy_router
from api.ticker_routes import router as ticker_router
from api.trade_routes import router as trade_router
from api.watchlist_routes import router as watchlist_router
from api.workspace_routes import router as workspace_router
from brokers.alpaca import AlpacaAdapter
from brokers.alpaca_tier import clear_alpaca_tier_cache, data_tier, effective_alpaca_feed
from brokers.errors import BrokerError
from brokers.factory import (
    aclose_broker_cache,
    clear_broker_cache,
    connection_is_configured,
    get_broker,
    get_broker_for_account,
    get_enabled_broker_names,
    get_market_data_provider,
    market_data_provider_name,
)
from brokers.market_clock import market_session as _market_session
from brokers.market_stream import AlpacaMarketStreamHub, BaseMarketHub
from brokers.schwab_market_stream import SchwabMarketStreamHub
from config.log_redaction import install_log_redaction
from config.settings import (
    alpaca_config,
    apply_settings_patch,
    broker_is_configured,
    get_chart_layout,
    is_entries_halted,
    is_kill_switch_active,
    load_settings,
    mask_settings,
    primary_broker,
    save_chart_layout,
)
from db.accounts import (
    get_account,
    get_connection,
    get_default_account,
    list_accounts,
    reconcile_account_external_id,
)
from db.ai_tables import delete_rule, init_ai_tables
from db.base import DEFAULT_DB_PATH
from db.chart_features import init_chart_features, list_price_alerts
from db.chat_sessions import init_chat_tables
from db.device_tokens import init_device_token_tables
from db.earnings_alerts import init_earnings_alert_tables
from db.earnings_calendar import earnings_hour, last_earnings_date, next_earnings_date
from db.equity_history import equity_history
from db.filings import init_filings_tables
from db.idea_outcomes import init_idea_outcomes_tables
from db.live_activities import init_live_activity_tables
from db.multi_account import migrate_multi_account
from db.notifications import init_notifications_tables
from db.order_executions import (
    count_pending_executions,
    count_submitted_executions,
    entry_dates_by_symbol,
    init_execution_tables,
)
from db.paper_orders import init_paper_order_tables
from db.push_deliveries import init_push_delivery_tables
from db.reports import init_reports_tables
from db.session_node import init_session_node_tables
from db.sqlite import (
    compute_and_save_snapshot_analytics,
    get_all_latest_snapshots,
    get_latest_snapshot,
    init_db,
    save_snapshot,
)
from db.strategies import init_strategy_tables, list_strategies, seed_default_strategies
from db.trades import init_trade_tables
from db.workspace import init_workspace_tables
from signals.earnings_refresh import arefresh_earnings_calendar
from signals.options_flow.runner import OptionsFlowRunner
from signals.regime import refresh_regime_macro
from signals.regime_sleeves import run_regime_sleeves_scan
from signals.scan import run_signal_scan
from signals.session_node_strategy import (
    compute_session_levels,
    run_session_node_scan,
)
from signals.social_buzz import _LOOP_INTERVAL_SECONDS as _SOCIAL_BUZZ_INTERVAL_SECONDS
from signals.social_buzz import social_buzz_loop
from signals.strategy_kinds import strategy_kind
from signals.strategy_scan import run_strategy_scan
from signals.strategy_universe import resolve_universe_spec
from signals.universe import all_watchlist_symbols as _all_watchlist_symbols
from signals.universe import flow_watch_symbols as _flow_watch_symbols
from trading.exit_engine import run_exit_engine
from trading.fill_tracker import reconcile_open_executions, reconcile_pending_executions
from trading.market_hours import _ET, _SESSION_OPEN
from trading.proposal_intake import propose_from_scan_fire
from trading.stop_guard import place_protective_stops
from trading.sync import async_sync_all_enabled_brokers

POLL_INTERVAL_SECONDS = max(5, int(os.environ.get("POLL_INTERVAL_SECONDS", "20")))
SIGNAL_SCAN_INTERVAL_SECONDS = max(30, int(os.environ.get("SIGNAL_SCAN_INTERVAL_SECONDS", "300")))
# Session-node reacts to SPY touching a level on the 1-min chart, so it runs on its
# OWN fast cadence (default 60s = the bar granularity) — decoupled from the heavy
# 5-min signal scan so we don't also run the expensive daily-bar scans every minute.
SESSION_NODE_SCAN_INTERVAL_SECONDS = max(
    20, int(os.environ.get("SESSION_NODE_SCAN_INTERVAL_SECONDS", "60"))
)
EARNINGS_REFRESH_INTERVAL_SECONDS = 24 * 3600
# The EDGAR "current filings" feed is a rolling window of the latest 100 8-Ks.
# Measured on a heavy earnings evening it held 74 minutes of filings, so polling
# every 5 minutes leaves ~15x headroom before a burst could push one off the
# window unseen. Cheap: one atom request, then two requests per NEW listed filer.
EARNINGS_RELEASE_POLL_SECONDS = 300
REGIME_REFRESH_INTERVAL_SECONDS = 24 * 3600
# Regime sleeves evaluate completed daily bars after the close — a ~10-min cadence is
# plenty (the scan self-gates to the post-close window and dedups per trading day).
REGIME_SLEEVES_SCAN_INTERVAL_SECONDS = max(
    60, int(os.environ.get("REGIME_SLEEVES_SCAN_INTERVAL_SECONDS", "600"))
)
RVOL_ALERT_INTERVAL_SECONDS = max(60, int(os.environ.get("RVOL_ALERT_INTERVAL_SECONDS", "300")))
_RVOL_ALERTS_ENABLED = os.environ.get("RVOL_ALERTS_ENABLED", "1").strip() not in (
    "0",
    "false",
    "no",
)
_FILINGS_FEED_ENABLED = os.environ.get("FILINGS_FEED_ENABLED", "1").strip() not in (
    "0",
    "false",
    "no",
)
FILINGS_INSIDER_MIN_USD = float(os.environ.get("FILINGS_INSIDER_MIN_USD", "100000"))
_NEWS_STREAM_ENABLED = os.environ.get("NEWS_STREAM_ENABLED", "1").strip() not in (
    "0",
    "false",
    "no",
)
_OPTIONS_FLOW_STREAM_ENABLED = os.environ.get("OPTIONS_FLOW_STREAM_ENABLED", "0").strip() not in (
    "0",
    "false",
    "no",
)
# "1" = the standalone collector process (tools/flow_collector.py) owns the
# intraday flow pipeline and the API must NOT run it: Alpaca allows one OPRA
# websocket per account, so two hosts 406-fight each other into backoff. Both
# processes read the same .env, so this one switch flips ownership atomically.
_OPTIONS_FLOW_EXTERNAL_COLLECTOR = os.environ.get(
    "OPTIONS_FLOW_EXTERNAL_COLLECTOR", "0"
).strip() not in ("0", "false", "no")
# Same ownership pattern for the STOCKS socket: "1" = tools/equity_collector.py
# owns the wildcard tape (and runs market-wide tape detection + the confluence
# watch itself); the hub dials the collector's local proxy and this process's
# tape flush loop stands down.
_EQUITY_TAPE_EXTERNAL_COLLECTOR = os.environ.get(
    "EQUITY_TAPE_EXTERNAL_COLLECTOR", "0"
).strip() not in ("0", "false", "no")
OPTIONS_FLOW_POLL_SECONDS = max(10, int(os.environ.get("OPTIONS_FLOW_POLL_SECONDS", "30")))
SCHWAB_HEALTH_INTERVAL_SECONDS = max(
    300, int(os.environ.get("SCHWAB_HEALTH_INTERVAL_SECONDS", str(6 * 3600)))
)
_SCHWAB_HEALTH_ALERTS_ENABLED = os.environ.get("SCHWAB_HEALTH_ALERTS_ENABLED", "1").strip() not in (
    "0",
    "false",
    "no",
)
SCHWAB_SNAPSHOT_INTERVAL_SECONDS = max(
    300, int(os.environ.get("SCHWAB_SNAPSHOT_INTERVAL_SECONDS", str(1800)))
)
EARNINGS_ALERT_INTERVAL_SECONDS = max(
    60, int(os.environ.get("EARNINGS_ALERT_INTERVAL_SECONDS", str(6 * 3600)))
)
_EARNINGS_ALERTS_ENABLED = os.environ.get("EARNINGS_ALERTS_ENABLED", "1").strip() not in (
    "0",
    "false",
    "no",
)
_TRADE_SYNC_INTERVAL_SECONDS = 3600.0
_ALERT_SYMBOL_INTERVAL_SECONDS = 30.0
_PROPOSAL_SWEEP_INTERVAL_SECONDS = 60.0
_IDEA_TRACKER_INTERVAL_MARKET_SECONDS = 1800.0
_IDEA_TRACKER_INTERVAL_OFF_SECONDS = 3600.0
_LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
logging.basicConfig(
    level=getattr(logging, os.environ.get("LOG_LEVEL", "INFO").upper(), logging.INFO),
    format=_LOG_FORMAT,
)
# Vendor keys ride in the query string (Finnhub token, FRED api_key, AV apikey) and
# httpx logs full URLs — redact before anything reaches the journal.
install_log_redaction(_LOG_FORMAT)
logger = logging.getLogger(__name__)


# NB: the live-quote hub follows the DATA provider (MARKET_DATA_PROVIDER), NOT the
# execution broker. Keeps Schwab data/streaming decoupled from order routing —
# Schwab stays read-only; activating Schwab streaming must never imply Schwab execution.
def _build_market_hub() -> BaseMarketHub:
    provider = market_data_provider_name()
    if provider == "hybrid":
        from brokers.hybrid_market_stream import HybridMarketStreamHub

        return HybridMarketStreamHub()
    if provider == "schwab":
        return SchwabMarketStreamHub()
    return AlpacaMarketStreamHub()


market_hub: BaseMarketHub = _build_market_hub()

# The trading path (execution_service/fill_tracker/exit_engine/proposals) imports
# `brokers.market_stream.market_hub` to broadcast alerts. Point that alias at THIS live hub
# so execution/fill/exit alerts reach connected WS clients (cockpit notifications).
from brokers import market_stream as _market_stream  # noqa: E402

_market_stream.market_hub.bind(market_hub)


async def _poll_account(account: dict) -> bool:
    account_id = str(account["id"])
    broker_name = str(account["broker"]).strip().lower()
    connection_id = account.get("connection_id")
    if connection_id:
        connection = await asyncio.to_thread(get_connection, str(connection_id))
        if connection is None or not connection_is_configured(connection):
            logger.warning(
                "Skipping account %s — connection not configured",
                account_id,
            )
            return False
    elif not broker_is_configured(broker_name):
        logger.warning("Skipping account %s — broker not configured", account_id)
        return False

    try:
        broker = get_broker_for_account(account_id)
        # Stamped BEFORE the fetch: see save_snapshot's captured_at note.
        asked_at = datetime.now(timezone.utc).isoformat()
        fetched_account, positions, orders = await broker.afetch_snapshot()
        registered_external_id = account.get("broker_external_id")
        fetched_external_id = str(fetched_account.id)
        if registered_external_id:
            if str(registered_external_id) != fetched_external_id:
                logger.error(
                    "Snapshot account id mismatch for %s: registered=%s fetched=%s — skipping save",
                    account_id,
                    registered_external_id,
                    fetched_external_id,
                )
                return False
        else:
            await asyncio.to_thread(
                reconcile_account_external_id,
                account_id,
                fetched_external_id,
            )

        captured_at = await asyncio.to_thread(
            save_snapshot,
            fetched_account,
            positions,
            orders,
            broker=broker_name,
            account_id=account_id,
            captured_at=asked_at,
        )
        logger.info(
            "Snapshot saved at %s (%s account=%s)",
            captured_at,
            broker_name,
            account_id,
        )
        # Per-account fill reconcile (each account via its own adapter).
        if count_pending_executions(broker_name, account_id=account_id) > 0:
            await reconcile_pending_executions(broker_name, account_id=account_id)
        if count_submitted_executions(broker_name, account_id=account_id) > 0:
            await reconcile_open_executions(broker_name, account_id=account_id)
        # Book protective stops ride the same pass: the snapshot saved above
        # already shows a just-filled entry, so the stop validates first try.
        if broker_name == "alpaca":
            await place_protective_stops(
                broker_name, account_id=account_id, positions=positions
            )
        # Analytics + rules-eval stay default-account-only (broker-keyed display +
        # watchlist-global rules) — unchanged.
        if not account.get("is_default"):
            return True
        await asyncio.to_thread(compute_and_save_snapshot_analytics, broker_name)
        snapshot = await asyncio.to_thread(
            get_latest_snapshot,
            broker_name,
            account_id=account_id,
        )
        if snapshot:
            symbols = {
                str(p.get("symbol", "")).upper()
                for p in snapshot.get("positions") or []
                if p.get("symbol")
            }
            quotes = market_hub.quote_snapshot(symbols)
            triggered = evaluate_on_snapshot(snapshot, quotes=quotes)
            if triggered:
                logger.info(
                    "Rules triggered on poll (%s account=%s): %s",
                    broker_name,
                    account_id,
                    [t["rule_id"] for t in triggered],
                )
                from signals.notify import notify

                for item in triggered:
                    await notify(
                        kind="rule_triggered",
                        title=f"{item['rule_name']} triggered — {item['action'].get('type', '?')}",
                        body=str(item["action"]),
                        severity="info",
                        ref_type="rule",
                        ref_id=item["rule_id"],
                        action_url="/alerts",
                        data={
                            "rule_id": item["rule_id"],
                            "rule_name": item["rule_name"],
                            "action": item["action"],
                            "broker": broker_name,
                        },
                        push=False,
                    )
        return True
    except Exception:
        logger.exception("Poll failed for account %s (%s)", account_id, broker_name)
        return False


async def _poll_broker(broker_name: str) -> bool:
    if not broker_is_configured(broker_name):
        logger.warning("Skipping %s — not configured", broker_name)
        return False
    try:
        broker = get_broker(broker_name)
        # Stamped BEFORE the fetch: see save_snapshot's captured_at note.
        asked_at = datetime.now(timezone.utc).isoformat()
        account, positions, orders = await broker.afetch_snapshot()
        trade_account = get_default_account(broker_name)
        captured_at = await asyncio.to_thread(
            save_snapshot,
            account,
            positions,
            orders,
            broker=broker_name,
            account_id=trade_account["id"] if trade_account else None,
            captured_at=asked_at,
        )
        logger.info("Snapshot saved at %s (%s)", captured_at, broker_name)
        await asyncio.to_thread(compute_and_save_snapshot_analytics, broker_name)
        if count_pending_executions(broker_name) > 0:
            await reconcile_pending_executions(broker_name)
        if count_submitted_executions(broker_name) > 0:
            await reconcile_open_executions(broker_name)
        snapshot = await asyncio.to_thread(
            get_latest_snapshot,
            broker_name,
            account_id=trade_account["id"] if trade_account else None,
        )
        if snapshot:
            symbols = {
                str(p.get("symbol", "")).upper()
                for p in snapshot.get("positions") or []
                if p.get("symbol")
            }
            quotes = market_hub.quote_snapshot(symbols)
            triggered = evaluate_on_snapshot(snapshot, quotes=quotes)
            if triggered:
                logger.info(
                    "Rules triggered on poll (%s): %s",
                    broker_name,
                    [t["rule_id"] for t in triggered],
                )
                from signals.notify import notify

                for item in triggered:
                    await notify(
                        kind="rule_triggered",
                        title=f"{item['rule_name']} triggered — {item['action'].get('type', '?')}",
                        body=str(item["action"]),
                        severity="info",
                        ref_type="rule",
                        ref_id=item["rule_id"],
                        action_url="/alerts",
                        data={
                            "rule_id": item["rule_id"],
                            "rule_name": item["rule_name"],
                            "action": item["action"],
                            "broker": broker_name,
                        },
                        push=False,
                    )
        return True
    except Exception:
        logger.exception("Poll failed for %s", broker_name)
        return False


async def _poll_once() -> None:
    tasks: list[asyncio.Task[bool]] = []
    alpaca_accounts = [
        account
        for account in list_accounts(enabled_only=True)
        if str(account["broker"]).strip().lower() == "alpaca"
    ]
    for account in alpaca_accounts:
        tasks.append(asyncio.create_task(_poll_account(account)))

    for name in get_enabled_broker_names():
        if name == "schwab":
            tasks.append(asyncio.create_task(_poll_broker(name)))

    if not tasks:
        return
    results = await asyncio.gather(*tasks, return_exceptions=False)
    if any(results):
        poll_health.record_success()
        mark_alive("poll")

    # Unmanaged-fill sweep (audit H1/M1) — ONCE per cycle, cross-account, AFTER the
    # per-account reconciles above (so any fill attributed this cycle already has its
    # managed/orphan row). A partial fill that jumped terminal across a poll gap, or a
    # fill whose attribution was lost to a restart, holds real shares with NO exit
    # management + NO page; this quarantines + pages them. Additive to the reconcile
    # loop, not a new bg loop; fail-isolated so it can never break the poll.
    try:
        from trading.position_ledger import sweep_unmanaged_terminal_fills

        swept = await sweep_unmanaged_terminal_fills()
        if swept:
            logger.warning("Unmanaged-fill sweep quarantined %d new orphan fill(s)", swept)
    except Exception:
        logger.exception("Unmanaged-fill sweep pass failed")

    # Exit engine — ONE pass after all snapshots are saved + fills reconciled this
    # cycle, so it reads fresh per-account snapshots and a current managed ledger. It
    # walks open managed positions and dispatches sells per each strategy's exit_mode
    # (auto -> ExecutionService chokepoint; propose -> phone proposal). Fail-closed
    # inside; isolated here so an exit hiccup can never break the poll loop.
    try:
        outcomes = await run_exit_engine()
        acted = [o for o in outcomes if o.get("action") in {"exit", "scale", "closed"}]
        if acted:
            logger.info(
                "Exit engine acted on %d position(s): %s",
                len(acted),
                [(o.get("symbol"), o.get("action"), o.get("reason")) for o in acted],
            )
    except Exception:
        logger.exception("Exit engine pass failed")


async def _poll_loop() -> None:
    while True:
        try:
            await _poll_once()
        except Exception:
            logger.exception("Poll loop iteration failed")
        await asyncio.sleep(POLL_INTERVAL_SECONDS)


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


async def _earnings_refresh_once() -> int:
    """Refresh the earnings calendar for the universes of ENABLED earnings-kind strategies.

    One Finnhub call covers all companies; it filters to the union of those universes. Returns
    rows upserted (0 if nothing enabled / Finnhub unconfigured).
    """
    symbols: set[str] = set()
    for s in list_strategies(enabled_only=True):
        if strategy_kind(s["id"]) == "earnings":
            symbols |= set(resolve_universe_spec(s.get("universe")))
    # Also cover the watchlist so its fundamentals panel shows a next-earnings date
    # (Schwab gives only lastEarningsDate; the forward date is Finnhub/calendar-sourced).
    symbols |= set(_all_watchlist_symbols())
    if not symbols:
        return 0
    return await arefresh_earnings_calendar(sorted(symbols))


async def _earnings_refresh_loop() -> None:
    while True:
        try:
            n = await _earnings_refresh_once()
            if n:
                logger.info("Earnings calendar refresh upserted %d rows", n)
        except Exception:
            logger.exception("Earnings refresh loop iteration failed")
        else:
            mark_alive("earnings_refresh")
        await asyncio.sleep(EARNINGS_REFRESH_INTERVAL_SECONDS)


async def _earnings_release_loop() -> None:
    """Pull new Item 2.02 earnings releases off EDGAR and store their exhibit text.

    Separate from `_earnings_refresh_loop` (which is the daily calendar of WHEN
    companies report): this is the release itself, and it is the only source we
    hold for segment results and guidance — the XBRL corpus strips dimensions.
    """
    from signals.earnings_exhibit import poll_once

    while True:
        try:
            summary = await asyncio.to_thread(poll_once)
            if summary["stored"]:
                logger.info(
                    "Earnings releases stored: %d (failed %d)",
                    summary["stored"],
                    summary["failed"],
                )
        except Exception:
            logger.exception("Earnings release poll failed")
        else:
            mark_alive("earnings_release")
        await asyncio.sleep(EARNINGS_RELEASE_POLL_SECONDS)


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


_EQUITY_TAPE_FLUSH_SECONDS = 2.0


async def _probe_equity_proxy() -> bool:
    """True iff the equity collector's local proxy accepts a TCP connection —
    i.e. the collector PROCESS is up (upstream health is the proxy's own job)."""
    from urllib.parse import urlparse

    parsed = urlparse(os.environ.get("EQUITY_TAPE_PROXY_URL", "ws://127.0.0.1:8767"))
    try:
        _, writer = await asyncio.wait_for(
            asyncio.open_connection(parsed.hostname or "127.0.0.1", parsed.port or 8767),
            timeout=1.5,
        )
    except Exception:
        return False
    writer.close()
    try:
        await writer.wait_closed()
    except Exception:
        pass
    return True


_EQUITY_PROXY_PROBE_SECONDS = 15.0


async def _equity_tape_flush_loop() -> None:
    """Persist large-print tape events queued by the stream hub's detector,
    then run them through the confluence watch (any 2 watched things)."""
    from db.equity_tape import insert_event as _insert_tape_event
    from signals.options_flow.convergence import process_events

    if _EQUITY_TAPE_EXTERNAL_COLLECTOR:
        logger.info(
            "equity tape: external collector owns detection "
            "(EQUITY_TAPE_EXTERNAL_COLLECTOR=1); flush loop standing down"
        )

    collector_reachable = True
    next_probe_mono = 0.0
    while True:
        await asyncio.sleep(_EQUITY_TAPE_FLUSH_SECONDS)
        if _EQUITY_TAPE_EXTERNAL_COLLECTOR:
            # The collector persists + converges market-wide; draining the
            # hub's (bounded, maxlen=256) detector here would double-process.
            # The heartbeat now means "the collector's proxy is reachable" —
            # a dead collector must surface as a stale beat on /health, not a
            # green board over a frozen watchlist (2026-07-15 Opus review).
            now_mono = time.monotonic()
            if now_mono >= next_probe_mono:
                next_probe_mono = now_mono + _EQUITY_PROXY_PROBE_SECONDS
                reachable = await _probe_equity_proxy()
                if reachable != collector_reachable:
                    (logger.info if reachable else logger.error)(
                        "equity tape: collector proxy %s",
                        "reachable again"
                        if reachable
                        else "UNREACHABLE — watchlist stream is down until "
                        "hmt-equity-collector returns (or flip the flag to 0)",
                    )
                collector_reachable = reachable
            if collector_reachable:
                mark_alive("equity_tape_flush")
            continue
        try:
            detector = getattr(market_hub, "tape_detector", None)
            events: list[dict] = []
            if detector is not None:
                while detector.pending:
                    events.append(detector.pending.popleft())
            for ev in events:
                await asyncio.to_thread(_insert_tape_event, ev)

            if events:
                try:
                    broker = get_broker("alpaca")
                except Exception:
                    broker = None
                if broker is not None:
                    now_et = datetime.now(_ET)
                    open_min = _SESSION_OPEN[0] * 60 + _SESSION_OPEN[1]
                    now_min = now_et.hour * 60 + now_et.minute
                    conv = await process_events(
                        broker,
                        events,
                        today_et=now_et.date().isoformat(),
                        # Real minutes (negative pre-market): rvol_from_profile returns None
                        # for <0 — clamping to 0 compared pre-market volume against the
                        # first 5-min bucket and fabricated 16-22x RVOL (2026-07-10 4AM).
                        minutes_since_open=now_min - open_min,
                    )
                    if conv.get("convergence") or conv.get("pushes"):
                        logger.info(
                            "equity tape: events=%s convergence=%s pushes=%s",
                            len(events),
                            conv.get("convergence", 0),
                            conv.get("pushes", 0),
                        )
        except Exception:
            logger.exception("equity tape: flush failed")
        else:
            mark_alive("equity_tape_flush")


async def _poll_ext_quotes_for_alerts(symbols: set[str]) -> None:
    """Fetch extended-hours quotes for alert symbols and feed apply_extended.

    Server-side so off-hours price alerts fire even with no browser/phone polling
    the watchlist (the only other apply_extended caller). Schwab-only (ext quotes);
    fail-soft — a quote miss just skips the symbol this cycle.
    """
    try:
        provider = get_market_data_provider()
    except BrokerError:
        return
    from brokers.hybrid_market_data import HybridMarketData
    from brokers.schwab_market_data import SchwabMarketData

    if not isinstance(provider, (SchwabMarketData, HybridMarketData)):
        return
    session = _market_session()
    try:
        ext_quotes = await provider.aget_quotes(sorted(symbols))
    except Exception:
        logger.debug("Alert ext-quote poll failed", exc_info=True)
        return
    for sym in symbols:
        eq = ext_quotes.get(sym, {})
        ext_price = eq.get("ext_last")
        if ext_price is None:
            continue
        try:
            await market_hub.apply_extended(
                sym,
                ext_price=ext_price,
                ext_change_pct=eq.get("ext_change_pct"),
                market_session=session,
                broadcast=True,
            )
        except Exception:
            logger.debug("apply_extended (alert poll) failed for %s", sym, exc_info=True)


# ---------------------------------------------------------------------------
# The nightly EOD options-flow scan lives in api/research_jobs.py (R1a) so the
# research worker can own it after the R1b split.
# The intraday flow pipeline (poll + OPRA stream + sweep consumer) lives in
# signals/options_flow/runner.py so the standalone collector process
# (tools/flow_collector.py) can host it instead of this event loop --
# ownership flips via OPTIONS_FLOW_EXTERNAL_COLLECTOR in the shared .env.
def _flow_universe() -> list[str]:
    """Flow watchlist (dedicated "flow" list, pinned-first; union fallback)
    + Round-4 hot-lane promotions (lane names ride last so the underlying cap
    clips them first — the founder's list is never evicted).
    Scoped to the FLOW runner only; other _all_watchlist_symbols consumers
    (earnings warm, ext quotes) stay watchlist-pure. Import inside the guard:
    even a broken attention module must never cost the flow lane its
    watchlist."""
    base = _flow_watch_symbols()
    try:
        from signals.fusion.attention import merge_hot_lane

        return merge_hot_lane(base)
    except Exception:
        logger.warning("flow universe: hot-lane merge unavailable", exc_info=True)
        return base


_flow_runner = OptionsFlowRunner(watchlist_symbols=_flow_universe)


def _upcoming_earnings_date(symbol: str, today_iso: str) -> str | None:
    """Next watchlist earnings on or after today (includes day-of)."""
    if last_earnings_date(symbol, today_iso) == today_iso:
        return today_iso
    return next_earnings_date(symbol, today_iso)


def _earnings_hour_suffix(hour: str | None, *, day_of: bool) -> str:
    if not hour:
        return ""
    h = hour.lower()
    if day_of:
        if h == "amc":
            return ", after close"
        if h == "bmo":
            return ", before open"
        return ""
    if h == "amc":
        return ", AMC"
    if h == "bmo":
        return ", BMO"
    return ""


def _t0_still_ahead(hour: str | None, now_et: datetime) -> bool:
    """True while a day-of 'reports TODAY' alert is still a heads-up, not a post-mortem.

    BMO prints before the open → useless after 09:30 ET. AMC (and unknown hour)
    print at/after the close → useless once the regular session has ended.
    Without this gate, adding a name to the watchlist after the print (or a 6h
    loop landing post-close) still fires 'reports TODAY' hours late.
    """
    from trading.market_hours import session_close_time

    minutes = now_et.hour * 60 + now_et.minute
    h = (hour or "").strip().lower()
    if h == "bmo":
        return minutes < 9 * 60 + 30
    close_h, close_m = session_close_time(now_et.date())
    return minutes < close_h * 60 + close_m


def _kick_earnings_alert_catchup() -> None:
    """Fire-and-forget a pass so a mid-day watchlist add doesn't wait up to 6h."""

    async def _run() -> None:
        try:
            await _earnings_alert_once()
        except Exception:
            logger.exception("Earnings alert catch-up after watchlist edit failed")

    try:
        asyncio.get_running_loop().create_task(_run())
    except RuntimeError:
        # Sync context with no loop (tests) — the periodic job will pick it up.
        pass


async def _earnings_alert_once() -> None:
    """Check watchlist symbols and push earnings milestone alerts (t14/t1/t0)."""
    from db.earnings_alerts import mark_sent, was_sent
    from signals.notify import notify

    now_et = datetime.now(_ET)
    today = now_et.date()
    today_iso = today.isoformat()
    symbols = _all_watchlist_symbols()
    if not symbols:
        return

    for sym in symbols:
        try:
            earnings_date_str = _upcoming_earnings_date(sym, today_iso)
            if not earnings_date_str:
                continue
            earnings_date = date.fromisoformat(earnings_date_str)
            if earnings_date < today:
                continue
            days_until = (earnings_date - today).days
            hour = earnings_hour(sym, earnings_date_str)
            hour_t1 = _earnings_hour_suffix(hour, day_of=False)
            hour_t0 = _earnings_hour_suffix(hour, day_of=True)

            milestones: list[tuple[str, str]] = []
            if 1 < days_until <= 14:
                milestones.append(("t14", f"📅 {sym} earnings in 2 weeks — {earnings_date_str}"))
            if 0 < days_until <= 1:
                milestones.append(
                    (
                        "t1",
                        f"⏰ {sym} reports tomorrow ({earnings_date_str}{hour_t1})",
                    )
                )
            if days_until == 0 and _t0_still_ahead(hour, now_et):
                milestones.append(
                    (
                        "t0",
                        f"🔔 {sym} reports TODAY ({earnings_date_str}{hour_t0})",
                    )
                )

            for milestone, body in milestones:
                key = f"{sym}:{earnings_date_str}:{milestone}"
                if was_sent(key):
                    continue
                await notify(
                    kind="earnings",
                    title=f"Earnings: {sym} — {milestone}",
                    body=body,
                    severity="info",
                    action_url=f"/watchlist/{sym}",
                    data={
                        "symbol": sym,
                        "earnings_date": earnings_date_str,
                        "milestone": milestone,
                    },
                )
                mark_sent(key)
                logger.info(
                    "Earnings alert sent: %s milestone=%s date=%s",
                    sym,
                    milestone,
                    earnings_date_str,
                )
        except Exception:
            logger.exception("Earnings alert: error processing symbol %s", sym)


# Schwab health alert state: last-alert date (resets each ET day)
_schwab_health_last_date: dict[
    str, date
] = {}  # key: alert type ("expiring"/"offline"/"probe_failed")
_schwab_probe_failures = 0
_SCHWAB_PROBE_FAILURE_THRESHOLD = 3


async def _schwab_health_once() -> None:
    """Single pass: auth bookkeeping checks + one SPY quote probe."""
    global _schwab_probe_failures

    from brokers.schwab_auth import auth_status
    from signals.notify import notify

    status = auth_status()
    today_et = datetime.now(_ET).date()

    if not status.get("authenticated"):
        if _schwab_health_last_date.get("offline") != today_et:
            await notify(
                kind="schwab_health",
                title="Schwab data offline",
                body="Token invalid; re-auth needed. Run scripts.schwab_reauth or re-login.",
                severity="critical",
                group_key="schwab_health_offline",
                data={"auth_status": "offline"},
            )
            _schwab_health_last_date["offline"] = today_et
            logger.warning("Schwab health alert: token offline")
    else:
        days_left = status.get("estimated_days_until_reauth")
        if days_left is not None and days_left <= 1.0:
            if _schwab_health_last_date.get("expiring") != today_et:
                await notify(
                    kind="schwab_health",
                    title="Schwab re-auth needed",
                    body=(
                        f"Token expires within ~{days_left:.1f} day(s). "
                        "Run scripts.schwab_reauth or re-login."
                    ),
                    severity="warning",
                    group_key="schwab_health_expiring",
                    data={
                        "auth_status": "expiring",
                        "days_left": days_left,
                    },
                )
                _schwab_health_last_date["expiring"] = today_et
                logger.warning("Schwab health alert: token expiring in %.2f days", days_left)

    # Probe only when the token *claims* to be fine — an offline token already
    # paged above; probing it too would double-alert (alert fatigue).
    if status.get("authenticated") and broker_is_configured("schwab"):
        try:
            provider = get_market_data_provider("schwab")
            await provider.aget_quotes(["SPY"])
            _schwab_probe_failures = 0
        except Exception:
            _schwab_probe_failures += 1
            logger.exception(
                "Schwab data probe failed (%d consecutive)",
                _schwab_probe_failures,
            )
            if _schwab_probe_failures >= _SCHWAB_PROBE_FAILURE_THRESHOLD:
                if _schwab_health_last_date.get("probe_failed") != today_et:
                    await notify(
                        kind="schwab_health",
                        title="Schwab data probe failing",
                        body=(
                            f"Schwab data probe failing ({_SCHWAB_PROBE_FAILURE_THRESHOLD}x) — "
                            "token or WAF problem; market data may be dead."
                        ),
                        severity="critical",
                        group_key="schwab_health_probe_failed",
                        data={
                            "auth_status": "probe_failed",
                            "consecutive_failures": _schwab_probe_failures,
                        },
                    )
                    _schwab_health_last_date["probe_failed"] = today_et
                    logger.warning(
                        "Schwab health alert: data probe failed %d times",
                        _schwab_probe_failures,
                    )


_schwab_snapshot_last_date: dict = {}
_db_retention_last_date: dict = {}


async def _book_deepseek_once() -> None:
    """Deep Seek paper-book nightly analyst cycle (research/book_driver.py).

    Self-gates: weekday post-17:05-ET, once per ET day, skips when the DeepSeek
    key is unconfigured. Disabled strategy record = DRY cycle (pack + report +
    lane rows + inbox, no proposals) — the shipped state until the founder arms it."""
    from research.book_driver import run_book_cycle

    result = await run_book_cycle("deepseek")
    if result.get("status") != "skipped":
        logger.info("Deep Seek book cycle: %s", result)


async def _book_matt_once() -> None:
    """Matt + LLM co-managed book — nightly ADVISORY analyst cycle: same research
    session and inbox report, structurally zero proposals and zero lane rows."""
    from research.book_driver import run_book_cycle

    result = await run_book_cycle("matt")
    if result.get("status") != "skipped":
        logger.info("Matt+LLM book cycle: %s", result)


_book_deadman_last_date: dict = {}


async def _book_deadman_once() -> None:
    """Books dead-man (spec §6): any book with no report by 20:00 ET gets flagged
    — inbox + push (severity=warning). Once per ET day; the in-memory stamp
    resetting on restart re-checks, which is the safe direction."""
    from db.reports import report_exists_for_day
    from research.books import BOOKS
    from signals.notify import notify

    now_et = datetime.now(_ET)
    if now_et.weekday() >= 5 or now_et.hour < 20:
        return
    today = now_et.date()
    if _book_deadman_last_date.get("date") == today:
        return
    missing = [
        b.name
        for b in BOOKS.values()
        if not b.paused
        and not report_exists_for_day(
            f"book_{b.book_id}", today, dedup_key=f"book_{b.book_id}"
        )
    ]
    _book_deadman_last_date["date"] = today
    if not missing:
        return
    await notify(
        kind="book_deadman",
        title=f"Book dead-man: {len(missing)} report(s) missing tonight",
        body="No nightly report filed by 20:00 ET: " + ", ".join(sorted(missing)),
        severity="warning",
    )
    logger.warning("book dead-man fired: missing=%s", missing)


async def _db_retention_once() -> None:
    from db.earnings_alerts import prune_earnings_alerts_sent
    from db.equity_tape import prune as prune_equity_tape
    from db.retention import compress_research_captures, prune_poll_history

    now_et = datetime.now(_ET)
    if not (1 <= now_et.hour < 5):
        return
    today = now_et.date()
    if _db_retention_last_date.get("date") == today:
        return
    counts = await asyncio.to_thread(prune_poll_history)
    # RVOL alert-dedup keys grow ~1 row/symbol/day forever — prune the old ones.
    pruned_alerts = await asyncio.to_thread(prune_earnings_alerts_sent)
    # Equity tape events (blocks + dark-pool prints) age out past EVENTS_PRUNE_DAYS.
    pruned_tape = await asyncio.to_thread(prune_equity_tape)
    # Prior sessions' research capture JSONL (print lab + chain samples) gzip
    # losslessly (~14x) once immutable — never downsampled (lab ground truth).
    compressed = await asyncio.to_thread(compress_research_captures)
    _db_retention_last_date["date"] = today
    logger.info(
        "DB retention complete: %s (earnings_alerts_sent pruned=%s, equity_tape "
        "pruned=%s, research_jsonl=%s)",
        counts,
        pruned_alerts,
        pruned_tape,
        compressed,
    )


async def _schwab_snapshot_once() -> None:
    now_et = datetime.now(_ET)
    today = now_et.date()
    if now_et.weekday() >= 5:  # weekend — no trading day
        return
    after_close = now_et.hour > 16 or (now_et.hour == 16 and now_et.minute >= 5)
    if not after_close:
        return
    if _schwab_snapshot_last_date.get("date") == today:
        return
    from brokers.schwab_accounts import get_schwab_accounts
    from brokers.schwab_auth import token_exists

    if not token_exists():
        return
    data = await get_schwab_accounts(force=True)
    if data.get("authenticated"):
        _schwab_snapshot_last_date["date"] = today
        logger.info("Schwab EOD equity snapshot captured for %s", today)




# ---------------------------------------------------------------------------
# Filings feed: insider Form 4 buys + 13D stakes (EDGAR, near-real-time) and
# congressional trades (STOCK Act, legally up to 45 days stale). A feed the
# founder eyeballs — no scoring, no alerts, no outcome machinery.
# ---------------------------------------------------------------------------
async def _filings_edgar_once() -> None:
    from db.filings import has_uid, insert_events, remember_uid
    from signals.filings.edgar import poll_13d_once, poll_form4_once

    def _arm(poller, label: str) -> tuple[list, bool]:
        try:
            return poller(), True
        except Exception:
            logger.exception("Filings EDGAR %s poll failed", label)
            return [], False

    def _pass() -> None:
        form4, form4_ok = _arm(
            lambda: poll_form4_once(
                seen=has_uid,
                remember=remember_uid,
                min_usd=FILINGS_INSIDER_MIN_USD,
            ),
            "Form 4",
        )
        d13, d13_ok = _arm(lambda: poll_13d_once(seen=has_uid), "13D")
        inserted = insert_events(form4 + d13)
        if inserted:
            logger.info("Filings feed: %d new EDGAR event(s)", inserted)
        if not form4_ok and not d13_ok:
            raise RuntimeError("both Form 4 and 13D pollers failed")

    await asyncio.to_thread(_pass)


async def _filings_congress_once() -> None:
    from db.filings import insert_events
    from signals.filings.congress import poll_congress_once

    def _pass() -> int:
        return insert_events(poll_congress_once())

    inserted = await asyncio.to_thread(_pass)
    if inserted:
        logger.info("Filings feed: %d new congressional trade(s)", inserted)


async def _filings_initial_fill() -> None:
    """Jobs sleep a full interval before their first pass (6 h for congress) —
    seed the feed shortly after boot so a deploy never blanks it."""
    if not _FILINGS_FEED_ENABLED:
        return
    await asyncio.sleep(15)
    for fn in (_filings_congress_once, _filings_edgar_once):
        try:
            await fn()
        except Exception:
            logger.exception("Filings initial fill failed: %s", fn.__name__)


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
        ("bars_prewarm", _BARS_PREWARM_INTERVAL, None),
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


_loop_probe = LoopProbe()


@asynccontextmanager
async def lifespan(app: FastAPI):
    if not auth_enabled():
        logger.warning(
            "API_TOKEN is not set — API and WebSocket routes are unauthenticated. "
            "Set API_TOKEN in .env for production-like local security."
        )
    from config.settings import push_config

    push_cfg = push_config()
    if (
        push_cfg["enabled"]
        and str(push_cfg.get("provider") or "").strip().lower() == "apns"
        and push_cfg.get("apns_use_sandbox")
    ):
        logger.warning(
            "APNs is in SANDBOX mode — production builds will not receive pushes; "
            "set APNS_USE_SANDBOX=0 for TestFlight/App Store builds"
        )
    init_db()
    init_chart_features()
    init_workspace_tables()
    init_ai_tables()
    init_idea_outcomes_tables()
    init_filings_tables()
    init_session_node_tables()
    init_earnings_alert_tables()
    init_reports_tables()
    init_notifications_tables()
    from db.fusion import init_fusion_tables

    init_fusion_tables()
    from db.news import init_db as init_news_tables

    init_news_tables()
    init_chat_tables()
    init_trade_tables()
    init_paper_order_tables()
    init_push_delivery_tables()
    init_device_token_tables()
    init_live_activity_tables()
    init_execution_tables()
    migrate_multi_account()
    init_strategy_tables()
    seed_default_strategies()
    try:
        delete_rule("session_node_spy")
    except Exception:
        logger.exception("Failed to remove migrated session_node_spy rule")
    if os.getenv("HMT_TEST_LIFESPAN", "").strip().lower() == "minimal":
        # Test-mode lifespan (set by tests/conftest.py, never by a unit file):
        # schema init + job REGISTRATION only. The full startup runs a real
        # network _poll_once inline and spawns ~17 loops whose shutdown
        # cancel cannot interrupt sync work inside asyncio.to_thread — the
        # TestClient suite-hang class. Read lazily so a test can opt back
        # into the full lifespan by clearing the env before TestClient(app).
        _register_background_jobs()
        yield
        return
    await market_hub.start()
    for account in list_accounts(enabled_only=True):
        if str(account["broker"]).strip().lower() != "alpaca":
            continue
        acct_id = str(account["id"])
        connection_id = account.get("connection_id")
        if connection_id:
            connection = get_connection(str(connection_id))
            if connection is None or not connection_is_configured(connection):
                continue
        elif not broker_is_configured("alpaca"):
            continue
        if count_pending_executions("alpaca", account_id=acct_id) > 0:
            await reconcile_pending_executions("alpaca", account_id=acct_id)
    for name in get_enabled_broker_names():
        if (
            name == "schwab"
            and broker_is_configured(name)
            and count_pending_executions(name) > 0
        ):
            await reconcile_pending_executions(name)
    await _poll_once()
    _register_background_jobs()
    background_tasks = _loop_probe.start() + start_all() + [
        asyncio.create_task(_poll_loop()),
        asyncio.create_task(_signal_scan_loop()),
        asyncio.create_task(_session_node_loop()),
        asyncio.create_task(_alert_symbol_loop()),
        asyncio.create_task(_trade_sync_loop()),
        asyncio.create_task(_proposal_sweeper_loop()),
        asyncio.create_task(_idea_tracker_loop()),
        asyncio.create_task(_filings_initial_fill()),
        asyncio.create_task(social_buzz_loop()),
        asyncio.create_task(_earnings_refresh_loop()),
        asyncio.create_task(_earnings_release_loop()),
        asyncio.create_task(_regime_refresh_loop()),
        asyncio.create_task(_regime_sleeves_loop()),
        asyncio.create_task(_reports_scheduler_loop()),
        asyncio.create_task(_bars_prewarm_loop()),
        asyncio.create_task(_equity_tape_flush_loop()),
    ]
    if _NEWS_STREAM_ENABLED:
        from signals.news_stream import news_stream_loop

        background_tasks.append(
            asyncio.create_task(
                news_stream_loop(
                    mark_alive=lambda: mark_alive("news_stream")
                )
            )
        )
    if _OPTIONS_FLOW_ENABLED and _OPTIONS_FLOW_STREAM_ENABLED:
        if _OPTIONS_FLOW_EXTERNAL_COLLECTOR:
            logger.info(
                "options_flow: external collector owns the pipeline "
                "(OPTIONS_FLOW_EXTERNAL_COLLECTOR=1); not starting in-process"
            )
        else:
            background_tasks.append(asyncio.create_task(_flow_runner.stream_run()))
            background_tasks.append(asyncio.create_task(_flow_runner.sweep_loop()))
    yield
    _loop_probe.stop()
    _flow_runner.stop()
    for task in background_tasks:
        task.cancel()
    for task in background_tasks:
        try:
            await task
        except asyncio.CancelledError:
            pass
    await market_hub.stop()
    await aclose_broker_cache()


app = FastAPI(title="Help Me Trade", lifespan=lifespan)
app.include_router(ai_router)
app.include_router(device_router)
app.include_router(trade_router)
app.include_router(performance_router)
app.include_router(strategy_router)
app.include_router(strategy_lab_router)
app.include_router(notification_router)
app.include_router(scan_router)
app.include_router(schwab_router)
app.include_router(journal_router)
app.include_router(fill_orphan_router)
app.include_router(ticker_router)
app.include_router(social_router)
app.include_router(orders_router)
app.include_router(alert_drawing_router)
app.include_router(fusion_router)
app.include_router(status_page_router)
app.include_router(workspace_router)
app.include_router(reports_router)
app.include_router(bars_router)
app.include_router(scanner_router)
app.include_router(flow_router)
app.include_router(watchlist_router)

app.add_middleware(SecurityMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=frontend_origins(),
    allow_credentials=True,
    allow_methods=["GET", "PUT", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["*"],
    # Every poll is a cross-origin fetch carrying X-API-Token, so each one needs a
    # cached preflight or it doubles into an OPTIONS + GET pair. Browsers clamp
    # this to their own ceiling (Chromium hours, Safari 600s); a day is simply the
    # longest any of them will honour.
    max_age=86_400,
)


@app.get("/health")
def health() -> dict[str, int | str | float | bool | list | None]:
    age = poll_health.age_seconds()
    try:
        db_size_bytes = os.stat(DEFAULT_DB_PATH).st_size
    except OSError:
        db_size_bytes = None
    return {
        "status": "ok",
        "poll_interval_seconds": POLL_INTERVAL_SECONDS,
        "last_poll_age_seconds": age,
        "poll_healthy": poll_health.is_healthy(POLL_INTERVAL_SECONDS),
        "jobs": jobs_health(),
        "db_size_bytes": db_size_bytes,
        "newest_backup_age_s": newest_backup_age_s(),
        "off_box_pull_age_s": off_box_pull_age_s(),
        # Both safety states, reported independently. kill_switch = total freeze
        # (human-only); entries_halted = HALT (blocks exposure-increasing orders,
        # exits still flow; armed by the daily-loss breaker or a human).
        "kill_switch_active": is_kill_switch_active(),
        "entries_halted": is_entries_halted(),
    }


@app.websocket("/ws/market")
async def ws_market(websocket: WebSocket):
    if not await verify_websocket_auth(websocket):
        return
    await market_hub.handle_client(websocket)


@app.get("/api/alerts/recent")
def api_alerts_recent() -> dict[str, list[dict]]:
    return {"alerts": market_hub.recent_alerts()}


def _alpaca_data_feed_block(*, recheck: bool = False) -> dict | None:
    """Fail-soft Alpaca SIP/IEX tier snapshot for /api/broker."""
    if not broker_is_configured("alpaca"):
        return None
    if recheck:
        clear_alpaca_tier_cache()
    adapter = None
    for getter in (get_market_data_provider, get_broker):
        try:
            adapter = getter("alpaca")
            break
        except Exception:
            continue
    if adapter is None:
        return None
    try:
        configured = alpaca_config()["data_feed"]
        tier = data_tier(adapter)
        get_data = getattr(adapter, "_get_data", None)
        probe = get_data if configured == "auto" else None
        feed = effective_alpaca_feed(configured_feed=configured, get_data=probe)
        return {
            "configured": configured,
            "tier": tier,
            "feed": feed,
            "overnight_feed": "boats" if tier == "paid" else "overnight",
        }
    except Exception as exc:
        logger.warning("[broker] alpaca data_feed block failed: %s", exc)
        return None


@app.get("/api/broker")
def api_broker(recheck_feed: bool = Query(False)):
    settings = load_settings()
    primary = primary_broker()
    brokers_payload: dict = {}

    for name in ("alpaca", "schwab"):
        cfg = settings["brokers"].get(name, {})
        entry: dict = {
            "enabled": bool(cfg.get("enabled")),
            "configured": broker_is_configured(name),
        }
        if name == "alpaca":
            data_feed = _alpaca_data_feed_block(recheck=recheck_feed)
            if data_feed is not None:
                entry["data_feed"] = data_feed
        if name == "schwab":
            from brokers.schwab_auth import auth_status

            entry["auth"] = auth_status()
        brokers_payload[name] = entry

    return {
        "broker": primary,
        "primary_broker": primary,
        "brokers": brokers_payload,
    }


def _account_money_type(broker: str) -> str:
    """Real funded money vs paper sandbox — the firewall property.

    Schwab = real funded accounts (read-only). Alpaca is the paper sandbox in this
    app (the combined book is hard paper-fenced). Kept as one explicit derivation
    so the paper/real boundary is a real property, never a broker-name guess
    scattered across the UI. (Revisit if a live Alpaca endpoint is ever wired.)
    """
    return "real" if broker == "schwab" else "paper"


def _aggregate_account_snapshots(snaps: list[dict], money_type: str = "paper") -> dict:
    """Sum balances across accounts of a SINGLE money_type only.

    Hard firewall: paper (Alpaca) and real (Schwab) balances are NEVER blended
    into one figure. The caller picks one money_type; any snapshot of a different
    money_type is skipped — belt-and-suspenders so a future change that starts
    writing real accounts into poll_runs alongside paper ones can't silently
    reintroduce a mixed total.
    """
    cash = bp = pv = upl = 0.0
    counted = 0
    for s in snaps:
        if _account_money_type(str(s.get("broker") or "")) != money_type:
            continue
        counted += 1
        acct = s.get("account") or {}
        cash += float(acct.get("cash") or 0)
        bp += float(acct.get("buying_power") or 0)
        pv += float(acct.get("portfolio_value") or 0)
        for pos in s.get("positions") or []:
            upl += float(pos.get("unrealized_pl") or 0)
    return {
        "cash": cash,
        "buying_power": bp,
        "portfolio_value": pv,
        "unrealized_pl": upl,
        "account_count": counted,
        "money_type": money_type,
    }


def _combined_snapshot_response() -> dict | None:
    all_snaps = get_all_latest_snapshots()
    if not all_snaps:
        return None

    # Drop legacy NULL-account snapshots when real per-account ones exist.
    labeled = [s for s in all_snaps if s.get("account_id")]
    if labeled:
        all_snaps = labeled

    # Attach a stable trade-account label + default flag + money_type to each snap.
    for s in all_snaps:
        acct = get_account(str(s["account_id"])) if s.get("account_id") else None
        s["account_label"] = acct["label"] if acct else None
        s["is_default"] = bool(acct["is_default"]) if acct else False
        s["money_type"] = _account_money_type(str(s.get("broker") or ""))
        if s.get("account_id") and s.get("positions"):
            entered = entry_dates_by_symbol(str(s["account_id"]))
            for p in s["positions"]:
                p["entered_at"] = entered.get(p.get("symbol"))

    primary = primary_broker()
    # Top-level summary is pinned to the DEFAULT account (deterministic) rather
    # than whichever account polled last — that last-one-wins behavior was the
    # source of the dashboard flicker between accounts.
    primary_snap = (
        next((s for s in all_snaps if s.get("is_default")), None)
        or next((s for s in all_snaps if s["broker"] == primary), None)
        or all_snaps[0]
    )

    primary_money_type = primary_snap.get("money_type") or _account_money_type(
        str(primary_snap.get("broker") or "")
    )
    return {
        **primary_snap,
        "primary_broker": primary,
        "broker": primary_snap["broker"],
        "money_type": primary_money_type,
        "snapshots": all_snaps,
        # Paper-only aggregate — Schwab (real) is summed separately on the client
        # from GET /api/schwab/accounts; the two are never combined (the firewall).
        "all_accounts_summary": _aggregate_account_snapshots(all_snaps, "paper"),
    }


@app.get("/api/snapshot")
def api_snapshot():
    data = _combined_snapshot_response()
    if data is None:
        return JSONResponse({"error": "No snapshot yet"}, status_code=503)
    return data


@app.get("/api/settings")
def api_get_settings():
    return mask_settings(load_settings())


@app.put("/api/settings")
def api_put_settings(request: Request, patch: dict = Body(...)):
    if "trading" in patch:
        trading = patch.get("trading")
        if isinstance(trading, dict) and "kill_switch_active" in trading:
            return JSONResponse(
                {"error": "use /api/kill-switch"},
                status_code=400,
            )
        if isinstance(trading, dict) and "entries_halted" in trading:
            return JSONResponse(
                {"error": "use /api/entries-halt"},
                status_code=400,
            )
        try:
            verify_admin_token(request)
        except HTTPException as exc:
            return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
    try:
        updated = apply_settings_patch(patch)
        clear_llm_cache()
        clear_broker_cache()
    except (TypeError, ValueError) as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    return mask_settings(updated)


@app.post("/api/kill-switch")
async def api_kill_switch(request: Request, body: dict = Body(...)):
    active = body.get("active")
    if not isinstance(active, bool):
        return JSONResponse(
            {"error": "active must be a boolean"},
            status_code=400,
        )
    if not active:
        try:
            verify_admin_token(request)
        except HTTPException as exc:
            return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)

    try:
        updated = apply_settings_patch({"trading": {"kill_switch_active": active}})
    except (TypeError, ValueError) as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)

    state = bool(updated.get("trading", {}).get("kill_switch_active", active))

    from signals.notify import notify

    if active:
        title = "Kill switch armed"
        body_text = "All new order submissions are blocked until disarmed."
    else:
        title = "Kill switch disarmed"
        body_text = "Order submissions may resume per current risk limits."
    await notify(
        kind="kill_switch",
        title=title,
        body=body_text,
        severity="critical",
        push=True,
    )

    return {"kill_switch_active": state}


@app.post("/api/entries-halt")
async def api_entries_halt(request: Request, body: dict = Body(...)):
    active = body.get("active")
    if not isinstance(active, bool):
        return JSONResponse(
            {"error": "active must be a boolean"},
            status_code=400,
        )
    # HALT uses symmetric normal-token auth (no admin gate on disarm): it is
    # far less dangerous than the kill switch — it only blocks exposure-
    # increasing orders (exits keep flowing), and after a hard breaker trip the
    # per-account breaker stage independently keeps entries blocked until the ET
    # day rolls, so disarming HALT alone cannot resume entries mid-bleed.
    try:
        updated = apply_settings_patch({"trading": {"entries_halted": active}})
    except (TypeError, ValueError) as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)

    state = bool(updated.get("trading", {}).get("entries_halted", active))

    from signals.notify import notify

    if active:
        title = "Entries halted"
        body_text = "New entries and adds are blocked; protective exits still flow."
    else:
        title = "Entries resumed"
        body_text = "New entries may resume, subject to current risk limits."
    # The state change already persisted — a notify failure must not 500 the
    # request (state and response must agree). Log and return the real state.
    try:
        await notify(
            kind="entries_halt",
            title=title,
            body=body_text,
            severity="critical",
            push=True,
        )
    except Exception:
        logger.warning("entries-halt notify failed (state persisted)", exc_info=True)

    return {"entries_halted": state}


@app.get("/api/chart-layout")
def api_get_chart_layout():
    return {"layout": get_chart_layout()}


@app.put("/api/chart-layout")
def api_put_chart_layout(body: dict = Body(...)):
    layout = body.get("layout")
    if not isinstance(layout, list):
        return JSONResponse({"error": "layout must be a list"}, status_code=400)
    return {"layout": save_chart_layout(layout)}


@app.get("/api/symbols/search")
async def api_symbol_search(q: str = "", limit: int = 10):
    broker = _require_alpaca()
    if isinstance(broker, JSONResponse):
        return broker
    if limit < 1 or limit > 25:
        return JSONResponse({"error": "limit must be 1–25"}, status_code=400)
    try:
        results = await broker.asearch_symbols(q, limit=limit)
    except BrokerError as exc:
        return JSONResponse({"error": str(exc)}, status_code=502)
    # Index option roots aren't Alpaca assets so the broker search never
    # surfaces them, but the options chain serves them (INDEX_OPTION_ROOTS).
    qn = q.strip().upper()
    if qn:
        from brokers.options_chain import INDEX_OPTION_ROOTS

        idx = sorted(s for s in INDEX_OPTION_ROOTS if s.startswith(qn))
        if idx:
            results = idx + [r for r in results if r not in idx]
            results = results[:limit]
    return {"query": q, "symbols": results}


@app.get("/api/symbols/overnight")
async def api_symbols_overnight(symbols: str = ""):
    broker = _require_alpaca()
    if isinstance(broker, JSONResponse):
        return broker

    symbol_list = [s.strip().upper() for s in symbols.split(",") if s.strip()]
    if not symbol_list:
        return {"overnight_tradable": []}

    try:
        overnight = await broker.aovernight_tradable_symbols()
    except BrokerError as exc:
        return JSONResponse({"error": str(exc)}, status_code=502)

    return {
        "overnight_tradable": [s for s in symbol_list if s in overnight],
    }


@app.get("/api/positions")
def api_positions():
    data = get_latest_snapshot()
    if data is None:
        return JSONResponse({"error": "No snapshot yet"}, status_code=503)
    return {
        "captured_at": data["captured_at"],
        "position_count": data["position_count"],
        "positions": data["positions"],
    }


@app.get("/api/orders")
def api_orders():
    data = get_latest_snapshot()
    if data is None:
        return JSONResponse({"error": "No snapshot yet"}, status_code=503)
    return {
        "captured_at": data["captured_at"],
        "open_order_count": data["open_order_count"],
        "open_orders": data["open_orders"],
    }


@app.get("/api/account")
def api_account():
    data = get_latest_snapshot()
    if data is None:
        return JSONResponse({"error": "No snapshot yet"}, status_code=503)
    return {"captured_at": data["captured_at"], **data["account"]}


@app.get("/api/portfolio/history")
async def api_portfolio_history(
    range: str = "1W", account_id: str | None = None, source: str | None = None
):
    valid = {"1D", "3D", "1W", "1M", "1Y", "ALL"}
    if range not in valid:
        return JSONResponse(
            {"error": f"Invalid range. Use one of: {', '.join(sorted(valid))}"},
            status_code=400,
        )

    # Two scoped sources. `all_paper` (or an explicit source=snapshots) is served
    # from the poller's own snapshot tables — aggregate/per-account curves the
    # broker API can't provide, plus window change/realized/unrealized (mobile
    # Home). A bare acct_* keeps the Lab's broker-backed passthrough; no
    # account_id keeps the original default-broker behavior.
    if account_id == "all_paper" or (account_id and source == "snapshots"):
        data = equity_history(account_id, range)
        if data is None:
            return JSONResponse(
                {"error": f"Unknown or non-paper account: {account_id}"},
                status_code=404,
            )
        return data

    if account_id:
        try:
            resolved = get_broker_for_account(account_id)
        except BrokerError as exc:
            return JSONResponse({"error": str(exc)}, status_code=502)
        if not isinstance(resolved, AlpacaAdapter):
            return JSONResponse(
                {"error": "Portfolio history requires an Alpaca account"},
                status_code=400,
            )
        broker: AlpacaAdapter = resolved
    else:
        default_broker = _require_alpaca()
        if isinstance(default_broker, JSONResponse):
            return default_broker
        broker = default_broker

    try:
        points = await broker.aget_portfolio_history(range)
    except BrokerError as exc:
        return JSONResponse({"error": str(exc)}, status_code=502)

    return {
        "range": range,
        "points": [
            {
                "timestamp": p.timestamp,
                "equity": p.equity,
                "profit_loss": p.profit_loss,
                "profit_loss_pct": p.profit_loss_pct,
            }
            for p in points
        ],
    }


@app.get("/api/session-node/levels")
async def api_session_node_levels(symbol: str = "SPY"):
    sym = symbol.strip().upper()
    if not sym:
        return JSONResponse({"error": "symbol required"}, status_code=400)
    try:
        payload = await compute_session_levels(sym)
    except BrokerError:
        payload = None
    if payload is None:
        return {"symbol": sym, "session_date": None, "levels": []}
    return payload


def _enrich_open_idea(row: dict) -> dict:
    """Attach expires_on + zone_distance_pct so a pending row can explain
    itself (why still pending, when it times out). Fail-soft per field."""
    from decimal import Decimal, InvalidOperation

    from research.idea_tracker import (
        IDEA_TTL_DAYS,
        RESOLVE_TTL_DAYS,
        _add_trading_days,
        _parse_as_of_date,
    )

    try:
        if row.get("status") == "triggered" and row.get("triggered_at"):
            base = _parse_as_of_date(str(row["triggered_at"]))
            ttl = RESOLVE_TTL_DAYS
        else:
            base = _parse_as_of_date(str(row["as_of"]))
            ttl = IDEA_TTL_DAYS
        row["expires_on"] = _add_trading_days(base, ttl).isoformat()
    except Exception:
        row["expires_on"] = None

    row["zone_distance_pct"] = None
    try:
        last = Decimal(str(row.get("last_price") or "").strip())
        lo = Decimal(str(row["entry_low"]))
        hi = Decimal(str(row["entry_high"]))
        if lo <= last <= hi:
            row["zone_distance_pct"] = 0.0
        elif last > hi > 0:
            row["zone_distance_pct"] = float((last - hi) / hi * 100)
        elif lo > 0:
            row["zone_distance_pct"] = float((last - lo) / lo * 100)
    except (InvalidOperation, ValueError, TypeError, KeyError):
        pass
    return row


@app.get("/api/filings")
def api_filings(kinds: str | None = None, limit: int = 100):
    """Filings feed rows, newest first. kinds = comma list of
    insider_buy | stake_13d | congress_trade (omit for all)."""
    from db.filings import list_recent

    wanted = [k.strip() for k in kinds.split(",") if k.strip()] if kinds else None
    return {"events": list_recent(kinds=wanted, limit=limit)}


@app.get("/api/ideas/outcomes")
def api_idea_outcomes():
    from db.idea_outcomes import (
        get_summary,
        list_recent_open,
        list_recent_resolved,
    )

    return {
        "summary": get_summary(),
        "open": [_enrich_open_idea(r) for r in list_recent_open()],
        "resolved": list_recent_resolved(limit=50),
    }


@app.get("/api/ideas/ledger")
def api_idea_ledger(
    origin: str | None = None,
    status: str | None = None,
    limit: int = Query(200, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    from db.idea_outcomes import list_ledger_rows

    return list_ledger_rows(
        origin=origin,
        status=status,
        limit=limit,
        offset=offset,
    )


def _earnings_quarter_label(fiscal_quarter: int, period_iso: str) -> str:
    """Label an earnings report by the company's own fiscal quarter NUMBER, but with the
    CALENDAR year of the period (not the fiscal year). Offset-fiscal names (NVDA, AAPL)
    report e.g. "Q1 fiscal 2027" for a quarter that lands in calendar 2026 — Finnhub's raw
    fiscal year gives the confusing forward "Q1'27", so use the period's calendar year →
    "Q1'26" (matches how the company + news refer to the quarter). Calendar-fiscal names
    are unaffected (fiscal quarter == calendar quarter)."""
    year = date.fromisoformat(period_iso[:10]).year
    return f"Q{fiscal_quarter}'{year % 100:02d}"


@app.get("/api/fundamentals/earnings-history")
async def api_earnings_history(symbol: str = "", n: int = 6):
    """Past earnings EPS surprise by quarter.

    Query params:
      symbol — required ticker (case-insensitive)
      n      — number of most-recent quarters to return (default 6, max 20)

    Returns:
      {
        "symbol": str,
        "events": [
          {
            "period": "YYYY-MM-DD",
            "label": "Q2'26",
            "eps_actual": float | null,
            "eps_estimate": float | null,
            "surprise_pct": float | null,
          },
          ...
        ]
      }
      Events are ordered most-recent first. Empty events list on any data error.
      Never returns 500; returns 400 if symbol is missing.
    """
    from signals.finnhub import afetch_eps_surprises

    sym = symbol.strip().upper()
    if not sym:
        return JSONResponse({"error": "symbol is required"}, status_code=400)
    if n < 1 or n > 20:
        n = min(max(n, 1), 20)

    try:
        raw = await afetch_eps_surprises(sym, limit=n)
    except Exception:  # noqa: BLE001
        return {"symbol": sym, "events": []}

    events: list[dict] = []
    for item in raw:
        try:
            label = _earnings_quarter_label(int(item["quarter"]), str(item["period"]))
            events.append(
                {
                    "period": item["period"],
                    "label": label,
                    "eps_actual": item.get("eps_actual"),
                    "eps_estimate": item.get("eps_estimate"),
                    "surprise_pct": item.get("surprise_pct"),
                }
            )
        except (KeyError, TypeError, ValueError):
            continue

    return {"symbol": sym, "events": events}






