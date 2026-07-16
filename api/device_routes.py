"""Device push-token registration API."""

from __future__ import annotations

from fastapi import APIRouter, Body
from fastapi.responses import JSONResponse

import db.base as db_base
from db.device_tokens import (
    deactivate_device_token,
    list_active_device_tokens,
    register_device_token,
)
from db.live_activities import bind_update_token_latest, record_update_token

router = APIRouter(prefix="/api", tags=["devices"])


@router.post("/devices")
def api_register_device(body: dict = Body(...)):
    token = str(body.get("token", "")).strip()
    if not token:
        return JSONResponse({"error": "token is required"}, status_code=400)
    register_device_token(
        token=token,
        platform=str(body.get("platform", "ios")).strip() or "ios",
        token_type=str(body.get("token_type", "expo")).strip() or "expo",
        account_id=(
            str(body["account_id"]).strip()
            if body.get("account_id") is not None
            else None
        ),
        app_version=(
            str(body["app_version"]).strip()
            if body.get("app_version") is not None
            else None
        ),
    )
    return {"status": "registered"}


@router.post("/devices/unregister")
def api_unregister_device(body: dict = Body(...)):
    token = str(body.get("token", "")).strip()
    if not token:
        return JSONResponse({"error": "token is required"}, status_code=400)
    deactivate_device_token(token)
    return {"status": "unregistered"}


@router.delete("/devices/{token}")
def api_delete_device(token: str):
    clean = token.strip()
    if not clean:
        return JSONResponse({"error": "token is required"}, status_code=400)
    deactivate_device_token(clean)
    return {"status": "unregistered"}


@router.get("/devices")
def api_list_devices():
    return {"devices": list_active_device_tokens()}


@router.post("/live-activities")
def api_register_live_activity(body: dict = Body(...)):
    proposal_id = str(body.get("proposal_id", "")).strip()
    activity_update_token = str(body.get("activity_update_token", "")).strip()
    device_token = str(body.get("device_token", "")).strip()
    if not activity_update_token or not device_token:
        return JSONResponse(
            {"error": "activity_update_token and device_token are required"},
            status_code=400,
        )
    db_path = db_base.DEFAULT_DB_PATH
    if proposal_id:
        record_update_token(
            proposal_id,
            device_token,
            activity_update_token,
            db_path=db_path,
        )
        return {"ok": True, "bound": "exact"}
    bound_proposal_id = bind_update_token_latest(
        device_token,
        activity_update_token,
        db_path=db_path,
    )
    if bound_proposal_id is None:
        return {"ok": False, "reason": "no pending start for device"}
    return {"ok": True, "bound": "latest", "proposal_id": bound_proposal_id}
