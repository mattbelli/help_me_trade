"""Notification inbox REST API."""

from __future__ import annotations

from fastapi import APIRouter, Body
from fastapi.responses import JSONResponse

import db.base as db_base
from db.notifications import (
    dismiss,
    list_notifications,
    mark_all_read,
    mark_read,
    unread_count,
)
from signals.notify import broadcast_notification_update

router = APIRouter(prefix="/api", tags=["notifications"])


@router.get("/notifications")
def api_list_notifications(
    limit: int = 50,
    before: str | None = None,
    account_id: str | None = None,
    strategy_id: str | None = None,
    include_dismissed: bool = False,
):
    db_path = db_base.DEFAULT_DB_PATH
    notifications = list_notifications(
        limit=limit,
        before=before,
        account_id=account_id,
        strategy_id=strategy_id,
        include_dismissed=include_dismissed,
        db_path=db_path,
    )
    unread = unread_count(account_id=account_id, db_path=db_path)
    return {"notifications": notifications, "unread": unread}


@router.get("/notifications/unread-count")
def api_unread_count(account_id: str | None = None):
    return {"unread": unread_count(account_id=account_id, db_path=db_base.DEFAULT_DB_PATH)}


@router.post("/notifications/read-all")
async def api_mark_all_read(body: dict | None = Body(default=None)):
    account_id = None
    if body and body.get("account_id") is not None:
        account_id = str(body["account_id"]).strip() or None
    updated = mark_all_read(account_id=account_id, db_path=db_base.DEFAULT_DB_PATH)
    return {"updated": updated}


@router.post("/notifications/{notification_id}/read")
async def api_mark_read(notification_id: str):
    row = mark_read(notification_id, db_path=db_base.DEFAULT_DB_PATH)
    if row is None:
        return JSONResponse({"error": "notification not found"}, status_code=404)
    await broadcast_notification_update(
        notification_id=row["id"],
        read=row["read"],
        dismissed=row["dismissed"],
    )
    return {"notification": row}


@router.post("/notifications/{notification_id}/dismiss")
async def api_dismiss(notification_id: str):
    row = dismiss(notification_id, db_path=db_base.DEFAULT_DB_PATH)
    if row is None:
        return JSONResponse({"error": "notification not found"}, status_code=404)
    await broadcast_notification_update(
        notification_id=row["id"],
        read=row["read"],
        dismissed=row["dismissed"],
    )
    return {"notification": row}
