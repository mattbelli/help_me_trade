"""FastAPI app host: builds the app, wires routers + middleware, owns the lifespan
(schema init → reconcile pending executions → first poll → job registry → the
background loops) and the app-level routes (health, WS, broker/snapshot, settings,
kill-switch, entries-halt, a few read endpoints).

Everything else lives in ``api/*_routes.py`` (endpoints) and ``api/*_jobs.py`` (loops
and periodic jobs; ``api/background_jobs.py`` is the registry — the manifest of what
runs; ``api/hub.py`` is the live market-data hub). ``tests/test_main_ratchet.py`` fails
if this file grows.
"""

from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from datetime import date

from fastapi import Body, FastAPI, HTTPException, Query, Request, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from ai.llm.factory import clear_llm_cache
from api.ai_routes import router as ai_router
from api.alert_drawing_routes import router as alert_drawing_router
from api.alpaca_guard import _require_alpaca
from api.auth import (
    auth_enabled,
    frontend_origins,
    verify_admin_token,
    verify_websocket_auth,
)
from api.background_jobs import _NEWS_STREAM_ENABLED, _register_background_jobs
from api.bars_routes import router as bars_router
from api.collector_routes import router as collector_router
from api.device_routes import router as device_router
from api.earnings_jobs import _earnings_refresh_loop, _earnings_release_loop
from api.fill_orphan_routes import router as fill_orphan_router
from api.flow_jobs import (
    _OPTIONS_FLOW_EXTERNAL_COLLECTOR,
    _OPTIONS_FLOW_STREAM_ENABLED,
    _flow_runner,
)
from api.flow_routes import router as flow_router
from api.fusion_routes import router as fusion_router
from api.hub import market_hub
from api.jobs import (
    jobs_health,
    mark_alive,
    newest_backup_age_s,
    off_site_push_age_s,
    start_all,
)
from api.journal_routes import router as journal_router
from api.loop_probe import LoopProbe
from api.loop_probe import router as debug_router
from api.middleware import SecurityMiddleware
from api.notification_routes import router as notification_router
from api.ops_jobs import _filings_initial_fill
from api.orders_routes import router as orders_router
from api.performance_routes import router as performance_router
from api.poll_health import poll_health
from api.poll_jobs import POLL_INTERVAL_SECONDS, _poll_loop, _poll_once
from api.reports_routes import (
    _reports_scheduler_loop,
)
from api.reports_routes import router as reports_router
from api.research_jobs import OPTIONS_FLOW_ENABLED as _OPTIONS_FLOW_ENABLED
from api.scan_jobs import (
    _regime_refresh_loop,
    _regime_sleeves_loop,
    _session_node_loop,
    _signal_scan_loop,
)
from api.scan_routes import router as scan_router
from api.scanner_routes import router as scanner_router
from api.schwab_routes import router as schwab_router
from api.social_routes import router as social_router
from api.status_page import router as status_page_router
from api.strategy_lab_routes import router as strategy_lab_router
from api.strategy_routes import router as strategy_router
from api.tape_jobs import _equity_tape_flush_loop
from api.ticker_routes import router as ticker_router
from api.trade_jobs import (
    _alert_symbol_loop,
    _idea_tracker_loop,
    _proposal_sweeper_loop,
    _trade_sync_loop,
)
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
)
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
    list_accounts,
)
from db.ai_tables import delete_rule, init_ai_tables
from db.base import DEFAULT_DB_PATH
from db.chart_features import init_chart_features
from db.chat_sessions import init_chat_tables
from db.device_tokens import init_device_token_tables
from db.earnings_alerts import init_earnings_alert_tables
from db.equity_history import equity_history
from db.filings import init_filings_tables
from db.idea_outcomes import init_idea_outcomes_tables
from db.live_activities import init_live_activity_tables
from db.multi_account import migrate_multi_account
from db.notifications import init_notifications_tables
from db.order_executions import (
    count_pending_executions,
    entry_dates_by_symbol,
    init_execution_tables,
)
from db.paper_orders import init_paper_order_tables
from db.push_deliveries import init_push_delivery_tables
from db.reports import init_reports_tables
from db.session_node import init_session_node_tables
from db.sqlite import (
    get_all_latest_snapshots,
    get_latest_snapshot,
    init_db,
)
from db.strategies import init_strategy_tables, seed_default_strategies
from db.trades import init_trade_tables
from db.workspace import init_workspace_tables
from signals.session_node_strategy import (
    compute_session_levels,
)
from signals.social_buzz import social_buzz_loop
from trading.fill_tracker import reconcile_pending_executions

_LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
logging.basicConfig(
    level=getattr(logging, os.environ.get("LOG_LEVEL", "INFO").upper(), logging.INFO),
    format=_LOG_FORMAT,
)
# Vendor keys ride in the query string (Finnhub token, FRED api_key, AV apikey) and
# httpx logs full URLs — redact before anything reaches the journal.
install_log_redaction(_LOG_FORMAT)
logger = logging.getLogger(__name__)

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
    from trading.single_writer import acquire as acquire_writer_fence

    # One backend per ledger — the submit lock is per-process (see module doc).
    acquire_writer_fence()
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
app.include_router(debug_router)
app.include_router(workspace_router)
app.include_router(reports_router)
app.include_router(bars_router)
app.include_router(scanner_router)
app.include_router(flow_router)
app.include_router(collector_router)
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
        "off_site_push_age_s": off_site_push_age_s(),
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






