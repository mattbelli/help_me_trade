"""FastAPI app: poll Alpaca, store snapshots, serve JSON + simple HTML."""

from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Body, FastAPI, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse

from ai.llm.factory import clear_llm_cache
from ai.reflection.scheduler import reflection_loop
from ai.rules.engine import evaluate_on_snapshot
from api.ai_routes import router as ai_router
from api.auth import auth_enabled, frontend_origins, verify_websocket_auth
from api.middleware import SecurityMiddleware
from api.performance_routes import router as performance_router
from api.poll_health import poll_health
from api.trade_routes import router as trade_router
from brokers.alpaca import AlpacaAdapter
from brokers.errors import BrokerError
from brokers.factory import (
    aclose_broker_cache,
    clear_broker_cache,
    get_broker,
    get_enabled_broker_names,
)
from brokers.market_stream import market_hub
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
from db.ai_tables import init_ai_tables
from db.chart_features import (
    create_price_alert,
    delete_price_alert,
    init_chart_features,
    list_price_alerts,
    mark_alert_triggered,
)
from db.chat_sessions import init_chat_tables
from db.order_executions import (
    count_pending_paper_executions,
    count_submitted_executions,
    init_execution_tables,
)
from db.paper_orders import init_paper_order_tables
from db.sqlite import (
    compute_and_save_snapshot_analytics,
    get_all_latest_snapshots,
    get_latest_snapshot,
    init_db,
    save_snapshot,
)
from db.trades import init_trade_tables
from signals.scan import run_signal_scan
from trading.fill_tracker import reconcile_open_executions, reconcile_pending_executions
from trading.sync import async_sync_all_enabled_brokers

ROOT = Path(__file__).resolve().parent.parent
POLL_INTERVAL_SECONDS = max(
    5, int(os.environ.get("POLL_INTERVAL_SECONDS", "20"))
)
SIGNAL_SCAN_INTERVAL_SECONDS = max(
    30, int(os.environ.get("SIGNAL_SCAN_INTERVAL_SECONDS", "300"))
)
logging.basicConfig(
    level=getattr(logging, os.environ.get("LOG_LEVEL", "INFO").upper(), logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


async def _poll_broker(broker_name: str) -> bool:
    if not broker_is_configured(broker_name):
        logger.warning("Skipping %s — not configured", broker_name)
        return False
    try:
        broker = get_broker(broker_name)
        account, positions, orders = await broker.afetch_snapshot()
        captured_at = await asyncio.to_thread(
            save_snapshot,
            account=account,
            positions=positions,
            orders=orders,
            broker=broker_name,
        )
        logger.info("Snapshot saved at %s (%s)", captured_at, broker_name)
        await asyncio.to_thread(compute_and_save_snapshot_analytics, broker_name)
        if count_pending_paper_executions(broker_name) > 0:
            await reconcile_pending_executions(broker_name)
        if count_submitted_executions(broker_name) > 0:
            await reconcile_open_executions(broker_name)
        snapshot = await asyncio.to_thread(get_latest_snapshot, broker_name)
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
    broker_names = get_enabled_broker_names()
    if not broker_names:
        return
    results = await asyncio.gather(
        *[_poll_broker(name) for name in broker_names],
        return_exceptions=False,
    )
    if any(results):
        poll_health.record_success()


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
                await market_hub.broadcast_alert(
                    {
                        "source": "signal_scan",
                        "rule_id": item["rule_id"],
                        "rule_name": item["rule_name"],
                        "action": item["action"],
                    }
                )
        except Exception:
            logger.exception("Signal scan loop iteration failed")
        await asyncio.sleep(SIGNAL_SCAN_INTERVAL_SECONDS)


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


@asynccontextmanager
async def lifespan(app: FastAPI):
    if not auth_enabled():
        logger.warning(
            "API_TOKEN is not set — API and WebSocket routes are unauthenticated. "
            "Set API_TOKEN in .env for production-like local security."
        )
    init_db()
    init_chart_features()
    init_ai_tables()
    init_chat_tables()
    init_trade_tables()
    init_paper_order_tables()
    init_execution_tables()
    await market_hub.start()
    for name in get_enabled_broker_names():
        if broker_is_configured(name) and count_pending_paper_executions(name) > 0:
            await reconcile_pending_executions(name)
    await _poll_once()
    poll_task = asyncio.create_task(_poll_loop())
    scan_task = asyncio.create_task(_signal_scan_loop())
    alert_task = asyncio.create_task(_alert_symbol_loop())
    reflection_task = asyncio.create_task(reflection_loop())
    sync_task = asyncio.create_task(_trade_sync_loop())
    yield
    sync_task.cancel()
    poll_task.cancel()
    scan_task.cancel()
    alert_task.cancel()
    reflection_task.cancel()
    for task in (poll_task, scan_task, alert_task, reflection_task, sync_task):
        try:
            await task
        except asyncio.CancelledError:
            pass
    await market_hub.stop()
    await aclose_broker_cache()


app = FastAPI(title="Help Me Trade", lifespan=lifespan)
app.include_router(ai_router)
app.include_router(trade_router)
app.include_router(performance_router)

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


def _combined_snapshot_response() -> dict | None:
    all_snaps = get_all_latest_snapshots()
    if not all_snaps:
        return None

    primary = primary_broker()
    primary_snap = next((s for s in all_snaps if s["broker"] == primary), all_snaps[0])

    return {
        **primary_snap,
        "primary_broker": primary,
        "broker": primary_snap["broker"],
        "snapshots": all_snaps,
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


@app.get("/api/watchlist/market")
async def api_watchlist_market(symbols: str = ""):
    broker = _require_alpaca()
    if isinstance(broker, JSONResponse):
        return broker

    symbol_list = [
        s.strip().upper()
        for s in symbols.split(",")
        if s.strip()
    ]
    if not symbol_list:
        symbol_list = get_watchlist()
    if not symbol_list:
        return {"quotes": {}}

    try:
        bars_by_symbol = await broker.aget_stock_bars_batch(symbol_list, days=12)
    except BrokerError as exc:
        return JSONResponse({"error": str(exc)}, status_code=502)

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
        quotes[sym] = {
            "price": price,
            "change_pct": round(change_pct, 2),
            "sparkline": [round(c, 4) for c in closes[-7:]],
            "prev_close": prev,
            "volume": float(latest.volume),
            "day_high": float(latest.high),
            "day_low": float(latest.low),
        }

    return {"quotes": quotes}


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


@app.get("/api/price-alerts")
def api_list_alerts(symbol: str | None = None):
    return {"alerts": list_price_alerts(symbol=symbol, active_only=True)}


@app.post("/api/price-alerts")
def api_create_alert(body: dict = Body(...)):
    symbol = str(body.get("symbol", "")).strip()
    price = body.get("price")
    direction = str(body.get("direction", "cross"))
    note = str(body.get("note", ""))
    if not symbol or price is None:
        return JSONResponse(
            {"error": "symbol and price are required"}, status_code=400
        )
    try:
        price_f = float(price)
    except (TypeError, ValueError):
        return JSONResponse({"error": "price must be a number"}, status_code=400)
    alert = create_price_alert(
        symbol=symbol, price=price_f, direction=direction, note=note
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
    mark_alert_triggered(alert_id)
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
