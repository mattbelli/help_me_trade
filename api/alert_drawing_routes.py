"""Price-alert + chart-drawing CRUD routes (user-drawn levels and the alerts on them).

Extracted verbatim from ``api/main.py`` (2026-08-16 god-module split). Registered by
``api/main`` via ``app.include_router`` (behind SecurityMiddleware). No shared state
with main — pure DB CRUD via ``db.sqlite``.
"""

from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Body
from fastapi.responses import JSONResponse

from db.chart_features import (
    create_chart_drawing,
    create_price_alert,
    delete_chart_drawing,
    delete_price_alert,
    list_chart_drawings,
    list_price_alerts,
    mark_alert_triggered,
    update_chart_drawing,
    update_price_alert,
)
from signals.price_alert_sync import broadcast_price_alert_change

router = APIRouter(tags=["price-alerts", "drawings"])


@router.get("/api/price-alerts")
def api_list_alerts(symbol: str | None = None):
    return {"alerts": list_price_alerts(symbol=symbol, active_only=True)}


@router.post("/api/price-alerts")
def api_create_alert(background_tasks: BackgroundTasks, body: dict = Body(...)):
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
    background_tasks.add_task(
        broadcast_price_alert_change,
        "created",
        alert_id=int(alert["id"]),
        symbol=str(alert["symbol"]),
    )
    return alert


@router.put("/api/price-alerts/{alert_id}")
def api_update_alert(
    alert_id: int,
    background_tasks: BackgroundTasks,
    body: dict = Body(...),
):
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
    background_tasks.add_task(
        broadcast_price_alert_change,
        "updated",
        alert_id=alert_id,
        symbol=str(updated["symbol"]),
    )
    return updated


@router.delete("/api/price-alerts/{alert_id}")
def api_delete_alert(alert_id: int, background_tasks: BackgroundTasks):
    if not delete_price_alert(alert_id):
        return JSONResponse({"error": "Not found"}, status_code=404)
    background_tasks.add_task(
        broadcast_price_alert_change,
        "deleted",
        alert_id=alert_id,
    )
    return {"ok": True}


@router.post("/api/price-alerts/{alert_id}/trigger")
def api_trigger_alert(alert_id: int, background_tasks: BackgroundTasks):
    active = [a for a in list_price_alerts(active_only=True) if int(a["id"]) == alert_id]
    if not active:
        return JSONResponse({"error": "Not found or already triggered"}, status_code=404)
    mark_alert_triggered(alert_id, repeat=bool(active[0].get("repeat")))
    background_tasks.add_task(
        broadcast_price_alert_change,
        "triggered",
        alert_id=alert_id,
        symbol=str(active[0]["symbol"]),
    )
    return {"ok": True, "alert": active[0]}


@router.get("/api/drawings")
def api_list_drawings(symbol: str | None = None):
    return {"drawings": list_chart_drawings(symbol=symbol)}


@router.post("/api/drawings")
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


@router.put("/api/drawings/{drawing_id}")
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


@router.delete("/api/drawings/{drawing_id}")
def api_delete_drawing(drawing_id: int):
    if not delete_chart_drawing(drawing_id):
        return JSONResponse({"error": "Not found"}, status_code=404)
    return {"ok": True}
