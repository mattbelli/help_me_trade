"""FastAPI app: poll Alpaca, store snapshots, serve JSON + simple HTML."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import tempfile
import time
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from fastapi import Body, FastAPI, HTTPException, Query, Request, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse

from ai.llm.factory import clear_llm_cache
from ai.reflection.scheduler import REFLECTION_INTERVAL_SECONDS, reflection_once
from ai.rules.engine import evaluate_on_snapshot
from api.ai_routes import router as ai_router
from api.auth import (
    auth_enabled,
    frontend_origins,
    verify_admin_token,
    verify_mutation_token,
    verify_websocket_auth,
)
from api.device_routes import router as device_router
from api.fill_orphan_routes import router as fill_orphan_router
from api.jobs import (
    jobs_health,
    mark_alive,
    newest_backup_age_s,
    register_job,
    register_watchdog_job,
    start_all,
)
from api.journal_routes import router as journal_router
from api.middleware import SecurityMiddleware
from api.notification_routes import router as notification_router
from api.orders_routes import router as orders_router
from api.performance_routes import router as performance_router
from api.poll_health import poll_health
from api.scan_routes import router as scan_router
from api.schwab_routes import router as schwab_router
from api.social_routes import router as social_router
from api.strategy_routes import router as strategy_router
from api.ticker_routes import router as ticker_router
from api.trade_routes import router as trade_router
from brokers.alpaca import AlpacaAdapter, StockBar
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
from config.settings import (
    SCHEDULABLE_REPORT_KINDS,
    add_watchlist_symbol,
    alpaca_config,
    apply_settings_patch,
    broker_is_configured,
    create_watchlist,
    delete_watchlist,
    get_chart_layout,
    get_watchlist,
    get_watchlists_state,
    is_entries_halted,
    is_kill_switch_active,
    load_settings,
    mask_settings,
    primary_broker,
    remove_watchlist_symbol,
    reports_config,
    save_chart_layout,
    save_watchlist,
    set_active_watchlist,
    update_watchlist,
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
from db.chart_features import (
    create_chart_drawing,
    create_price_alert,
    delete_chart_drawing,
    delete_price_alert,
    init_chart_features,
    list_chart_drawings,
    list_price_alerts,
    mark_alert_triggered,
    update_chart_drawing,
    update_price_alert,
)
from db.chat_sessions import init_chat_tables
from db.device_tokens import init_device_token_tables
from db.earnings_alerts import init_earnings_alert_tables
from db.earnings_calendar import earnings_hour, last_earnings_date, next_earnings_date
from db.filings import init_filings_tables
from db.idea_outcomes import init_idea_outcomes_tables
from db.live_activities import init_live_activity_tables
from db.multi_account import migrate_multi_account
from db.notifications import init_notifications_tables
from db.order_executions import (
    count_pending_paper_executions,
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
from db.workspace import (
    init_workspace_tables,
    is_valid_custom_themes,
    load_custom_themes,
    load_workspace_layout,
    load_workspace_pages,
    save_custom_themes,
    save_workspace_layout,
    save_workspace_pages,
)
from signals import indicators
from signals.earnings_refresh import arefresh_earnings_calendar
from signals.momentum import refresh_universe_bars
from signals.options_flow.runner import OptionsFlowRunner
from signals.regime import refresh_regime_macro
from signals.regime_sleeves import run_regime_sleeves_scan
from signals.scan import run_signal_scan
from signals.session_node_strategy import compute_session_levels, run_session_node_scan
from signals.social_buzz import social_buzz_loop
from signals.strategy_kinds import strategy_kind
from signals.strategy_scan import run_strategy_scan
from signals.strategy_universe import resolve_universe_spec
from signals.volume_profile import build_profile, load_profile, profile_dir, rvol_now
from trading.exit_engine import run_exit_engine
from trading.fill_tracker import reconcile_open_executions, reconcile_pending_executions
from trading.market_hours import _ET, _SESSION_CLOSE, _SESSION_OPEN
from trading.proposal_intake import propose_from_scan_fire
from trading.sync import async_sync_all_enabled_brokers

ROOT = Path(__file__).resolve().parent.parent
BOARD_JSON_PATH = ROOT / "References" / "board.json"
_EMPTY_BOARD: dict = {"updated_at": None, "lanes": [], "cards": []}
POLL_INTERVAL_SECONDS = max(5, int(os.environ.get("POLL_INTERVAL_SECONDS", "20")))
SIGNAL_SCAN_INTERVAL_SECONDS = max(30, int(os.environ.get("SIGNAL_SCAN_INTERVAL_SECONDS", "300")))
# Session-node reacts to SPY touching a level on the 1-min chart, so it runs on its
# OWN fast cadence (default 60s = the bar granularity) — decoupled from the heavy
# 5-min signal scan so we don't also run the expensive daily-bar scans every minute.
SESSION_NODE_SCAN_INTERVAL_SECONDS = max(
    20, int(os.environ.get("SESSION_NODE_SCAN_INTERVAL_SECONDS", "60"))
)
EARNINGS_REFRESH_INTERVAL_SECONDS = 24 * 3600
REGIME_REFRESH_INTERVAL_SECONDS = 24 * 3600
# Regime sleeves evaluate completed daily bars after the close — a ~10-min cadence is
# plenty (the scan self-gates to the post-close window and dedups per trading day).
REGIME_SLEEVES_SCAN_INTERVAL_SECONDS = max(
    60, int(os.environ.get("REGIME_SLEEVES_SCAN_INTERVAL_SECONDS", "600"))
)
MOMENTUM_BARS_REFRESH_INTERVAL_SECONDS = 24 * 3600
VOLUME_PROFILE_REFRESH_INTERVAL_SECONDS = 24 * 3600
RVOL_ALERT_INTERVAL_SECONDS = max(60, int(os.environ.get("RVOL_ALERT_INTERVAL_SECONDS", "300")))
RVOL_ALERT_THRESHOLD = float(os.environ.get("RVOL_ALERT_THRESHOLD", "3.0"))
_RVOL_ALERTS_ENABLED = os.environ.get("RVOL_ALERTS_ENABLED", "1").strip() not in (
    "0",
    "false",
    "no",
)
OPTIONS_FLOW_INTERVAL_SECONDS = float(os.environ.get("OPTIONS_FLOW_INTERVAL_SECONDS", "1800"))
_OPTIONS_FLOW_ENABLED = os.environ.get("OPTIONS_FLOW_ENABLED", "0").strip() not in (
    "0",
    "false",
    "no",
)
_SETUP_SCAN_NIGHTLY_ENABLED = os.environ.get("SETUP_SCAN_NIGHTLY_ENABLED", "1").strip() not in (
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
# Earliest ET clock time (fractional hours) for the nightly setup scan — after
# the close so the day's daily bar is final.
SETUP_SCAN_NIGHTLY_AFTER_ET = float(os.environ.get("SETUP_SCAN_NIGHTLY_AFTER_ET", "17.5"))
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
MORNING_REPORT_INTERVAL_SECONDS = max(
    60, int(os.environ.get("MORNING_REPORT_INTERVAL_SECONDS", "900"))
)
_MORNING_REPORT_ENV_ENABLED = os.environ.get("MORNING_REPORT_ENABLED", "1").strip() not in (
    "0",
    "false",
    "no",
)
MORNING_REPORT_SYMBOL = "SPY"
_BARS_PREWARM_INTERVAL = 15.0
_TRADE_SYNC_INTERVAL_SECONDS = 3600.0
_ALERT_SYMBOL_INTERVAL_SECONDS = 30.0
_PROPOSAL_SWEEP_INTERVAL_SECONDS = 60.0
_IDEA_TRACKER_INTERVAL_MARKET_SECONDS = 1800.0
_IDEA_TRACKER_INTERVAL_OFF_SECONDS = 3600.0
_PROFILE_FRESH_S = 20 * 3600
_PROFILE_BUILD_MIN_INTERVAL_S = 0.6  # ~100/min, safely under Schwab's ~120/min
_UNIVERSE_DIR = Path(__file__).resolve().parent.parent / "signals" / "universes"
_LIQUID_CORE_FILES = ("sp500.json", "russell1000.json", "nasdaq100.json")
logging.basicConfig(
    level=getattr(logging, os.environ.get("LOG_LEVEL", "INFO").upper(), logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
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
        )
        logger.info(
            "Snapshot saved at %s (%s account=%s)",
            captured_at,
            broker_name,
            account_id,
        )
        # Per-account fill reconcile (each account via its own adapter).
        if count_pending_paper_executions(broker_name, account_id=account_id) > 0:
            await reconcile_pending_executions(broker_name, account_id=account_id)
        if count_submitted_executions(broker_name, account_id=account_id) > 0:
            await reconcile_open_executions(broker_name, account_id=account_id)
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
        account, positions, orders = await broker.afetch_snapshot()
        trade_account = get_default_account(broker_name)
        captured_at = await asyncio.to_thread(
            save_snapshot,
            account,
            positions,
            orders,
            broker=broker_name,
            account_id=trade_account["id"] if trade_account else None,
        )
        logger.info("Snapshot saved at %s (%s)", captured_at, broker_name)
        await asyncio.to_thread(compute_and_save_snapshot_analytics, broker_name)
        if count_pending_paper_executions(broker_name) > 0:
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


async def _regime_sleeves_loop() -> None:
    """Regime-local sleeves (generals_dip + vol_event) — propose-only, post-close.

    The scan itself gates to the after-close window, dedups per trading day, and
    no-ops while both sleeves are disabled (their shipped state)."""
    while True:
        try:
            acted = await run_regime_sleeves_scan()
            if acted:
                logger.info("Regime-sleeves scan acted: %s", acted)
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
        await asyncio.sleep(REGIME_REFRESH_INTERVAL_SECONDS)


def _momentum_strategy_enabled() -> bool:
    return any(
        s.get("enabled") and str(s.get("id", "")).startswith("momentum") for s in list_strategies()
    )


async def _momentum_bars_refresh_once() -> int:
    if not _momentum_strategy_enabled():
        return 0
    return await refresh_universe_bars()


async def _momentum_bars_refresh_loop() -> None:
    while True:
        try:
            n = await _momentum_bars_refresh_once()
            if n:
                logger.info("Momentum daily bars refresh upserted %d rows", n)
        except Exception:
            logger.exception("Momentum bars refresh loop iteration failed")
        else:
            mark_alive("momentum_bars_refresh")
        await asyncio.sleep(MOMENTUM_BARS_REFRESH_INTERVAL_SECONDS)


def _all_watchlist_symbols() -> list[str]:
    state = get_watchlists_state()
    syms: set[str] = set()
    for lst in state.get("lists", []):
        for s in lst.get("symbols", []):
            if isinstance(s, str) and s.strip():
                syms.add(s.strip().upper())
    return sorted(syms)


def _liquid_core_symbols() -> list[str]:
    syms: set[str] = set()
    for fname in _LIQUID_CORE_FILES:
        try:
            data = json.loads((_UNIVERSE_DIR / fname).read_text())
            for s in data.get("symbols", []):
                if isinstance(s, str) and s.strip():
                    syms.add(s.strip().upper())
        except Exception:
            logger.exception("Failed to load universe file %s", fname)
    return sorted(syms)


def _profile_universe() -> list[str]:
    return sorted(set(_liquid_core_symbols()) | set(_all_watchlist_symbols()))


def _profile_is_fresh(symbol: str) -> bool:
    fp = profile_dir() / f"{symbol}.json"
    try:
        return fp.is_file() and (time.time() - fp.stat().st_mtime) < _PROFILE_FRESH_S
    except OSError:
        return False


async def _volume_profile_refresh_once() -> int:
    symbols = [s for s in _profile_universe() if not _profile_is_fresh(s)]
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


async def _volume_profile_refresh_loop() -> None:
    while True:
        try:
            n = await _volume_profile_refresh_once()
            if n:
                logger.info("Volume profiles refreshed for %d symbols", n)
        except Exception:
            logger.exception("Volume profile refresh loop iteration failed")
        else:
            mark_alive("volume_profile_refresh")
        await asyncio.sleep(VOLUME_PROFILE_REFRESH_INTERVAL_SECONDS)


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
# RVOL-spike alert dedup: persisted in the earnings_alerts_sent key store (a
# generic one-row-per-key table) under key rvol:{symbol}:{ET-date} so a mid-day
# restart does not re-fire already-sent alerts (ops MED-5). The job runs at most
# once per RVOL_ALERT_INTERVAL_SECONDS (default 300s), so a plain DB read per
# symbol per tick is cheap — no in-memory memo needed.
# ---------------------------------------------------------------------------
def _rvol_alert_key(symbol: str, today_et: date) -> str:
    return f"rvol:{symbol}:{today_et.isoformat()}"


def _should_rvol_alert(symbol: str, today_et: date) -> bool:
    """Return True if we have NOT already sent an RVOL alert for this symbol today."""
    from db.earnings_alerts import was_sent

    return not was_sent(_rvol_alert_key(symbol, today_et))


def _mark_rvol_alerted(symbol: str, today_et: date) -> None:
    from db.earnings_alerts import mark_sent

    mark_sent(_rvol_alert_key(symbol, today_et))


async def _rvol_alert_once() -> None:
    if _market_session() != "regular":
        return

    bucket = _rvol_bucket_now()
    if bucket is None:
        return

    symbols = _all_watchlist_symbols()
    if not symbols:
        return

    try:
        provider = get_market_data_provider()
    except BrokerError as exc:
        logger.warning("RVOL alert: provider unavailable: %s", exc)
        return

    if not hasattr(provider, "aget_quotes"):
        return

    from brokers.hybrid_market_data import HybridMarketData
    from brokers.schwab_market_data import SchwabMarketData

    if not isinstance(provider, (SchwabMarketData, HybridMarketData)):
        return

    try:
        quotes = await provider.aget_quotes(symbols)
    except Exception:
        logger.exception("RVOL alert: quote fetch failed")
        return

    today_et = datetime.now(_ET).date()

    from signals.notify import notify

    for sym in symbols:
        try:
            q = quotes.get(sym)
            if q is None:
                continue
            total_volume = q.get("total_volume")
            if total_volume is None or total_volume <= 0:
                continue
            profile = load_profile(sym)
            if profile is None:
                continue
            rvol_val = _compute_rvol_from_total_volume(profile, float(total_volume), bucket)
            if rvol_val is None or rvol_val < RVOL_ALERT_THRESHOLD:
                continue
            if not _should_rvol_alert(sym, today_et):
                continue
            price = q.get("regular_last") or q.get("ext_last")
            price_str = f"${price:.2f}" if price else "n/a"
            await notify(
                kind="rvol",
                title=f"Unusual volume: {sym}",
                body=f"{sym} {rvol_val:.1f}× normal volume ({price_str})",
                severity="info",
                action_url=f"/watchlist/{sym}",
                data={
                    "symbol": sym,
                    "rvol": round(rvol_val, 2),
                    "price": price,
                },
            )
            _mark_rvol_alerted(sym, today_et)
            logger.info("RVOL alert sent: %s rvol=%.2f", sym, rvol_val)
        except Exception:
            logger.exception("RVOL alert: error processing symbol %s", sym)


# ---------------------------------------------------------------------------
# Options-flow EOD scan (Phase 1c): nightly UV-1/UV-2 into options_flow_events.
# Env-gated OFF by default (serv-only; PC order-flow-RAM incident). Pull-only —
# no notifications here (Phase 2 convergence watch).
# ---------------------------------------------------------------------------
_options_flow_ran_dates: set[date] = set()
_options_flow_non_alpaca_logged = False


def _should_options_flow_scan(today_et: date) -> bool:
    return today_et not in _options_flow_ran_dates


def _mark_options_flow_scanned(today_et: date) -> None:
    _options_flow_ran_dates.add(today_et)
    # Bound memory: keep only today and yesterday.
    stale = [d for d in _options_flow_ran_dates if d < today_et - timedelta(days=1)]
    for d in stale:
        _options_flow_ran_dates.discard(d)


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

        symbols = _all_watchlist_symbols()
        event_date = today_et.isoformat()
        summary = run_eod_scan(broker, symbols=symbols, event_date=event_date)
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

            oc = fill_outcomes(broker)
            logger.info("options_flow outcomes: %s", oc)
        except Exception:
            logger.exception("options_flow outcomes: fill failed")
    except Exception:
        logger.exception("options_flow EOD: unexpected failure")


# The intraday flow pipeline (poll + OPRA stream + sweep consumer) lives in
# signals/options_flow/runner.py so the standalone collector process
# (tools/flow_collector.py) can host it instead of this event loop --
# ownership flips via OPTIONS_FLOW_EXTERNAL_COLLECTOR in the shared .env.
_flow_runner = OptionsFlowRunner(watchlist_symbols=_all_watchlist_symbols)


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


async def _earnings_alert_once() -> None:
    """Check watchlist symbols and push earnings milestone alerts (t14/t1/t0)."""
    from db.earnings_alerts import mark_sent, was_sent
    from signals.notify import notify

    today = datetime.now(_ET).date()
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
            if days_until == 0:
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


async def _db_retention_once() -> None:
    from db.earnings_alerts import prune_earnings_alerts_sent
    from db.retention import prune_poll_history

    now_et = datetime.now(_ET)
    if not (1 <= now_et.hour < 5):
        return
    today = now_et.date()
    if _db_retention_last_date.get("date") == today:
        return
    counts = await asyncio.to_thread(prune_poll_history)
    # RVOL alert-dedup keys grow ~1 row/symbol/day forever — prune the old ones.
    pruned_alerts = await asyncio.to_thread(prune_earnings_alerts_sent)
    _db_retention_last_date["date"] = today
    logger.info(
        "DB retention complete: %s (earnings_alerts_sent pruned=%s)",
        counts,
        pruned_alerts,
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


def _morning_report_window(now_et: datetime, hour_et: int) -> bool:
    """True across a ~2-hour morning window from the scheduled hour (ET). Wide enough
    that the 15-min poll always lands inside it regardless of boot timing (a 14-min
    slot could be missed entirely); the per-ET-day dedup still ensures one send."""
    return hour_et <= now_et.hour < hour_et + 2


def _morning_report_enabled() -> bool:
    cfg = reports_config()
    return _MORNING_REPORT_ENV_ENABLED and bool(cfg.get("morning_levels_enabled", True))


async def _morning_levels_report_once() -> None:
    """Generate and push the SPY morning levels report once per ET day."""
    from db.reports import morning_levels_exists_for_day
    from signals.notify import notify
    from signals.reports import generate_morning_levels_report

    now_et = datetime.now(_ET)
    hour_et = int(reports_config().get("hour_et", 8))
    if not _morning_report_window(now_et, hour_et):
        return

    today = now_et.date()
    symbol = MORNING_REPORT_SYMBOL
    if morning_levels_exists_for_day(symbol, today):
        return

    try:
        report = await generate_morning_levels_report(symbol, et_date=today)
    except Exception:
        logger.exception("Morning levels report: compute/save failed")
        return
    if report is None:
        logger.info("Morning levels report: no session data for %s", symbol)
        return

    try:
        await notify(
            kind="report_ready",
            title=report["title"],
            body=report["body"],
            severity="info",
            ref_type="report",
            ref_id=report["id"],
            action_url=f"/reports/{report['id']}",
            data={"report_id": report["id"], "kind": "morning_levels"},
        )
    except Exception:
        logger.exception("Morning levels report: push failed for %s", report["id"])
    else:
        logger.info(
            "Morning levels report sent: id=%s symbol=%s et_date=%s",
            report["id"],
            symbol,
            today.isoformat(),
        )


def _report_push_body(report: dict) -> str:
    """Plaintext push body: HTML briefs push their summary, not the raw markup."""
    if report.get("format") == "html":
        rj = report.get("payload", {}).get("report") or {}
        summary = str(rj.get("summary") or "").strip()
        return summary[:280] or str(report.get("title", "Report ready"))
    return str(report.get("body", ""))


async def _notify_report_ready(report: dict, *, push: bool | None = None) -> None:
    from signals.notify import notify

    kwargs: dict[str, bool] = {}
    if push is not None:
        kwargs["push"] = push
    await notify(
        kind="report_ready",
        title=report["title"],
        body=_report_push_body(report),
        severity="info",
        ref_type="report",
        ref_id=report["id"],
        action_url=f"/reports/{report['id']}",
        data={"report_id": report["id"], "kind": report["kind"]},
        **kwargs,
    )


async def _generate_report_by_kind(
    kind: str,
    *,
    symbols: list[str] | None = None,
    topic: str | None = None,
    et_date: date | None = None,
) -> dict | None:
    """Dispatch a report kind to its generator (shared by scheduler + API)."""
    from signals import reports as reports_mod

    syms = symbols or None
    if kind == "morning_levels":
        sym = syms[0] if syms else MORNING_REPORT_SYMBOL
        return await reports_mod.generate_morning_levels_report(sym, et_date=et_date)
    if kind == "levels":
        return await reports_mod.generate_levels_report(
            syms or [MORNING_REPORT_SYMBOL], et_date=et_date
        )
    if kind == "morning_brief":
        return await reports_mod.generate_morning_brief_report(syms, et_date=et_date)
    if kind == "evening_brief":
        return await reports_mod.generate_evening_brief_report(syms, et_date=et_date)
    if kind == "on_request":
        return await reports_mod.generate_on_request_report(topic or "", syms, et_date=et_date)
    if kind == "flow_decomp":
        return await reports_mod.generate_flow_decomp_report(et_date=et_date)
    return None


async def _run_scheduled_report(job: dict) -> None:
    """Generate + push one configured report job, once per (kind, params, ET-day)."""
    from db.reports import report_exists_for_day

    kind = str(job.get("kind", "")).strip()
    if not job.get("enabled") or kind not in SCHEDULABLE_REPORT_KINDS:
        return

    now_et = datetime.now(_ET)
    if not _morning_report_window(now_et, int(job.get("hour_et", 8))):
        return

    today = now_et.date()
    symbols = [str(s).strip().upper() for s in (job.get("symbols") or []) if str(s).strip()]
    dedup_key = ",".join(symbols) if kind == "levels" else kind
    if report_exists_for_day(kind, today, dedup_key=dedup_key):
        return

    try:
        report = await _generate_report_by_kind(kind, symbols=symbols, et_date=today)
    except Exception:
        logger.exception("Scheduled report generation failed: kind=%s", kind)
        return
    if report is None:
        logger.info("Scheduled report produced nothing: kind=%s", kind)
        return

    try:
        await _notify_report_ready(report)
    except Exception:
        logger.exception("Scheduled report push failed: id=%s", report["id"])
    else:
        logger.info("Scheduled report sent: kind=%s id=%s", kind, report["id"])


async def _reports_scheduler_loop() -> None:
    """Poll every ~15 min; run the legacy morning-levels job plus any configured
    report jobs (morning/evening briefs, extra levels) during their ET windows.

    Env gates:
      MORNING_REPORT_ENABLED              default "1"  (set to "0" to disable all)
      MORNING_REPORT_INTERVAL_SECONDS     default 900
      MORNING_REPORT_HOUR_ET              default 8 (settings hour_et also applies)
    """
    while True:
        await asyncio.sleep(MORNING_REPORT_INTERVAL_SECONDS)
        if not _MORNING_REPORT_ENV_ENABLED:
            continue
        try:
            if _morning_report_enabled():
                try:
                    await _morning_levels_report_once()
                except Exception:
                    logger.exception("Morning levels report loop iteration failed")
            for job in reports_config().get("jobs", []):
                try:
                    await _run_scheduled_report(job)
                except Exception:
                    logger.exception("Scheduled report job failed: %s", job.get("kind"))
        except Exception:
            # Guards the config reads too — this loop must never die (audit LOW-7).
            logger.exception("Reports scheduler loop iteration failed")
        else:
            mark_alive("reports_scheduler")


# ---------------------------------------------------------------------------
# Filings feed: insider Form 4 buys + 13D stakes (EDGAR, near-real-time) and
# congressional trades (STOCK Act, legally up to 45 days stale). A feed the
# founder eyeballs — no scoring, no alerts, no outcome machinery.
# ---------------------------------------------------------------------------
async def _filings_edgar_once() -> None:
    from db.filings import has_uid, insert_events
    from signals.filings.edgar import poll_13d_once, poll_form4_once

    def _pass() -> int:
        events = poll_form4_once(seen=has_uid, min_usd=FILINGS_INSIDER_MIN_USD)
        events += poll_13d_once(seen=has_uid)
        return insert_events(events)

    inserted = await asyncio.to_thread(_pass)
    if inserted:
        logger.info("Filings feed: %d new EDGAR event(s)", inserted)


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
        from db.lane_stats import bump

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


_background_jobs_registered = False
_fusion_shock_until_mono = 0.0


def _fusion_price_from_cache(symbol: str):
    """Read the backend's existing bars caches only; never fetch."""
    from decimal import Decimal

    sym = symbol.strip().upper()
    now_wall = time.time()
    candidates: list[tuple[float, object]] = []
    for (cached_sym, timeframe), (stored_at, bars) in _wl_bars_cache.items():
        if cached_sym == sym and bars:
            ttl = _WL_BARS_TTL_S.get(timeframe, 90.0)
            if now_wall - stored_at <= ttl:
                candidates.append((stored_at, bars[-1]))
    for key, (expires, response) in _BARS_CACHE.items():
        if key and key[0] == sym and expires > time.monotonic():
            bars = response.get("bars") if isinstance(response, dict) else None
            if bars:
                candidates.append((expires, bars[-1]))
    if not candidates:
        return None
    latest = max(candidates, key=lambda item: item[0])[1]
    close = latest.get("close") if isinstance(latest, dict) else getattr(latest, "close", None)
    return Decimal(str(close)) if close is not None else None


def _fusion_cached_daily_move(symbol: str) -> float | None:
    sym = symbol.strip().upper()
    newest: tuple[float, list] | None = None
    hit = _wl_bars_cache.get((sym, "1Day"))
    if hit is not None and hit[1]:
        newest = hit
    for key, (expires, response) in _BARS_CACHE.items():
        if key and key[0] == sym and key[1] == "1Day":
            bars = response.get("bars") if isinstance(response, dict) else None
            if bars and (newest is None or expires > newest[0]):
                newest = (expires, bars)
    if newest is None or len(newest[1]) < 2:
        return None
    bars = newest[1]
    previous = bars[-2]
    current = bars[-1]
    prev_close = previous.get("close") if isinstance(previous, dict) else previous.close
    last_close = current.get("close") if isinstance(current, dict) else current.close
    if not prev_close:
        return None
    return (float(last_close) / float(prev_close) - 1.0) * 100.0


def _fusion_shock_active() -> bool:
    global _fusion_shock_until_mono
    from config.idea_engine import (
        FUSION_SHOCK_WINDOW_MINUTES,
        FUSION_SPY_SHOCK_MOVE_PCT,
        FUSION_VIX_SHOCK_MOVE_PCT,
    )

    now = time.monotonic()
    spy_move = _fusion_cached_daily_move("SPY")
    vix_move = _fusion_cached_daily_move("VIX")
    if (
        spy_move is not None
        and abs(spy_move) >= FUSION_SPY_SHOCK_MOVE_PCT
        or vix_move is not None
        and vix_move >= FUSION_VIX_SHOCK_MOVE_PCT
    ):
        _fusion_shock_until_mono = max(
            _fusion_shock_until_mono, now + FUSION_SHOCK_WINDOW_MINUTES * 60
        )
    return now <= _fusion_shock_until_mono


async def _fusion_intake_once() -> None:
    from signals.fusion.intake import intake_once

    await asyncio.to_thread(
        intake_once,
        price_lookup=_fusion_price_from_cache,
        shock_lookup=_fusion_shock_active,
    )


async def _fusion_atr_refresh_once() -> None:
    """Once-daily external work, deliberately separate from trigger intake."""
    from datetime import datetime
    from decimal import Decimal

    from db.base import DEFAULT_DB_PATH, connect
    from db.equity_tape import init_db as init_equity_tape
    from db.fusion import put_atr
    from db.options_flow import init_db as init_options_flow

    init_options_flow(DEFAULT_DB_PATH)
    init_equity_tape(DEFAULT_DB_PATH)
    as_of = datetime.now(_ET).date().isoformat()
    with connect(DEFAULT_DB_PATH) as conn:
        rows = conn.execute(
            """
            SELECT underlying AS symbol FROM options_flow_events
            UNION
            SELECT symbol FROM equity_tape_events
            """
        ).fetchall()
        cached_today = {
            str(row["symbol"])
            for row in conn.execute(
                "SELECT symbol FROM fusion_atr_cache WHERE as_of_date = ?", (as_of,)
            ).fetchall()
        }
    symbols = sorted(
        ({str(row["symbol"]).upper() for row in rows} | set(_all_watchlist_symbols()))
        - cached_today
    )
    if not symbols:
        return
    provider = get_market_data_provider()
    bars_by_symbol = await provider.aget_stock_bars_batch(symbols, timeframe="1Day", days=40)
    today_et = datetime.now(_ET).date()
    for symbol, bars in bars_by_symbol.items():
        completed = []
        for bar in bars:
            raw_time = getattr(bar, "time", None)
            try:
                bar_time = datetime.fromisoformat(str(raw_time).replace("Z", "+00:00"))
                bar_day = (
                    bar_time.astimezone(_ET).date()
                    if bar_time.tzinfo is not None
                    else bar_time.date()
                )
            except (TypeError, ValueError):
                bar_day = None
            if bar_day == today_et:
                continue
            completed.append(bar)
        highs = [float(bar.high) for bar in completed]
        lows = [float(bar.low) for bar in completed]
        closes = [float(bar.close) for bar in completed]
        atr14 = indicators.atr(highs, lows, closes, 14)
        if atr14 is not None:
            put_atr(symbol, as_of, Decimal(str(round(atr14, 6))))


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
        ("regime_refresh", float(REGIME_REFRESH_INTERVAL_SECONDS), None),
        (
            "momentum_bars_refresh",
            float(MOMENTUM_BARS_REFRESH_INTERVAL_SECONDS),
            None,
        ),
        (
            "volume_profile_refresh",
            float(VOLUME_PROFILE_REFRESH_INTERVAL_SECONDS),
            None,
        ),
        (
            "reports_scheduler",
            float(MORNING_REPORT_INTERVAL_SECONDS),
            lambda: _MORNING_REPORT_ENV_ENABLED,
        ),
        ("bars_prewarm", _BARS_PREWARM_INTERVAL, None),
        ("equity_tape_flush", _EQUITY_TAPE_FLUSH_SECONDS, None),
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
            name="options_flow",
            fn=_options_flow_eod_once,
            interval_s=float(OPTIONS_FLOW_INTERVAL_SECONDS),
            enabled=lambda: _OPTIONS_FLOW_ENABLED,
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
            name="setup_scan_nightly",
            fn=_setup_scan_nightly_once,
            interval_s=900.0,
            enabled=lambda: _SETUP_SCAN_NIGHTLY_ENABLED,
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
            name="reflection",
            fn=reflection_once,
            interval_s=float(REFLECTION_INTERVAL_SECONDS),
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
            name="pipeline_audit",
            fn=pipeline_audit_once,
            interval_s=900.0,
        )
    )
    register_watchdog_job()


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
        if count_pending_paper_executions("alpaca", account_id=acct_id) > 0:
            await reconcile_pending_executions("alpaca", account_id=acct_id)
    for name in get_enabled_broker_names():
        if (
            name == "schwab"
            and broker_is_configured(name)
            and count_pending_paper_executions(name) > 0
        ):
            await reconcile_pending_executions(name)
    await _poll_once()
    _register_background_jobs()
    background_tasks = start_all() + [
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
        asyncio.create_task(_regime_refresh_loop()),
        asyncio.create_task(_regime_sleeves_loop()),
        asyncio.create_task(_momentum_bars_refresh_loop()),
        asyncio.create_task(_volume_profile_refresh_loop()),
        asyncio.create_task(_reports_scheduler_loop()),
        asyncio.create_task(_bars_prewarm_loop()),
        asyncio.create_task(_equity_tape_flush_loop()),
    ]
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
app.include_router(notification_router)
app.include_router(scan_router)
app.include_router(schwab_router)
app.include_router(journal_router)
app.include_router(fill_orphan_router)
app.include_router(ticker_router)
app.include_router(social_router)
app.include_router(orders_router)

app.add_middleware(SecurityMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=frontend_origins(),
    allow_credentials=True,
    allow_methods=["GET", "PUT", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["*"],
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


@app.get("/api/watchlist")
def api_get_watchlist():
    return {"symbols": get_watchlist()}


@app.get("/api/watchlists")
def api_get_watchlists():
    return get_watchlists_state()


@app.put("/api/watchlists/active")
def api_set_active_watchlist(body: dict = Body(...)):
    list_id = str(body.get("id", "")).strip()
    if not list_id:
        return JSONResponse({"error": "id is required"}, status_code=400)
    try:
        state = set_active_watchlist(list_id)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    return state


@app.post("/api/watchlists")
def api_create_watchlist(body: dict = Body(...)):
    name = str(body.get("name", "")).strip()
    if not name:
        return JSONResponse({"error": "name is required"}, status_code=400)
    symbols = body.get("symbols")
    if symbols is not None and not isinstance(symbols, list):
        return JSONResponse({"error": "symbols must be a list"}, status_code=400)
    settings = body.get("settings")
    if settings is not None and not isinstance(settings, dict):
        return JSONResponse({"error": "settings must be an object"}, status_code=400)
    try:
        state = create_watchlist(
            name,
            symbols if isinstance(symbols, list) else None,
            settings if isinstance(settings, dict) else None,
        )
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    return state


@app.put("/api/watchlists/{list_id}")
def api_update_watchlist(list_id: str, body: dict = Body(...)):
    name = body.get("name")
    symbols = body.get("symbols")
    settings = body.get("settings")
    if name is not None and not isinstance(name, str):
        return JSONResponse({"error": "name must be a string"}, status_code=400)
    if symbols is not None and not isinstance(symbols, list):
        return JSONResponse({"error": "symbols must be a list"}, status_code=400)
    if settings is not None and not isinstance(settings, dict):
        return JSONResponse({"error": "settings must be an object"}, status_code=400)
    try:
        state = update_watchlist(
            list_id,
            name=name if isinstance(name, str) else None,
            symbols=symbols if isinstance(symbols, list) else None,
            settings=settings if isinstance(settings, dict) else None,
        )
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    return state


@app.delete("/api/watchlists/{list_id}")
def api_delete_watchlist(list_id: str):
    try:
        state = delete_watchlist(list_id)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    return state


# Per-(symbol, timeframe) TTL cache for the watchlist poll ONLY. The frontend
# polls /api/watchlist/market every few seconds and each uncached poll fanned
# out one Schwab pricehistory call per symbol per timeframe (~480 req/min on a
# 24-symbol watchlist) — the actual source of the Akamai WAF 429/403 episodes.
# Daily bars move once a day and RVOL only changes per 5-minute bucket, so
# short TTLs cost nothing functionally. Scoped here on purpose: RiskEngine /
# execution paths keep their own always-fresh fetches.
_wl_bars_cache: dict[tuple[str, str], tuple[float, list]] = {}
_WL_BARS_TTL_S = {"1Day": 300.0, "5Min": 90.0}


async def _wl_cached_bars_batch(
    provider, symbols: list[str], *, timeframe: str = "1Day", days: int = 12
) -> dict[str, list[StockBar]]:
    now = time.time()
    ttl = _WL_BARS_TTL_S.get(timeframe, 90.0)
    out: dict[str, list[StockBar]] = {}
    missing: list[str] = []
    for sym in symbols:
        hit = _wl_bars_cache.get((sym, timeframe))
        if hit is not None and now - hit[0] < ttl:
            out[sym] = hit[1]
        else:
            missing.append(sym)
    if missing:
        fetched = await provider.aget_stock_bars_batch(missing, timeframe=timeframe, days=days)
        for sym in missing:
            bars = fetched.get(sym) or []
            # Cache empty results too — an unfetchable symbol otherwise
            # re-fetches on every poll (the retry-storm shape).
            _wl_bars_cache[(sym, timeframe)] = (now, bars)
            out[sym] = bars
    return out


# 52-week / ATR / SMA baselines move once a day, so they're computed from a
# 300-day daily-bar fetch on a LONG TTL (~4 fetches/day/symbol) — the 20-s poll
# itself never adds broker requests for them. Kept separate from
# _wl_bars_cache because that cache is keyed (symbol, timeframe) and a 300-day
# entry would collide with the 12-day one.
_wl_baseline_cache: dict[str, tuple[float, dict | None]] = {}
_WL_BASELINE_TTL_S = 6 * 3600.0
_WL_BASELINE_MISS_TTL_S = 600.0  # retry sooner when the fetch failed/was empty


def _compute_wl_baseline(bars: list) -> dict | None:
    if len(bars) < 20:
        return None
    closes = [float(b.close) for b in bars]
    # Vendor glitch guard — a bad print can ship an absurd bar low/high (seen
    # live: SPY daily low=69.005 on a 695-close day, a decimal-shift bad tick,
    # which poisoned the 52w low and range bar). A valid bar has
    # low <= close <= high, so a low/high wildly inconsistent with its own
    # close is bogus — substitute the close so 52w and ATR stay sane.
    highs = [
        h if (c and h <= c * 2) else c for h, c in ((float(b.high), float(b.close)) for b in bars)
    ]
    lows = [
        lo if (c and lo >= c * 0.5) else c
        for lo, c in ((float(b.low), float(b.close)) for b in bars)
    ]

    def _r(v: float | None) -> float | None:
        return round(v, 4) if v is not None else None

    return {
        "w52_high": _r(max(highs[-252:])),
        "w52_low": _r(min(lows[-252:])),
        "atr14": _r(indicators.atr(highs, lows, closes, 14)),
        "sma50": _r(indicators.sma(closes, 50)),
        "sma200": _r(indicators.sma(closes, 200)),
    }


async def _wl_baselines_batch(provider, symbols: list[str]) -> dict[str, dict | None]:
    now = time.time()
    out: dict[str, dict | None] = {}
    missing: list[str] = []
    for sym in symbols:
        hit = _wl_baseline_cache.get(sym)
        if hit is not None:
            ttl = _WL_BASELINE_TTL_S if hit[1] is not None else _WL_BASELINE_MISS_TTL_S
            if now - hit[0] < ttl:
                out[sym] = hit[1]
                continue
        missing.append(sym)
    if missing:
        try:
            fetched = await provider.aget_stock_bars_batch(missing, timeframe="1Day", days=300)
        except BrokerError:
            logger.warning("Watchlist baseline bars fetch failed", exc_info=True)
            fetched = {}
        for sym in missing:
            baseline = _compute_wl_baseline(fetched.get(sym) or [])
            _wl_baseline_cache[sym] = (now, baseline)
            out[sym] = baseline
    return out


@app.get("/api/watchlist/market")
async def api_watchlist_market(symbols: str = ""):
    try:
        provider = get_market_data_provider()
    except BrokerError as exc:
        return JSONResponse({"error": str(exc)}, status_code=502)

    symbol_list = [s.strip().upper() for s in symbols.split(",") if s.strip()]
    if not symbol_list:
        symbol_list = get_watchlist()
    if not symbol_list:
        return {"quotes": {}}

    # Any requested symbol with no fresh volume profile gets one built in the
    # background (deduped/cooldowned) so RVOL works for ANY stock, not just the
    # nightly-precomputed watchlist. It populates within a poll cycle.
    for sym in symbol_list:
        if load_profile(sym) is None:
            session_type = "globex" if sym.startswith("/") else "rth"
            _schedule_profile_build(provider, sym, session=session_type)

    try:
        bars_by_symbol = await _wl_cached_bars_batch(provider, symbol_list, days=12)
    except BrokerError as exc:
        return JSONResponse({"error": str(exc)}, status_code=502)

    # Slow tier: 52w/ATR/SMA baselines (equities only; long-TTL cache).
    equity_symbols = [s for s in symbol_list if not s.startswith("/")]
    baselines = await _wl_baselines_batch(provider, equity_symbols)

    # SPY day change for the "vs SPY" relative-performance column — one
    # cache-shared bars call (free when SPY is already on the list).
    spy_change_pct: float | None = None
    try:
        spy_bars = (await _wl_cached_bars_batch(provider, ["SPY"], days=12)).get("SPY") or []
        if len(spy_bars) >= 2 and float(spy_bars[-2].close):
            spy_change_pct = round(
                (float(spy_bars[-1].close) - float(spy_bars[-2].close))
                / float(spy_bars[-2].close)
                * 100,
                2,
            )
    except BrokerError:
        logger.warning("Watchlist SPY change fetch failed", exc_info=True)

    if symbol_list and not any(bars_by_symbol.get(s) for s in symbol_list):
        logger.error(
            "Watchlist market: bar fetch returned no data for any of %d symbols — likely provider auth/data outage",
            len(symbol_list),
        )
        return JSONResponse(
            {"error": "market data unavailable: no bars for any requested symbol"},
            status_code=502,
        )

    intraday_by_symbol: dict[str, list[StockBar]] = {}
    try:
        intraday_by_symbol = await _wl_cached_bars_batch(
            provider, symbol_list, timeframe="5Min", days=4
        )
    except BrokerError as exc:
        logger.warning("Watchlist intraday bars failed: %s", exc)

    # Extended-hours quotes — Schwab only; fail-soft to empty dict on any error.
    ext_quotes: dict[str, dict] = {}
    if hasattr(provider, "aget_quotes"):
        try:
            from brokers.hybrid_market_data import HybridMarketData
            from brokers.schwab_market_data import SchwabMarketData

            if isinstance(provider, (SchwabMarketData, HybridMarketData)):
                ext_quotes = await provider.aget_quotes(symbol_list)
        except Exception:
            logger.warning("Extended-hours quote fetch failed; continuing without", exc_info=True)

    session = _market_session()

    quotes: dict[str, dict] = {}
    for sym in symbol_list:
        bars = bars_by_symbol.get(sym, [])
        if not bars:
            continue
        closes = [b.close for b in bars]
        latest = bars[-1]
        price = closes[-1]
        prev = closes[-2] if len(closes) >= 2 else price
        change_pct = ((price - prev) / prev * 100) if prev else 0.0
        intraday = _session_intraday_closes(
            intraday_by_symbol.get(sym, []), futures=sym.startswith("/")
        )

        # Futures symbols (e.g. /ES, /NQ) have no fundamentals, extended session,
        # volume profile, or earnings — skip all equity-only enrichment.
        is_future = sym.startswith("/")

        if is_future:
            day_high = float(latest.high)
            day_low = float(latest.low)
            day_volume = float(latest.volume)
            spark_closes = closes[-7:]
            roll = _futures_price_roll(bars, intraday_by_symbol.get(sym, []))
            if roll is not None:
                price = roll["price"]
                prev = roll["prev_close"]
                change_pct = roll["change_pct"]
                day_high = roll["day_high"]
                day_low = roll["day_low"]
                day_volume = roll["volume"]
                spark_closes = (closes + [price])[-7:]
            quotes[sym] = {
                "price": price,
                "change_pct": round(change_pct, 2),
                "sparkline": [round(c, 4) for c in spark_closes],
                "prev_close": prev,
                "volume": day_volume,
                "day_high": day_high,
                "day_low": day_low,
                "intraday": intraday,
                "market_session": session,
                "ext_price": None,
                "ext_change_pct": None,
                "pe": None,
                "eps": None,
                "div_yield": None,
                "market_cap": None,
                "last_earnings": None,
                "next_earnings": None,
                "rvol": _compute_rvol_field(sym, intraday_by_symbol.get(sym, []), session="globex"),
            }
            continue

        eq = ext_quotes.get(sym, {})

        shares_outstanding = eq.get("shares_outstanding")
        market_cap: float | None = None
        if shares_outstanding is not None:
            try:
                market_cap = round(float(shares_outstanding) * price, 2)
            except (TypeError, ValueError):
                pass

        today_iso = date.today().isoformat()
        try:
            _next_earnings = next_earnings_date(sym, on_date=today_iso)
        except Exception:
            _next_earnings = None

        # "closed" (weekend/holiday) included: Schwab's quote retains the last
        # extended print, and the chart candles (extended_hours=True) end on it —
        # omitting it left watchlist/label a session behind the visible candles.
        _ext_sessions = ("pre", "post", "overnight", "closed")
        ext_price_val = eq.get("ext_last") if session in _ext_sessions else None
        ext_change_val = eq.get("ext_change_pct") if session in _ext_sessions else None

        # Gap at the open vs prior close — from the daily bar already in hand.
        gap_pct: float | None = None
        try:
            if prev and len(bars) >= 2:
                gap_pct = round((float(latest.open) - prev) / prev * 100, 2)
        except (TypeError, ValueError):
            pass

        quotes[sym] = {
            "price": price,
            "change_pct": round(change_pct, 2),
            "sparkline": [round(c, 4) for c in closes[-7:]],
            "prev_close": prev,
            "volume": float(latest.volume),
            "day_high": float(latest.high),
            "day_low": float(latest.low),
            "gap_pct": gap_pct,
            "w52_high": (baselines.get(sym) or {}).get("w52_high"),
            "w52_low": (baselines.get(sym) or {}).get("w52_low"),
            "atr14": (baselines.get(sym) or {}).get("atr14"),
            "sma50": (baselines.get(sym) or {}).get("sma50"),
            "sma200": (baselines.get(sym) or {}).get("sma200"),
            "intraday": intraday,
            "market_session": session,
            "ext_price": ext_price_val,
            "ext_change_pct": ext_change_val,
            "overnight_delayed": bool(eq.get("overnight_delayed")),
            "pe": eq.get("pe_ratio"),
            "eps": eq.get("eps"),
            "div_yield": eq.get("div_yield"),
            "market_cap": market_cap,
            # Our own calendar only — SEC EDGAR now supplies past announce dates, so
            # this must not depend on Schwab (that would gate the field on the user
            # holding a Schwab account).
            "last_earnings": last_earnings_date(sym, today_iso),
            "next_earnings": _next_earnings,
        }
        quotes[sym]["rvol"] = _compute_rvol_field(sym, intraday_by_symbol.get(sym, []))

        # Push ext fields into the WS hub so a streamed quote frame carries the
        # same pre/post/overnight pricing the phone headlines (Schwab level-one
        # stream doesn't emit ext fields; without this they'd only refresh on the
        # 6-s REST poll).
        try:
            await market_hub.apply_extended(
                sym,
                ext_price=ext_price_val,
                ext_change_pct=ext_change_val,
                market_session=session,
                broadcast=True,
            )
        except Exception:
            logger.debug("apply_extended failed for %s", sym, exc_info=True)

    return {"quotes": quotes, "spy_change_pct": spy_change_pct}


_profile_build_attempts: dict[str, float] = {}
_PROFILE_BUILD_COOLDOWN_S = 600  # don't re-attempt a symbol's profile more than once / 10 min


def _schedule_profile_build(provider, symbol: str, *, session: str = "rth") -> None:
    """Fire-and-forget a volume-profile build for one symbol, deduped by cooldown.

    Guards against hammering Schwab when a symbol can't form a profile (illiquid →
    compute returns None → no file saved → would otherwise retry every poll).
    Pass ``session="globex"`` for futures symbols."""
    now = time.time()
    if now - _profile_build_attempts.get(symbol, 0.0) < _PROFILE_BUILD_COOLDOWN_S:
        return
    _profile_build_attempts[symbol] = now

    async def _run() -> None:
        try:
            await build_profile(provider, symbol, session=session)
        except Exception:
            logger.exception("On-demand volume-profile build failed for %s", symbol)

    asyncio.create_task(_run())


def _compute_rvol_field(
    symbol: str, intraday_bars: list[StockBar], *, session: str = "rth"
) -> dict | None:
    try:
        profile = load_profile(symbol)
        if profile is None or not intraday_bars:
            return None
        result = rvol_now(profile, intraday_bars, session=session)
        if result is None or result.value is None:
            if result is not None:
                return {"value": None, "state": result.state}
            return None
        state = result.state
        if state == "settled":
            latest_date = max(
                datetime.fromisoformat(b.time.replace("Z", "+00:00")).astimezone(_ET).date()
                for b in intraday_bars
            )
            if latest_date < datetime.now(_ET).date():
                state = "overnight"
        return {"value": result.value, "state": state}
    except Exception as exc:  # RVOL is best-effort — one symbol must never 500 the watchlist
        logger.warning("RVOL compute failed for %s: %s", symbol, exc)
        return None


_SCANNER_CHUNK_SIZE = 250  # Schwab get_quotes accepts many symbols; chunk to stay safe


def _rvol_bucket_now() -> int | None:
    """Return the current 5-min bucket index (0–77) based on ET wall clock.

    Returns None if before 09:30 ET (pre-market — totalVolume unreliable for RVOL).
    Clamps to 77 after 16:00 ET (post-close settled).
    """
    now = datetime.now(_ET)
    open_min = _SESSION_OPEN[0] * 60 + _SESSION_OPEN[1]
    now_min = now.hour * 60 + now.minute
    if now_min < open_min:
        return None
    return min((now_min - open_min) // 5, 77)


def _compute_rvol_from_total_volume(
    profile,
    total_volume: float,
    bucket: int,
) -> float | None:
    """Compute RVOL_now from a symbol's cumulative totalVolume and its precomputed profile.

    This is an efficient *screen* using Schwab's totalVolume from the quote block
    (cumulative RTH + extended volume as reported by the exchange), divided by the
    expected cumulative RTH volume at this point in the day.

    Note: totalVolume includes pre-market volume accumulated before 09:30, which may
    inflate RVOL slightly in early morning buckets. Rankings are what matter; the
    per-symbol watchlist RVOL (which uses RTH 5-min bars only) is more precise for
    individual tracking. This scanner is designed for finding anomalies, not for
    exact calibration.

    Returns None if profile is missing, bucket is out of range, or expected volume <= 0.
    """
    if profile is None or bucket < 0 or bucket > 77:
        return None
    expected = profile.median_daily_total * profile.cumfrac[bucket]
    if expected <= 0:
        return None
    return round(total_volume / expected, 2)


_OPTIONS_CHAIN_CACHE: dict[tuple[str, str, str], tuple[float, dict]] = {}
_OPTIONS_CHAIN_TTL_S = 30.0


@app.get("/api/options-chain")
async def api_options_chain(
    symbol: str = "",
    expiry: str | None = None,
    strikes: str = "10",
):
    """Read-only OPRA option chain for one underlying + expiration."""
    broker = _require_alpaca()
    if isinstance(broker, JSONResponse):
        return broker

    sym = symbol.strip().upper()
    if not sym:
        return JSONResponse({"error": "symbol required"}, status_code=400)

    strikes_key = strikes.strip().lower() if strikes else "10"
    if strikes_key != "all":
        try:
            n = int(strikes_key)
            if n <= 0:
                return JSONResponse(
                    {"error": "strikes must be a positive integer or 'all'"},
                    status_code=400,
                )
            strikes_key = str(n)
        except ValueError:
            return JSONResponse(
                {"error": "strikes must be a positive integer or 'all'"},
                status_code=400,
            )

    cache_expiry = (expiry or "").strip()[:10] if expiry else ""
    cache_key = (sym, cache_expiry, strikes_key)
    now = time.time()
    hit = _OPTIONS_CHAIN_CACHE.get(cache_key)
    if hit is not None and now - hit[0] < _OPTIONS_CHAIN_TTL_S:
        return hit[1]

    from brokers.options_chain import build_options_chain_sync

    try:
        result = await asyncio.to_thread(build_options_chain_sync, broker, sym, expiry, strikes_key)
    except BrokerError as exc:
        return JSONResponse({"error": str(exc)}, status_code=502)
    except Exception as exc:  # noqa: BLE001 — read-only endpoint, fail-soft
        logger.exception("options-chain: fetch failed for %s", sym)
        return JSONResponse({"error": str(exc)}, status_code=500)

    _OPTIONS_CHAIN_CACHE[cache_key] = (now, result)
    return result


@app.get("/api/options-flow")
async def api_options_flow(
    limit: int = 200,
    symbol: str | None = None,
    event_type: str | None = None,
    min_premium: float | None = None,
    since: str | None = None,
):
    """Recent unusual options-activity events (Mode-A perception).

    Facts about the OPRA tape — NOT a buy/sell signal. Each event carries a
    ``caveat`` and never a win%/EV/confidence. Populated by the nightly EOD scan
    (``OPTIONS_FLOW_ENABLED``); returns an empty list until that has run.
    ``event_type`` accepts a comma-separated list to filter the tape by kind.
    """
    from db.options_flow import recent_events

    capped = max(1, min(int(limit), 250))
    try:
        events = recent_events(
            symbol=symbol,
            event_type=event_type,
            min_premium=min_premium,
            since=since,
            limit=capped,
        )
    except Exception as exc:  # noqa: BLE001 — read-only endpoint, fail-soft
        return JSONResponse({"error": str(exc)}, status_code=500)
    return {
        "as_of": datetime.now(_ET).isoformat(timespec="seconds"),
        "count": len(events),
        "events": events,
    }


@app.get("/api/index-pressure")
async def api_index_pressure(
    date: str | None = None, series: str | None = None, window: str = "open"
):
    """Session-bookend index/megacap flow pressure — raw perception.

    ``window=open`` (09:30-10:00 ET, default) or ``close`` (15:30-16:00 ET).
    Premium-weighted net imbalance per underlying (bull = call buys + put sells;
    bear = put buys + call sells; the undetermined remainder is surfaced as
    ``unclassified_usd``, never guessed). NOT a signal: no score, no win%, and no
    "abnormal" judgment until the per-minute baseline has accrued enough days
    (``baseline_days`` is returned so the UI can label the warm-up honestly).
    ``series=SYM`` adds that underlying's per-minute rows for a sparkline.
    """
    from db.index_pressure import baseline_days, day_summary, minute_series

    win = window.strip().lower()
    if win not in ("open", "close"):
        return JSONResponse({"error": "window must be 'open' or 'close'"}, status_code=400)
    target = (date or datetime.now(_ET).date().isoformat()).strip()
    try:
        from signals.options_flow.constants import GAUGE_ZERO_DTE_PICKS

        payload: dict = {
            "as_of": datetime.now(_ET).isoformat(timespec="seconds"),
            "date": target,
            "window": win,
            "window_et": "09:30-10:00" if win == "open" else "15:30-16:00",
            "rows": day_summary(target, window=win),
            "baseline_days": baseline_days(window=win),
            # Disclosed truncation: index 0-1DTE coverage = the top-N contracts
            # by day-$vol per name (ATM cluster), not the whole 0DTE chain.
            "lane": {"zero_dte_top_n": GAUGE_ZERO_DTE_PICKS},
        }
        if series:
            payload["series"] = {
                "underlying": series.strip().upper(),
                "minutes": minute_series(target, series, window=win),
            }
    except Exception as exc:  # noqa: BLE001 — read-only endpoint, fail-soft
        return JSONResponse({"error": str(exc)}, status_code=500)
    return payload


@app.get("/api/scanner/rvol")
async def api_scanner_rvol(min_rvol: float = 1.5, limit: int = 50):
    """Market-wide unusual-volume scanner powered by precomputed volume profiles.

    Returns symbols from the ~986-name liquid-core universe whose cumulative
    today volume is unusually high for this time of day, ranked by RVOL descending.

    Query params:
      min_rvol  — minimum RVOL to include (default 1.5)
      limit     — max symbols returned (default 50, max 200)

    Only meaningful with the Schwab provider (requires quote.totalVolume).
    Returns 400 if the active provider is not Schwab.
    Returns an empty results list (not an error) outside RTH (pre/post/closed/overnight).
    """
    from brokers.hybrid_market_data import HybridMarketData
    from brokers.schwab_market_data import SchwabMarketData

    if limit < 1 or limit > 200:
        return JSONResponse({"error": "limit must be 1–200"}, status_code=400)
    if min_rvol < 0:
        return JSONResponse({"error": "min_rvol must be >= 0"}, status_code=400)

    try:
        provider = get_market_data_provider()
    except BrokerError as exc:
        return JSONResponse({"error": str(exc)}, status_code=502)

    if not isinstance(provider, (SchwabMarketData, HybridMarketData)):
        return JSONResponse(
            {
                "error": (
                    "RVOL scanner requires Schwab as the market data provider "
                    "(needs quote.totalVolume). Set MARKET_DATA_PROVIDER=schwab "
                    "or hybrid."
                )
            },
            status_code=400,
        )

    session = _market_session()
    as_of = datetime.now(_ET).isoformat(timespec="seconds")

    # RVOL is only meaningful during RTH — totalVolume before open is unreliable.
    bucket = _rvol_bucket_now()
    if bucket is None or session not in ("regular",):
        return {
            "as_of": as_of,
            "session": session,
            "count": 0,
            "results": [],
            "note": (
                "RVOL scanner only runs during regular RTH session (09:30–16:00 ET). "
                f"Current session: {session!r}."
            ),
        }

    # Gather all symbols that have a cached volume profile.
    try:
        profile_files = list(profile_dir().glob("*.json"))
    except OSError as exc:
        return JSONResponse(
            {"error": f"Cannot read volume profile directory: {exc}"},
            status_code=502,
        )

    if not profile_files:
        return {
            "as_of": as_of,
            "session": session,
            "count": 0,
            "results": [],
            "note": "No volume profiles found. Run the nightly profile precompute first.",
        }

    symbols = [f.stem for f in profile_files]

    # Batch fetch quotes in chunks; gather concurrently.
    chunks = [
        symbols[i : i + _SCANNER_CHUNK_SIZE] for i in range(0, len(symbols), _SCANNER_CHUNK_SIZE)
    ]
    try:
        chunk_results = await asyncio.gather(*(provider.aget_quotes(chunk) for chunk in chunks))
    except BrokerError as exc:
        return JSONResponse(
            {"error": f"Schwab quote fetch failed: {exc}"},
            status_code=502,
        )

    # Merge chunk dicts into one flat map.
    all_quotes: dict[str, dict] = {}
    for chunk_dict in chunk_results:
        all_quotes.update(chunk_dict)

    # Compute RVOL for each symbol; keep those meeting the threshold.
    results: list[dict] = []
    for sym in symbols:
        q = all_quotes.get(sym)
        if q is None:
            continue
        total_volume = q.get("total_volume")
        if total_volume is None or total_volume <= 0:
            continue
        # load_profile respects max_age_days=7 staleness guard.
        from signals.volume_profile import load_profile as _load_profile

        profile = _load_profile(sym)
        if profile is None:
            continue
        rvol_val = _compute_rvol_from_total_volume(profile, float(total_volume), bucket)
        if rvol_val is None or rvol_val < min_rvol:
            continue
        price = q.get("regular_last") or q.get("ext_last")
        results.append(
            {
                "symbol": sym,
                "rvol": rvol_val,
                "total_volume": int(total_volume),
                "price": round(price, 4) if price is not None else None,
            }
        )

    results.sort(key=lambda r: r["rvol"], reverse=True)
    results = results[:limit]

    return {
        "as_of": as_of,
        "session": session,
        "count": len(results),
        "results": results,
    }


def _session_intraday_closes(bars: list[StockBar], *, futures: bool = False) -> list[float]:
    if not bars:
        return []

    def _bar_et(bar: StockBar) -> datetime:
        return datetime.fromisoformat(bar.time.replace("Z", "+00:00")).astimezone(_ET)

    # Futures trade ~23h on Globex with no RTH window — group by Globex session
    # (18:00 ET prev day -> 17:00 ET) and return the latest session's closes. The
    # RTH path below would return [] off-RTH, which the sparkline can't fall back from.
    if futures:
        from research.intraday_profile import globex_session_date

        sessions = [
            (sd, bar) for bar in bars if (sd := globex_session_date(_bar_et(bar))) is not None
        ]
        if not sessions:
            return []
        latest = max(sd for sd, _ in sessions)
        return [round(bar.close, 4) for sd, bar in sessions if sd == latest]

    session_date = _bar_et(bars[-1]).date()
    open_minutes = _SESSION_OPEN[0] * 60 + _SESSION_OPEN[1]
    close_minutes = _SESSION_CLOSE[0] * 60 + _SESSION_CLOSE[1]

    kept: list[StockBar] = []
    for bar in bars:
        et = _bar_et(bar)
        if et.date() != session_date:
            continue
        bar_minutes = et.hour * 60 + et.minute
        if open_minutes <= bar_minutes < close_minutes:
            kept.append(bar)

    return [round(b.close, 4) for b in kept]


def _futures_price_roll(daily_bars: list[StockBar], intraday_bars: list[StockBar]) -> dict | None:
    """Roll a futures quote forward when the 5-min series has a whole Globex
    session the daily series lacks — Schwab's futures DAILY bars can lag a
    session (e.g. the 2026-07-03 holiday half-session never got a daily bar),
    which froze the row's price at the prior settle while its own intraday
    line showed the newer session. Returns replacement quote fields, or None
    when the dailies are current.
    """
    if not daily_bars or not intraday_bars:
        return None
    from research.intraday_profile import globex_session_date

    def _bar_et(bar: StockBar) -> datetime:
        return datetime.fromisoformat(bar.time.replace("Z", "+00:00")).astimezone(_ET)

    sessions = [
        (sd, bar) for bar in intraday_bars if (sd := globex_session_date(_bar_et(bar))) is not None
    ]
    if not sessions:
        return None
    latest_session = max(sd for sd, _ in sessions)
    last_daily_date = datetime.fromisoformat(daily_bars[-1].time.replace("Z", "+00:00")).date()
    if latest_session <= last_daily_date:
        return None

    session_bars = [bar for sd, bar in sessions if sd == latest_session]
    price = session_bars[-1].close
    prev = daily_bars[-1].close
    return {
        "price": price,
        "prev_close": prev,
        "change_pct": ((price - prev) / prev * 100) if prev else 0.0,
        "day_high": float(max(b.high for b in session_bars)),
        "day_low": float(min(b.low for b in session_bars)),
        "volume": float(sum(b.volume for b in session_bars)),
    }


@app.put("/api/watchlist")
def api_put_watchlist(body: dict = Body(...)):
    symbols = body.get("symbols")
    if not isinstance(symbols, list):
        return JSONResponse({"error": "symbols must be a list"}, status_code=400)
    return {"symbols": save_watchlist(symbols)}


@app.post("/api/watchlist")
def api_add_watchlist_symbol(body: dict = Body(...)):
    symbol = str(body.get("symbol", "")).strip().upper()
    if not symbol:
        return JSONResponse({"error": "symbol is required"}, status_code=400)
    try:
        symbols = add_watchlist_symbol(symbol)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    return {"symbols": symbols}


@app.delete("/api/watchlist/{symbol}")
def api_remove_watchlist_symbol(symbol: str):
    sym = symbol.strip().upper()
    try:
        updated = remove_watchlist_symbol(sym)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    return {"symbols": updated}


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


def _require_alpaca() -> AlpacaAdapter | JSONResponse:
    if not broker_is_configured("alpaca"):
        return JSONResponse(
            {"error": "Alpaca is not configured. Add API keys in Settings."},
            status_code=501,
        )
    try:
        broker = get_broker("alpaca")
    except BrokerError as exc:
        return JSONResponse({"error": str(exc)}, status_code=502)
    if not isinstance(broker, AlpacaAdapter):
        return JSONResponse({"error": "Alpaca adapter unavailable"}, status_code=501)
    return broker


@app.get("/api/portfolio/history")
async def api_portfolio_history(range: str = "1W"):
    broker = _require_alpaca()
    if isinstance(broker, JSONResponse):
        return broker

    valid = {"1D", "3D", "1W", "1M", "1Y", "ALL"}
    if range not in valid:
        return JSONResponse(
            {"error": f"Invalid range. Use one of: {', '.join(sorted(valid))}"},
            status_code=400,
        )

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


@app.post("/api/ideas/outcomes/{outcome_id}/grade")
def api_grade_idea_outcome(outcome_id: int, request: Request, body: dict = Body(...)):
    """Founder-only 5b judging: grade a RESOLVED idea against the rubric.
    Re-grading overwrites (the founder can revise)."""
    verify_mutation_token(request)
    from db.idea_outcomes import grade_outcome, normalize_verdict

    try:
        verdict = normalize_verdict(body)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)

    result = grade_outcome(outcome_id, verdict)
    if result == "not_found":
        return JSONResponse({"error": "Idea outcome not found"}, status_code=404)
    if result == "not_gradeable":
        return JSONResponse(
            {"error": "Only resolved ideas (won/lost/expired) can be graded"},
            status_code=400,
        )
    return {"ok": True, "verdict": verdict}


@app.get("/api/reports")
def api_list_reports(limit: int = 30):
    from db.reports import list_reports

    cap = max(1, min(int(limit), 100))
    return {"reports": list_reports(limit=cap)}


@app.get("/api/reports/{report_id}")
def api_get_report(report_id: str):
    from db.reports import get_report

    report = get_report(report_id.strip())
    if report is None:
        return JSONResponse({"error": "Report not found"}, status_code=404)
    return report


_GENERATABLE_REPORT_KINDS = (
    "morning_levels",
    "levels",
    "morning_brief",
    "evening_brief",
    "on_request",
    "flow_decomp",
)


@app.post("/api/reports/generate")
async def api_generate_report(body: dict = Body(...), push: bool = False):
    kind = str(body.get("kind", "")).strip() or "morning_levels"
    if kind not in _GENERATABLE_REPORT_KINDS:
        return JSONResponse(
            {
                "error": f"Unsupported kind '{kind}'",
                "supported": list(_GENERATABLE_REPORT_KINDS),
            },
            status_code=400,
        )

    # symbols: accept `symbols` (list) or legacy single `symbol`.
    raw_symbols = body.get("symbols")
    if isinstance(raw_symbols, list):
        symbols = [str(s).strip().upper() for s in raw_symbols if str(s).strip()]
    else:
        one = str(body.get("symbol", "")).strip().upper()
        symbols = [one] if one else []
    if kind in ("morning_levels", "levels") and not symbols:
        symbols = [MORNING_REPORT_SYMBOL]
    topic = str(body.get("topic", "")).strip()
    if kind == "on_request" and not topic:
        return JSONResponse({"error": "kind 'on_request' requires a 'topic'"}, status_code=400)

    try:
        report = await _generate_report_by_kind(kind, symbols=symbols or None, topic=topic or None)
    except Exception as exc:
        logger.exception("Manual report generate failed")
        return JSONResponse({"error": str(exc)}, status_code=500)
    if report is None:
        return JSONResponse({"error": "No data available for this report"}, status_code=404)

    try:
        await _notify_report_ready(report, push=push)
    except Exception:
        logger.exception("Manual report notify failed for %s", report["id"])
    return report


# Short-TTL cache for chart bars so flipping back to a symbol/timeframe is instant
# instead of waiting on a fresh Schwab per-symbol round-trip (the live price rides the
# WS stream on the frontend, so a few-minutes-stale historical window is invisible).
_BARS_CACHE: dict[tuple, tuple[float, dict]] = {}
_BARS_CACHE_TTL = {
    "1Min": 60.0,
    "5Min": 180.0,
    "15Min": 300.0,
    "1Hour": 600.0,
    "4Hour": 600.0,
    "1Day": 300.0,
    "1Week": 300.0,
    "1Month": 300.0,
}

# Timeframes with no intraday session (daily/weekly/monthly) — pre/post-market data
# is meaningless for them, so extended-hours defaults OFF. Everything else is intraday
# and defaults ON so the chart shows pre/post candles (the founder's headline ask).
_NON_INTRADAY_TIMEFRAMES = frozenset({"1Day", "1Week", "1Month"})


def _bars_extended_default(timeframe: str) -> bool:
    """Extended-hours default for a timeframe: ON for intraday, OFF for daily+."""
    return timeframe not in _NON_INTRADAY_TIMEFRAMES


def _overnight_delayed_flag() -> bool:
    """Free-tier overnight data is ~15m delayed / indicative — surface for UI badge."""
    if _market_session() != "overnight":
        return False
    try:
        broker = get_broker("alpaca")
    except BrokerError:
        return False
    return data_tier(broker) == "free"


# Recency of chart-bar requests, used to keep the cache warm (prewarm loop below).
# spec = (timeframe, days) the frontend actually requests — capturing it avoids
# hardcoding the frontend's per-timeframe `days` here (they must match the cache key).
_BARS_SEEN_SYMBOLS: dict[str, float] = {}  # symbol -> last-requested monotonic
_BARS_SEEN_SPECS: dict[tuple[str, int], float] = {}  # (timeframe, days) -> last-requested monotonic
_BARS_SEEN_TTL = 30 * 60.0  # forget a symbol/spec 30 min after last request
_BARS_PREWARM_REFRESH_LEAD = 25.0  # refetch when <25s of cache TTL remains (never go cold mid-use)
_BARS_PREWARM_MAX_SYMBOLS = 40  # cap Schwab load for big watchlists
_BARS_PREWARM_PACE = 0.25  # sleep between actual fetches (s)
_BARS_PREWARM_DEFAULT_SPECS: set[tuple[str, int]] = {
    ("1Day", 1825)
}  # the default chart view, warm from boot


@app.get("/api/bars")
async def api_bars(
    symbol: str,
    timeframe: str = "1Day",
    days: int = 180,
    extended: bool | None = None,
):
    if days < 1 or days > 3650:
        return JSONResponse({"error": "days must be 1–3650"}, status_code=400)

    sym = symbol.strip().upper()
    # Explicit override wins; otherwise intraday → ext ON, daily+ → ext OFF. The chart
    # sends the flag per its ETH toggle so the pre/post candle set is unambiguous.
    ext = extended if extended is not None else _bars_extended_default(timeframe)
    now = time.monotonic()
    # Record what's actually viewed so the prewarm loop keeps it warm. Real requests
    # only — the loop calls _get_bars_cached directly so it can't self-refresh recency.
    _BARS_SEEN_SYMBOLS[sym] = now
    _BARS_SEEN_SPECS[(timeframe, int(days))] = now

    response = await _get_bars_cached(sym, timeframe, int(days), ext)
    if response is None:
        return JSONResponse({"error": "failed to load bars"}, status_code=502)
    return response


async def _get_bars_cached(
    sym: str, timeframe: str, days: int, extended: bool | None = None
) -> dict | None:
    """Cache-read → Schwab fetch (→ Alpaca fallback) → cache-write for chart bars.

    Returns the response dict, or None on total failure. Shared by the /api/bars
    endpoint and the prewarm loop — the loop must NOT record request-recency (else a
    prewarmed symbol would keep refreshing its own recency and never age out), so the
    fetch core lives here, separate from the endpoint's recency bookkeeping.
    """
    ext = extended if extended is not None else _bars_extended_default(timeframe)
    # ext is part of the cache key: an ext-inclusive and an RTH-only series for the
    # same (symbol, timeframe, days) are different payloads and must not alias.
    cache_key = (sym, timeframe, int(days), ext)
    now = time.monotonic()
    cached = _BARS_CACHE.get(cache_key)
    if cached is not None and cached[0] > now:
        return cached[1]

    try:
        provider = get_market_data_provider()
    except BrokerError:
        return None

    try:
        bars = await provider.aget_stock_bars(
            sym, timeframe=timeframe, days=days, extended_hours=ext
        )
    except Exception as exc:
        # Any provider failure (BrokerError, Schwab outage, httpx error) falls back
        # to Alpaca so charts never hard-break — resilience is the point of the path.
        logger.warning("/api/bars provider failed (%s), falling back to Alpaca", exc)
        broker = _require_alpaca()
        if isinstance(broker, JSONResponse):
            return None
        try:
            bars = await broker.aget_stock_bars(sym, timeframe=timeframe, days=days)
        except BrokerError:
            return None

    from brokers.bars_hygiene import sanitize_bar_dicts

    response = {
        "symbol": sym,
        "timeframe": timeframe,
        "bars": sanitize_bar_dicts(
            sym,
            [
                {
                    "time": b.time,
                    "open": b.open,
                    "high": b.high,
                    "low": b.low,
                    "close": b.close,
                    "volume": b.volume,
                }
                for b in bars
            ],
        ),
    }
    if ext and timeframe not in _NON_INTRADAY_TIMEFRAMES and _overnight_delayed_flag():
        response["overnight_delayed"] = True
    _BARS_CACHE[cache_key] = (now + _BARS_CACHE_TTL.get(timeframe, 120.0), response)
    if len(_BARS_CACHE) > 256:
        for k in [k for k, (exp, _) in list(_BARS_CACHE.items()) if exp <= now]:
            _BARS_CACHE.pop(k, None)
    return response


async def _bars_prewarm_once() -> None:
    now = time.monotonic()
    # Specs (timeframe, days) seen recently, plus the default view — only warm what's in use.
    specs = {s for s, ts in _BARS_SEEN_SPECS.items() if now - ts < _BARS_SEEN_TTL}
    specs |= _BARS_PREWARM_DEFAULT_SPECS
    # Symbols likely to be switched to: the whole watchlist (always) + the most-recently
    # viewed non-watchlist symbols, up to the cap (watchlist is never evicted).
    watch = list(_all_watchlist_symbols())
    recent = sorted(
        (
            s
            for s, ts in _BARS_SEEN_SYMBOLS.items()
            if now - ts < _BARS_SEEN_TTL and s not in set(watch)
        ),
        key=lambda s: _BARS_SEEN_SYMBOLS[s],
        reverse=True,
    )
    extra = max(0, _BARS_PREWARM_MAX_SYMBOLS - len(watch))
    symbols = watch + recent[:extra]
    if not symbols or not specs:
        return
    for sym in symbols:
        for timeframe, days in specs:
            key = (sym, timeframe, int(days))
            entry = _BARS_CACHE.get(key)
            now = time.monotonic()
            if entry is not None and entry[0] > now + _BARS_PREWARM_REFRESH_LEAD:
                continue  # still warm enough — skip
            try:
                # Shared fetch core — refetches (cache miss/expiring) + writes _BARS_CACHE
                # with the exact same key/shape, WITHOUT recording request-recency.
                await _get_bars_cached(sym, timeframe, int(days))
            except Exception:
                logger.debug("bars prewarm failed for %s %s", sym, timeframe, exc_info=True)
            await asyncio.sleep(_BARS_PREWARM_PACE)


async def _bars_prewarm_loop() -> None:
    while True:
        try:
            await _bars_prewarm_once()
        except Exception:
            logger.exception("Bars prewarm loop iteration failed")
        else:
            mark_alive("bars_prewarm")
        await asyncio.sleep(_BARS_PREWARM_INTERVAL)


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


@app.get("/api/workspace/layout")
def api_get_workspace_layout():
    return {"layout": load_workspace_layout()}


def _is_valid_layout_item(item: object) -> bool:
    if not isinstance(item, dict):
        return False
    return (
        isinstance(item.get("i"), str)
        and isinstance(item.get("type"), str)
        and isinstance(item.get("x"), (int, float))
        and isinstance(item.get("y"), (int, float))
        and isinstance(item.get("w"), (int, float))
        and isinstance(item.get("h"), (int, float))
    )


@app.put("/api/workspace/layout")
def api_put_workspace_layout(body: dict = Body(...)):
    layout = body.get("layout")
    if not isinstance(layout, list):
        return JSONResponse({"error": "layout must be an array"}, status_code=400)
    for item in layout:
        if not _is_valid_layout_item(item):
            return JSONResponse(
                {"error": ("each layout item must have string i, type and numeric x, y, w, h")},
                status_code=400,
            )
    save_workspace_layout(layout)
    return {"ok": True}


def _is_valid_workspace_page(page: object) -> bool:
    if not isinstance(page, dict):
        return False
    layout = page.get("layout")
    if not isinstance(page.get("id"), str) or not isinstance(page.get("name"), str):
        return False
    if not isinstance(layout, list):
        return False
    return all(_is_valid_layout_item(item) for item in layout)


def _is_valid_pages_state(state: object) -> bool:
    if not isinstance(state, dict):
        return False
    pages = state.get("pages")
    active_page_id = state.get("activePageId")
    if not isinstance(pages, dict) or not isinstance(active_page_id, str):
        return False
    if not pages:
        return False
    if active_page_id not in pages:
        return False
    return all(_is_valid_workspace_page(page) for page in pages.values())


def _is_valid_workspaces_state(state: object) -> bool:
    from db.workspace import _is_valid_workspaces_state as _valid

    return _valid(state)


@app.get("/api/workspace/workspaces")
def api_get_workspaces():
    from db.workspace import load_workspaces

    return {"workspaces_state": load_workspaces()}


@app.put("/api/workspace/workspaces")
def api_put_workspaces(body: dict = Body(...)):
    from db.workspace import save_workspaces

    workspaces_state = body.get("workspaces_state")
    if not _is_valid_workspaces_state(workspaces_state):
        return JSONResponse(
            {
                "error": (
                    "workspaces_state must be v2: version=2, a pages library "
                    "(1-24 valid pages), a workspaces map (≤8, each with string "
                    "id, name, 1-5 unique pageIds that exist in the library and "
                    "an activePageId among them) and a defaultWorkspaceId that "
                    "exists in the map"
                )
            },
            status_code=400,
        )
    save_workspaces(workspaces_state)
    return {"ok": True}


@app.get("/api/workspace/pages")
def api_get_workspace_pages():
    return {"pages_state": load_workspace_pages()}


@app.put("/api/workspace/pages")
def api_put_workspace_pages(body: dict = Body(...)):
    pages_state = body.get("pages_state")
    if not _is_valid_pages_state(pages_state):
        return JSONResponse(
            {
                "error": (
                    "pages_state must have pages map, activePageId, and each page "
                    "must have string id, name and a layout array of valid items"
                )
            },
            status_code=400,
        )
    save_workspace_pages(pages_state)
    return {"ok": True}


def load_board() -> dict:
    """Read project kanban board from References/board.json (fresh each call)."""
    logger = logging.getLogger(__name__)
    try:
        if not BOARD_JSON_PATH.is_file():
            logger.warning("board.json missing at %s", BOARD_JSON_PATH)
            return dict(_EMPTY_BOARD)
        parsed = json.loads(BOARD_JSON_PATH.read_text(encoding="utf-8"))
        if not isinstance(parsed, dict):
            logger.warning("board.json invalid: root is not an object")
            return dict(_EMPTY_BOARD)
        return parsed
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("failed to load board.json: %s", exc)
        return dict(_EMPTY_BOARD)


def _load_board_for_mutation() -> dict:
    """Load board.json for writes; 409 if missing or corrupt."""
    logger = logging.getLogger(__name__)
    if not BOARD_JSON_PATH.is_file():
        raise HTTPException(
            status_code=409,
            detail="board.json is missing; an agent must restore it before adding inbox cards",
        )
    try:
        parsed = json.loads(BOARD_JSON_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("failed to load board.json for mutation: %s", exc)
        raise HTTPException(
            status_code=409,
            detail=f"board.json is corrupt or unreadable: {exc}",
        ) from exc
    if not isinstance(parsed, dict):
        raise HTTPException(
            status_code=409,
            detail="board.json is corrupt: root is not an object",
        )
    return parsed


def _save_board_atomic(board: dict) -> None:
    """Atomically replace board.json (safe under concurrent agent edits)."""
    content = json.dumps(board, indent=2) + "\n"
    fd, tmp_path = tempfile.mkstemp(
        dir=BOARD_JSON_PATH.parent,
        prefix=".board-",
        suffix=".tmp",
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
        os.replace(tmp_path, BOARD_JSON_PATH)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


@app.get("/api/board")
def api_get_board():
    return {"board": load_board()}


@app.post("/api/board/inbox")
def api_post_board_inbox(body: dict = Body(...)):
    title_raw = body.get("title")
    if not isinstance(title_raw, str):
        raise HTTPException(status_code=400, detail="title is required")
    title = title_raw.strip()
    if not title:
        raise HTTPException(status_code=400, detail="title is required")
    if len(title) > 200:
        raise HTTPException(status_code=400, detail="title must be at most 200 characters")

    notes_raw = body.get("notes", "")
    if notes_raw is None:
        notes_raw = ""
    if not isinstance(notes_raw, str):
        raise HTTPException(status_code=400, detail="notes must be a string")
    notes = notes_raw.strip()
    if len(notes) > 500:
        raise HTTPException(status_code=400, detail="notes must be at most 500 characters")

    board = _load_board_for_mutation()

    lanes = board.get("lanes")
    if not isinstance(lanes, list):
        lanes = []
        board["lanes"] = lanes
    if "inbox" not in lanes:
        lanes.insert(0, "inbox")

    cards = board.get("cards")
    if not isinstance(cards, list):
        cards = []
        board["cards"] = cards

    card = {
        "id": f"fr-{int(time.time())}",
        "title": title,
        "lane": "inbox",
        "type": "idea",
        "priority": "normal",
        "notes": notes,
        "owner": "founder",
        "updated_at": date.today().isoformat(),
    }
    cards.append(card)
    board["updated_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    _save_board_atomic(board)
    return {"ok": True, "card": card}


@app.get("/api/workspace/custom-themes")
def api_get_custom_themes():
    return {"custom_themes": load_custom_themes()}


@app.put("/api/workspace/custom-themes")
def api_put_custom_themes(body: dict = Body(...)):
    themes = body.get("custom_themes")
    if not is_valid_custom_themes(themes):
        return JSONResponse(
            {
                "error": (
                    "custom_themes must be a list (≤24) of objects with string "
                    "id, name and a vars map of string→string"
                )
            },
            status_code=400,
        )
    save_custom_themes(themes)
    return {"ok": True}


@app.get("/api/price-alerts")
def api_list_alerts(symbol: str | None = None):
    return {"alerts": list_price_alerts(symbol=symbol, active_only=True)}


@app.post("/api/price-alerts")
def api_create_alert(body: dict = Body(...)):
    symbol = str(body.get("symbol", "")).strip()
    price = body.get("price")
    direction = str(body.get("direction", "cross")).strip().lower()
    note = str(body.get("note", ""))
    repeat = bool(body.get("repeat", False))
    if not symbol or price is None:
        return JSONResponse({"error": "symbol and price are required"}, status_code=400)
    if direction not in ("above", "below", "cross"):
        return JSONResponse(
            {"error": "direction must be one of: above, below, cross"},
            status_code=400,
        )
    try:
        price_f = float(price)
    except (TypeError, ValueError):
        return JSONResponse({"error": "price must be a number"}, status_code=400)
    alert = create_price_alert(
        symbol=symbol, price=price_f, direction=direction, note=note, repeat=repeat
    )
    return alert


@app.put("/api/price-alerts/{alert_id}")
def api_update_alert(alert_id: int, body: dict = Body(...)):
    price_f: float | None = None
    if body.get("price") is not None:
        try:
            price_f = float(body["price"])
        except (TypeError, ValueError):
            return JSONResponse({"error": "price must be a number"}, status_code=400)
    dir_norm: str | None = None
    if body.get("direction") is not None:
        dir_norm = str(body["direction"]).strip().lower()
        if dir_norm not in ("above", "below", "cross"):
            return JSONResponse(
                {"error": "direction must be one of: above, below, cross"},
                status_code=400,
            )
    updated = update_price_alert(
        alert_id,
        price=price_f,
        direction=dir_norm,
        note=None if body.get("note") is None else str(body["note"]),
        repeat=None if body.get("repeat") is None else bool(body["repeat"]),
    )
    if updated is None:
        return JSONResponse({"error": "Not found"}, status_code=404)
    return updated


@app.delete("/api/price-alerts/{alert_id}")
def api_delete_alert(alert_id: int):
    if not delete_price_alert(alert_id):
        return JSONResponse({"error": "Not found"}, status_code=404)
    return {"ok": True}


@app.post("/api/price-alerts/{alert_id}/trigger")
def api_trigger_alert(alert_id: int):
    active = [a for a in list_price_alerts(active_only=True) if int(a["id"]) == alert_id]
    if not active:
        return JSONResponse({"error": "Not found or already triggered"}, status_code=404)
    mark_alert_triggered(alert_id, repeat=bool(active[0].get("repeat")))
    return {"ok": True, "alert": active[0]}


@app.get("/api/drawings")
def api_list_drawings(symbol: str | None = None):
    return {"drawings": list_chart_drawings(symbol=symbol)}


@app.post("/api/drawings")
def api_create_drawing(body: dict = Body(...)):
    symbol = str(body.get("symbol", "")).strip()
    kind = str(body.get("kind", "")).strip().lower()
    data = body.get("data", {})
    if not symbol or not kind:
        return JSONResponse({"error": "symbol and kind are required"}, status_code=400)
    if kind not in ("level", "trendline", "channel"):
        return JSONResponse(
            {"error": "kind must be one of: level, trendline, channel"},
            status_code=400,
        )
    if not isinstance(data, dict):
        return JSONResponse({"error": "data must be an object"}, status_code=400)
    drawing = create_chart_drawing(symbol=symbol, kind=kind, data=data)
    return drawing


@app.put("/api/drawings/{drawing_id}")
def api_update_drawing(drawing_id: int, body: dict = Body(...)):
    kind_norm: str | None = None
    if body.get("kind") is not None:
        kind_norm = str(body["kind"]).strip().lower()
        if kind_norm not in ("level", "trendline", "channel"):
            return JSONResponse(
                {"error": "kind must be one of: level, trendline, channel"},
                status_code=400,
            )
    data_val: dict | None = None
    if body.get("data") is not None:
        if not isinstance(body["data"], dict):
            return JSONResponse({"error": "data must be an object"}, status_code=400)
        data_val = body["data"]
    updated = update_chart_drawing(
        drawing_id,
        data=data_val,
        kind=kind_norm,
    )
    if updated is None:
        return JSONResponse({"error": "Not found"}, status_code=404)
    return updated


@app.delete("/api/drawings/{drawing_id}")
def api_delete_drawing(drawing_id: int):
    if not delete_chart_drawing(drawing_id):
        return JSONResponse({"error": "Not found"}, status_code=404)
    return {"ok": True}


@app.get("/", response_class=HTMLResponse)
def dashboard() -> str:
    return """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Help Me Trade</title>
  <style>
    body { font-family: system-ui, sans-serif; margin: 2rem; background: #0f1115; color: #e8eaed; }
    h1 { font-size: 1.4rem; margin-bottom: 0.25rem; }
    .meta { color: #9aa0a6; font-size: 0.9rem; margin-bottom: 1.5rem; }
    .cards { display: flex; gap: 1rem; flex-wrap: wrap; margin-bottom: 1.5rem; }
    .card { background: #1a1d24; border: 1px solid #2a2f3a; border-radius: 8px; padding: 1rem 1.25rem; min-width: 140px; }
    .card label { display: block; color: #9aa0a6; font-size: 0.75rem; text-transform: uppercase; }
    .card span { font-size: 1.25rem; font-weight: 600; }
    table { width: 100%; border-collapse: collapse; margin-bottom: 2rem; }
    th, td { text-align: left; padding: 0.6rem 0.75rem; border-bottom: 1px solid #2a2f3a; }
    th { color: #9aa0a6; font-size: 0.8rem; text-transform: uppercase; }
  </style>
</head>
<body>
  <h1>Trading Diary</h1>
  <p class="meta" id="updated">Loading...</p>
  <div class="cards" id="account-cards"></div>
  <h2>Open positions</h2>
  <table>
    <thead><tr>
      <th>Symbol</th><th>Qty</th><th>Side</th><th>Price</th><th>Value</th><th>Unrealized P/L</th>
    </tr></thead>
    <tbody id="positions-body"></tbody>
  </table>
  <h2>Open orders</h2>
  <table>
    <thead><tr>
      <th>Symbol</th><th>Qty</th><th>Side</th><th>Type</th><th>Status</th><th>Filled</th>
    </tr></thead>
    <tbody id="orders-body"></tbody>
  </table>
  <script>
    function money(n) { return "$" + Number(n).toLocaleString(undefined, {minimumFractionDigits: 2, maximumFractionDigits: 2}); }
    function plClass(n) { return Number(n) >= 0 ? "color:#3dd68c" : "color:#f07178"; }

    async function refresh() {
      const res = await fetch("/api/snapshot");
      if (!res.ok) { document.getElementById("updated").textContent = "Waiting for first snapshot..."; return; }
      const data = await res.json();
      document.getElementById("updated").textContent =
        "Last updated: " + new Date(data.captured_at).toLocaleString() + " (polls every __POLL_SECONDS__s)";

      const a = data.account;
      document.getElementById("account-cards").innerHTML = [
        ["Cash", money(a.cash)],
        ["Buying power", money(a.buying_power)],
        ["Portfolio", money(a.portfolio_value)],
        ["Status", a.status],
      ].map(([label, val]) =>
        `<div class="card"><label>${label}</label><span>${val}</span></div>`
      ).join("");

      const posBody = document.getElementById("positions-body");
      if (!data.positions.length) {
        posBody.innerHTML = '<tr><td colspan="6" style="color:#9aa0a6">No open positions</td></tr>';
      } else {
        posBody.innerHTML = data.positions.map(p => `<tr>
          <td>${p.symbol}</td><td>${p.qty}</td><td>${p.side}</td>
          <td>${money(p.current_price)}</td><td>${money(p.market_value)}</td>
          <td style="${plClass(p.unrealized_pl)}">${money(p.unrealized_pl)}</td>
        </tr>`).join("");
      }

      const ordBody = document.getElementById("orders-body");
      if (!data.open_orders.length) {
        ordBody.innerHTML = '<tr><td colspan="6" style="color:#9aa0a6">No open orders</td></tr>';
      } else {
        ordBody.innerHTML = data.open_orders.map(o => `<tr>
          <td>${o.symbol}</td><td>${o.qty}</td><td>${o.side}</td>
          <td>${o.order_type}</td><td>${o.status}</td><td>${o.filled_qty}</td>
        </tr>`).join("");
      }
    }
    refresh();
    setInterval(refresh, 15000);
  </script>
</body>
</html>""".replace("__POLL_SECONDS__", str(POLL_INTERVAL_SECONDS))
