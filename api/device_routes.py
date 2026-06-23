"""Device push-token registration API."""

from __future__ import annotations

from fastapi import APIRouter, Body
from fastapi.responses import JSONResponse

from db.device_tokens import (
    deactivate_device_token,
    list_active_device_tokens,
    register_device_token,
)

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
