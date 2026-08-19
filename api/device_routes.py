"""Device push-token registration API."""

from __future__ import annotations

import base64
import binascii

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from fastapi import APIRouter, Body, HTTPException, Request
from fastapi.responses import JSONResponse

import db.base as db_base
from api.auth import verify_admin_token
from db.approval_devices import enroll_device, list_devices, revoke_device
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


# --- Approval devices: the phones whose Ed25519 key may approve a proposal ---
# Enroll + revoke are risk-control changes → ADMIN_TOKEN only (the phone asks
# for it once, sends it, never stores it). See api/approval_auth.py.


@router.post("/approval-devices")
def api_enroll_approval_device(request: Request, body: dict = Body(...)):
    try:
        verify_admin_token(request)
    except HTTPException as exc:
        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
    device_id = str(body.get("device_id", "")).strip()
    public_key = str(body.get("public_key", "")).strip()
    label = str(body.get("label", "")).strip()[:64]
    if not device_id or not public_key:
        return JSONResponse(
            {"error": "device_id and public_key are required"}, status_code=400
        )
    try:
        Ed25519PublicKey.from_public_bytes(base64.b64decode(public_key, validate=True))
    except (ValueError, binascii.Error):
        return JSONResponse(
            {"error": "public_key must be a base64 raw 32-byte Ed25519 key"},
            status_code=400,
        )
    enroll_device(
        device_id=device_id,
        public_key=public_key,
        label=label,
        db_path=db_base.DEFAULT_DB_PATH,
    )
    return {"status": "enrolled", "device_id": device_id}


@router.get("/approval-devices")
def api_list_approval_devices():
    return {"devices": list_devices(db_path=db_base.DEFAULT_DB_PATH)}


@router.post("/approval-devices/{device_id}/revoke")
def api_revoke_approval_device(request: Request, device_id: str):
    try:
        verify_admin_token(request)
    except HTTPException as exc:
        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
    if not revoke_device(device_id.strip(), db_path=db_base.DEFAULT_DB_PATH):
        return JSONResponse({"error": "Not found or already revoked"}, status_code=404)
    return {"status": "revoked", "device_id": device_id}
