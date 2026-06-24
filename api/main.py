"""FastAPI app: poll Alpaca, store snapshots, serve JSON + simple HTML."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from contextlib import asynccontextmanager
from datetime import date, datetime
from pathlib import Path

from fastapi import Body, FastAPI, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse

from ai.llm.factory import clear_llm_cache
from ai.reflection.scheduler import reflection_loop
from ai.rules.engine import evaluate_on_snapshot
from api.ai_routes import router as ai_router
from api.auth import auth_enabled, frontend_origins, verify_websocket_auth
from api.device_routes import router as device_router
from api.middleware import SecurityMiddleware
from api.performance_routes import router as performance_router
from api.poll_health import poll_health
from api.strategy_routes import router as strategy_router
from api.trade_routes import router as trade_router
from brokers.alpaca import AlpacaAdapter, StockBar
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
from brokers.market_stream import AlpacaMarketStreamHub, BaseMarketHub
from brokers.schwab_market_stream import SchwabMarketStreamHub
from config.settings import (
    add_watchlist_symbol,
    apply_settings_patch,
    broker_is_configured,
    create_watchlist,
    delete_watchlist,
    get_chart_layout,
    get_watchlist,
    get_watchlists_state,
    load_settings,
    mask_settings,
    primary_broker,
    remove_watchlist_symbol,
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
from db.ai_tables import create_rule, get_rule, init_ai_tables
from db.chart_features import (
    create_price_alert,
    delete_price_alert,
    init_chart_features,
    list_price_alerts,
    mark_alert_triggered,
)
from db.chat_sessions import init_chat_tables
from db.device_tokens import init_device_token_tables
from db.earnings_calendar import next_earnings_date
from db.multi_account import migrate_multi_account
from db.order_executions import (
    count_pending_paper_executions,
    count_submitted_executions,
    init_execution_tables,
)
from db.paper_orders import init_paper_order_tables
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
from db.workspace import init_workspace_tables, load_workspace_layout, save_workspace_layout
from signals.earnings_refresh import arefresh_earnings_calendar
from signals.scan import run_signal_scan
from signals.session_node_strategy import run_session_node_scan
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
POLL_INTERVAL_SECONDS = max(
    5, int(os.environ.get("POLL_INTERVAL_SECONDS", "20"))
)
SIGNAL_SCAN_INTERVAL_SECONDS = max(
    30, int(os.environ.get("SIGNAL_SCAN_INTERVAL_SECONDS", "300"))
)
EARNINGS_REFRESH_INTERVAL_SECONDS = 24 * 3600
VOLUME_PROFILE_REFRESH_INTERVAL_SECONDS = 24 * 3600
RVOL_ALERT_INTERVAL_SECONDS = max(
    60, int(os.environ.get("RVOL_ALERT_INTERVAL_SECONDS", "300"))
)
RVOL_ALERT_THRESHOLD = float(os.environ.get("RVOL_ALERT_THRESHOLD", "3.0"))
_RVOL_ALERTS_ENABLED = os.environ.get("RVOL_ALERTS_ENABLED", "1").strip() not in ("0", "false", "no")
SCHWAB_HEALTH_INTERVAL_SECONDS = max(
    300, int(os.environ.get("SCHWAB_HEALTH_INTERVAL_SECONDS", str(6 * 3600)))
)
_SCHWAB_HEALTH_ALERTS_ENABLED = (
    os.environ.get("SCHWAB_HEALTH_ALERTS_ENABLED", "1").strip() not in ("0", "false", "no")
)
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
market_hub: BaseMarketHub = (
    SchwabMarketStreamHub()
    if market_data_provider_name() == "schwab"
    else AlpacaMarketStreamHub()
)


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
                for item in triggered:
                    await market_hub.broadcast_alert(
                        {
                            "source": "rule",
                            "rule_id": item["rule_id"],
                            "rule_name": item["rule_name"],
                            "action": item["action"],
                            "broker": broker_name,
                        }
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
                for item in triggered:
                    await market_hub.broadcast_alert(
                        {
                            "source": "rule",
                            "rule_id": item["rule_id"],
                            "rule_name": item["rule_name"],
                            "action": item["action"],
                            "broker": broker_name,
                        }
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
                    await market_hub.broadcast_alert(
                        {
                            "source": "signal_scan",
                            "rule_id": item["rule_id"],
                            "rule_name": item["rule_name"],
                            "action": item["action"],
                        }
                    )
            try:
                await run_strategy_scan()
            except Exception:
                logger.exception("Dip-strategy scan failed")
            try:
                await run_session_node_scan()
            except Exception:
                logger.exception("Session-node scan failed")
        except Exception:
            logger.exception("Signal scan loop iteration failed")
        await asyncio.sleep(SIGNAL_SCAN_INTERVAL_SECONDS)


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
        await asyncio.sleep(EARNINGS_REFRESH_INTERVAL_SECONDS)


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
        await asyncio.sleep(VOLUME_PROFILE_REFRESH_INTERVAL_SECONDS)


async def _trade_sync_loop() -> None:
    """Sync trade ledger from enabled brokers on startup, then periodically."""
    try:
        results = await async_sync_all_enabled_brokers()
        for r in results:
            logger.info("Trade sync on connect: %s", r)
    except Exception:
        logger.exception("Initial trade sync failed")
    while True:
        await asyncio.sleep(3600)
        try:
            results = await async_sync_all_enabled_brokers()
            for r in results:
                logger.info("Periodic trade sync: %s", r)
        except Exception:
            logger.exception("Periodic trade sync failed")


async def _alert_symbol_loop() -> None:
    while True:
        try:
            symbols = {
                str(a["symbol"]).upper()
                for a in list_price_alerts(active_only=True)
            }
            await market_hub.ensure_symbols(symbols)
        except Exception:
            logger.exception("Alert symbol loop iteration failed")
        await asyncio.sleep(30)


# ---------------------------------------------------------------------------
# RVOL-spike alert state: last-alert date per symbol (ET day, resets each day)
# ---------------------------------------------------------------------------
_rvol_alert_last_date: dict[str, date] = {}


def _should_rvol_alert(symbol: str, today_et: date) -> bool:
    """Return True if we have NOT already sent an RVOL alert for this symbol today."""
    return _rvol_alert_last_date.get(symbol) != today_et


def _mark_rvol_alerted(symbol: str, today_et: date) -> None:
    _rvol_alert_last_date[symbol] = today_et


async def _rvol_alert_loop() -> None:
    """Every ~5 min during RTH, push a phone alert for any watchlist symbol with RVOL ≥ threshold.

    Env gates:
      RVOL_ALERTS_ENABLED        default "1"  (set to "0" to disable)
      RVOL_ALERT_THRESHOLD       default 3.0
      RVOL_ALERT_INTERVAL_SECONDS default 300
    """
    while True:
        await asyncio.sleep(RVOL_ALERT_INTERVAL_SECONDS)
        if not _RVOL_ALERTS_ENABLED:
            continue
        try:
            if _market_session() != "regular":
                continue

            bucket = _rvol_bucket_now()
            if bucket is None:
                continue

            symbols = _all_watchlist_symbols()
            if not symbols:
                continue

            try:
                provider = get_market_data_provider()
            except BrokerError as exc:
                logger.warning("RVOL alert: provider unavailable: %s", exc)
                continue

            if not hasattr(provider, "aget_quotes"):
                continue

            from brokers.schwab_market_data import SchwabMarketData

            if not isinstance(provider, SchwabMarketData):
                continue

            try:
                quotes = await provider.aget_quotes(symbols)
            except Exception:
                logger.exception("RVOL alert: quote fetch failed")
                continue

            today_et = datetime.now(_ET).date()

            from signals.push import send_alert_push

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
                    await send_alert_push(
                        title="Unusual volume",
                        body=f"{sym} {rvol_val:.1f}× normal volume ({price_str})",
                    )
                    _mark_rvol_alerted(sym, today_et)
                    logger.info("RVOL alert sent: %s rvol=%.2f", sym, rvol_val)
                except Exception:
                    logger.exception("RVOL alert: error processing symbol %s", sym)
        except Exception:
            logger.exception("RVOL alert loop iteration failed")


# Schwab health alert state: last-alert date (resets each ET day)
_schwab_health_last_date: dict[str, date] = {}  # key: alert type ("expiring"/"offline")


async def _schwab_health_loop() -> None:
    """Every ~6h, check Schwab token health and push if auth is expiring or offline.

    Env gates:
      SCHWAB_HEALTH_ALERTS_ENABLED   default "1"  (set to "0" to disable)
      SCHWAB_HEALTH_INTERVAL_SECONDS default 21600
    """
    while True:
        await asyncio.sleep(SCHWAB_HEALTH_INTERVAL_SECONDS)
        if not _SCHWAB_HEALTH_ALERTS_ENABLED:
            continue
        try:
            from brokers.schwab_auth import auth_status
            from signals.push import send_alert_push

            status = auth_status()
            today_et = datetime.now(_ET).date()

            if not status.get("authenticated"):
                if _schwab_health_last_date.get("offline") != today_et:
                    await send_alert_push(
                        title="Schwab data offline",
                        body="Token invalid; re-auth needed. Run scripts.schwab_reauth or re-login.",
                    )
                    _schwab_health_last_date["offline"] = today_et
                    logger.warning("Schwab health alert: token offline")
            else:
                days_left = status.get("estimated_days_until_reauth")
                if days_left is not None and days_left <= 1.0:
                    if _schwab_health_last_date.get("expiring") != today_et:
                        await send_alert_push(
                            title="Schwab re-auth needed",
                            body=(
                                f"Token expires within ~{days_left:.1f} day(s). "
                                "Run scripts.schwab_reauth or re-login."
                            ),
                        )
                        _schwab_health_last_date["expiring"] = today_et
                        logger.warning(
                            "Schwab health alert: token expiring in %.2f days", days_left
                        )
        except Exception:
            logger.exception("Schwab health loop iteration failed")


@asynccontextmanager
async def lifespan(app: FastAPI):
    if not auth_enabled():
        logger.warning(
            "API_TOKEN is not set — API and WebSocket routes are unauthenticated. "
            "Set API_TOKEN in .env for production-like local security."
        )
    init_db()
    init_chart_features()
    init_workspace_tables()
    init_ai_tables()
    init_session_node_tables()
    if get_rule("session_node_spy") is None:
        create_rule(
            rule_id="session_node_spy",
            name="Session-Node Reversion (SPY)",
            conditions={"type": "session_node", "symbol": "SPY"},
            action={
                "type": "trade",
                "symbol": "SPY",
                "side": "buy",
                "notional_usd": 500,
                "order_type": "market",
            },
        )
    init_chat_tables()
    init_trade_tables()
    init_paper_order_tables()
    init_device_token_tables()
    init_execution_tables()
    migrate_multi_account()
    init_strategy_tables()
    seed_default_strategies()
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
    poll_task = asyncio.create_task(_poll_loop())
    scan_task = asyncio.create_task(_signal_scan_loop())
    alert_task = asyncio.create_task(_alert_symbol_loop())
    reflection_task = asyncio.create_task(reflection_loop())
    sync_task = asyncio.create_task(_trade_sync_loop())
    earnings_task = asyncio.create_task(_earnings_refresh_loop())
    volume_profile_task = asyncio.create_task(_volume_profile_refresh_loop())
    rvol_alert_task = asyncio.create_task(_rvol_alert_loop())
    schwab_health_task = asyncio.create_task(_schwab_health_loop())
    yield
    earnings_task.cancel()
    volume_profile_task.cancel()
    rvol_alert_task.cancel()
    schwab_health_task.cancel()
    sync_task.cancel()
    poll_task.cancel()
    scan_task.cancel()
    alert_task.cancel()
    reflection_task.cancel()
    for task in (
        poll_task,
        scan_task,
        alert_task,
        reflection_task,
        sync_task,
        earnings_task,
        volume_profile_task,
        rvol_alert_task,
        schwab_health_task,
    ):
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

app.add_middleware(SecurityMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=frontend_origins(),
    allow_credentials=True,
    allow_methods=["GET", "PUT", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)


@app.get("/health")
def health() -> dict[str, int | str | float | bool | None]:
    age = poll_health.age_seconds()
    return {
        "status": "ok",
        "poll_interval_seconds": POLL_INTERVAL_SECONDS,
        "last_poll_age_seconds": age,
        "poll_healthy": poll_health.is_healthy(POLL_INTERVAL_SECONDS),
    }


@app.websocket("/ws/market")
async def ws_market(websocket: WebSocket):
    if not await verify_websocket_auth(websocket):
        return
    await market_hub.handle_client(websocket)


@app.get("/api/alerts/recent")
def api_alerts_recent() -> dict[str, list[dict]]:
    return {"alerts": market_hub.recent_alerts()}


@app.get("/api/broker")
def api_broker():
    settings = load_settings()
    primary = primary_broker()
    brokers_payload: dict = {}

    for name in ("alpaca", "schwab"):
        cfg = settings["brokers"].get(name, {})
        entry: dict = {
            "enabled": bool(cfg.get("enabled")),
            "configured": broker_is_configured(name),
        }
        if name == "schwab":
            from brokers.schwab_auth import auth_status

            entry["auth"] = auth_status()
        brokers_payload[name] = entry

    return {
        "broker": primary,
        "primary_broker": primary,
        "brokers": brokers_payload,
    }


def _aggregate_account_snapshots(snaps: list[dict]) -> dict:
    """Sum cash / buying power / portfolio value / unrealized P&L across accounts."""
    cash = bp = pv = upl = 0.0
    for s in snaps:
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
        "account_count": len(snaps),
    }


def _combined_snapshot_response() -> dict | None:
    all_snaps = get_all_latest_snapshots()
    if not all_snaps:
        return None

    # Drop legacy NULL-account snapshots when real per-account ones exist.
    labeled = [s for s in all_snaps if s.get("account_id")]
    if labeled:
        all_snaps = labeled

    # Attach a stable trade-account label + default flag to each account snapshot.
    for s in all_snaps:
        acct = get_account(str(s["account_id"])) if s.get("account_id") else None
        s["account_label"] = acct["label"] if acct else None
        s["is_default"] = bool(acct["is_default"]) if acct else False

    primary = primary_broker()
    # Top-level summary is pinned to the DEFAULT account (deterministic) rather
    # than whichever account polled last — that last-one-wins behavior was the
    # source of the dashboard flicker between accounts.
    primary_snap = (
        next((s for s in all_snaps if s.get("is_default")), None)
        or next((s for s in all_snaps if s["broker"] == primary), None)
        or all_snaps[0]
    )

    return {
        **primary_snap,
        "primary_broker": primary,
        "broker": primary_snap["broker"],
        "snapshots": all_snaps,
        "all_accounts_summary": _aggregate_account_snapshots(all_snaps),
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
def api_put_settings(patch: dict = Body(...)):
    try:
        updated = apply_settings_patch(patch)
        clear_llm_cache()
        clear_broker_cache()
    except (TypeError, ValueError) as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    return mask_settings(updated)


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
    try:
        state = create_watchlist(name, symbols if isinstance(symbols, list) else None)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    return state


@app.put("/api/watchlists/{list_id}")
def api_update_watchlist(list_id: str, body: dict = Body(...)):
    name = body.get("name")
    symbols = body.get("symbols")
    if name is not None and not isinstance(name, str):
        return JSONResponse({"error": "name must be a string"}, status_code=400)
    if symbols is not None and not isinstance(symbols, list):
        return JSONResponse({"error": "symbols must be a list"}, status_code=400)
    try:
        state = update_watchlist(
            list_id,
            name=name if isinstance(name, str) else None,
            symbols=symbols if isinstance(symbols, list) else None,
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


def _market_session() -> str:
    """Return the current US equities market session label based on ET clock.

    pre:       04:00–09:30 ET (weekday)
    regular:   09:30–16:00 ET (weekday)
    post:      16:00–20:00 ET (weekday)
    overnight: the 24/5 session — Sun 20:00 ET → Fri 20:00 ET, outside pre/regular/post.
               Many liquid names trade overnight and Schwab's `extended` block is live then.
    closed:    Fri 20:00 ET → Sun 20:00 ET (weekend).
    """
    now = datetime.now(_ET)
    wd = now.weekday()  # Mon=0 .. Sun=6
    minutes = now.hour * 60 + now.minute
    if wd <= 4:  # weekday RTH/pre/post windows
        if 4 * 60 <= minutes < 9 * 60 + 30:
            return "pre"
        if 9 * 60 + 30 <= minutes < 16 * 60:
            return "regular"
        if 16 * 60 <= minutes < 20 * 60:
            return "post"
    # Weekend closure: Fri after 20:00, all Saturday, Sunday before 20:00.
    if (wd == 4 and minutes >= 20 * 60) or wd == 5 or (wd == 6 and minutes < 20 * 60):
        return "closed"
    return "overnight"


@app.get("/api/watchlist/market")
async def api_watchlist_market(symbols: str = ""):
    try:
        provider = get_market_data_provider()
    except BrokerError as exc:
        return JSONResponse({"error": str(exc)}, status_code=502)

    symbol_list = [
        s.strip().upper()
        for s in symbols.split(",")
        if s.strip()
    ]
    if not symbol_list:
        symbol_list = get_watchlist()
    if not symbol_list:
        return {"quotes": {}}

    # Any requested symbol with no fresh volume profile gets one built in the
    # background (deduped/cooldowned) so RVOL works for ANY stock, not just the
    # nightly-precomputed watchlist. It populates within a poll cycle.
    for sym in symbol_list:
        if load_profile(sym) is None:
            _schedule_profile_build(provider, sym)

    try:
        bars_by_symbol = await provider.aget_stock_bars_batch(symbol_list, days=12)
    except BrokerError as exc:
        return JSONResponse({"error": str(exc)}, status_code=502)

    intraday_by_symbol: dict[str, list[StockBar]] = {}
    try:
        intraday_by_symbol = await provider.aget_stock_bars_batch(
            symbol_list, timeframe="5Min", days=4
        )
    except BrokerError as exc:
        logger.warning("Watchlist intraday bars failed: %s", exc)

    # Extended-hours quotes — Schwab only; fail-soft to empty dict on any error.
    ext_quotes: dict[str, dict] = {}
    if hasattr(provider, "aget_quotes"):
        try:
            from brokers.schwab_market_data import SchwabMarketData

            if isinstance(provider, SchwabMarketData):
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
        intraday = _session_intraday_closes(intraday_by_symbol.get(sym, []))

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

        ext_price_val = eq.get("ext_last") if session in ("pre", "post", "overnight") else None
        ext_change_val = (
            eq.get("ext_change_pct") if session in ("pre", "post", "overnight") else None
        )

        quotes[sym] = {
            "price": price,
            "change_pct": round(change_pct, 2),
            "sparkline": [round(c, 4) for c in closes[-7:]],
            "prev_close": prev,
            "volume": float(latest.volume),
            "day_high": float(latest.high),
            "day_low": float(latest.low),
            "intraday": intraday,
            "market_session": session,
            "ext_price": ext_price_val,
            "ext_change_pct": ext_change_val,
            "pe": eq.get("pe_ratio"),
            "eps": eq.get("eps"),
            "div_yield": eq.get("div_yield"),
            "market_cap": market_cap,
            "last_earnings": eq.get("last_earnings_date"),
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

    return {"quotes": quotes}


_profile_build_attempts: dict[str, float] = {}
_PROFILE_BUILD_COOLDOWN_S = 600  # don't re-attempt a symbol's profile more than once / 10 min


def _schedule_profile_build(provider, symbol: str) -> None:
    """Fire-and-forget a volume-profile build for one symbol, deduped by cooldown.

    Guards against hammering Schwab when a symbol can't form a profile (illiquid →
    compute returns None → no file saved → would otherwise retry every poll)."""
    now = time.time()
    if now - _profile_build_attempts.get(symbol, 0.0) < _PROFILE_BUILD_COOLDOWN_S:
        return
    _profile_build_attempts[symbol] = now

    async def _run() -> None:
        try:
            await build_profile(provider, symbol)
        except Exception:
            logger.exception("On-demand volume-profile build failed for %s", symbol)

    asyncio.create_task(_run())


def _compute_rvol_field(symbol: str, intraday_bars: list[StockBar]) -> dict | None:
    profile = load_profile(symbol)
    if profile is None or not intraday_bars:
        return None
    result = rvol_now(profile, intraday_bars)
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
    from brokers.schwab_market_data import SchwabMarketData

    if limit < 1 or limit > 200:
        return JSONResponse({"error": "limit must be 1–200"}, status_code=400)
    if min_rvol < 0:
        return JSONResponse({"error": "min_rvol must be >= 0"}, status_code=400)

    try:
        provider = get_market_data_provider()
    except BrokerError as exc:
        return JSONResponse({"error": str(exc)}, status_code=502)

    if not isinstance(provider, SchwabMarketData):
        return JSONResponse(
            {
                "error": (
                    "RVOL scanner requires Schwab as the market data provider "
                    "(needs quote.totalVolume). Set MARKET_DATA_PROVIDER=schwab."
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
        symbols[i : i + _SCANNER_CHUNK_SIZE]
        for i in range(0, len(symbols), _SCANNER_CHUNK_SIZE)
    ]
    try:
        chunk_results = await asyncio.gather(
            *(provider.aget_quotes(chunk) for chunk in chunks)
        )
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


def _session_intraday_closes(bars: list[StockBar]) -> list[float]:
    if not bars:
        return []

    def _bar_et(bar: StockBar) -> datetime:
        return datetime.fromisoformat(bar.time.replace("Z", "+00:00")).astimezone(_ET)

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

    symbol_list = [
        s.strip().upper()
        for s in symbols.split(",")
        if s.strip()
    ]
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


@app.get("/api/bars")
async def api_bars(symbol: str, timeframe: str = "1Day", days: int = 180):
    broker = _require_alpaca()
    if isinstance(broker, JSONResponse):
        return broker

    if days < 1 or days > 3650:
        return JSONResponse({"error": "days must be 1–3650"}, status_code=400)

    try:
        bars = await broker.aget_stock_bars(symbol, timeframe=timeframe, days=days)
    except BrokerError as exc:
        return JSONResponse({"error": str(exc)}, status_code=502)

    return {
        "symbol": symbol.strip().upper(),
        "timeframe": timeframe,
        "bars": [
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
    }


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
            year: int = item["year"]
            quarter: int = item["quarter"]
            label = f"Q{quarter}'{year % 100:02d}"
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
                {
                    "error": (
                        "each layout item must have string i, type and numeric x, y, w, h"
                    )
                },
                status_code=400,
            )
    save_workspace_layout(layout)
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
        return JSONResponse(
            {"error": "symbol and price are required"}, status_code=400
        )
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
