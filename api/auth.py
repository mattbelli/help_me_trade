"""API token and Host-header validation (local DNS-rebinding defense)."""

from __future__ import annotations

import os
import secrets

from fastapi import HTTPException, Request, WebSocket, status

API_TOKEN = os.environ.get("API_TOKEN", "").strip()
FRONTEND_ORIGIN = os.environ.get(
    "FRONTEND_ORIGIN", "http://localhost:3001,http://localhost:3000"
).strip()
BIND_HOST = os.environ.get("BIND_HOST", "127.0.0.1").strip()


def auth_enabled() -> bool:
    return bool(API_TOKEN)


def frontend_origins() -> list[str]:
    origins = [o.strip() for o in FRONTEND_ORIGIN.split(",") if o.strip()]
    return origins or ["http://localhost:3001", "http://localhost:3000"]


def allowed_hosts() -> set[str]:
    hosts = {"127.0.0.1", "localhost", "::1"}
    if BIND_HOST:
        hosts.add(BIND_HOST.lower().split(":")[0])
    return hosts


def _host_from_header(value: str) -> str:
    return value.split(":")[0].strip().lower()


def verify_request_host(request: Request) -> None:
    if not auth_enabled():
        return
    host = _host_from_header(request.headers.get("host", ""))
    if host not in allowed_hosts():
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid Host header",
        )


def extract_token_from_request(request: Request) -> str | None:
    token = request.headers.get("x-api-token", "").strip()
    if token:
        return token
    auth = request.headers.get("authorization", "").strip()
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    query_token = request.query_params.get("token", "").strip()
    return query_token or None


def verify_request_token(request: Request) -> None:
    if not auth_enabled():
        return
    token = extract_token_from_request(request)
    if not token or not secrets.compare_digest(token, API_TOKEN):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing API token",
        )


def extract_token_from_websocket(websocket: WebSocket) -> str | None:
    token = websocket.headers.get("x-api-token", "").strip()
    if token:
        return token
    auth = websocket.headers.get("authorization", "").strip()
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return websocket.query_params.get("token", "").strip() or None


async def verify_websocket_auth(websocket: WebSocket) -> bool:
    """Return True if the connection may proceed, False if closed."""
    if not auth_enabled():
        return True

    host = _host_from_header(websocket.headers.get("host", ""))
    if host not in allowed_hosts():
        await websocket.close(code=1008, reason="Invalid Host header")
        return False

    token = extract_token_from_websocket(websocket)
    if not token or not secrets.compare_digest(token, API_TOKEN):
        await websocket.close(code=1008, reason="Unauthorized")
        return False

    return True


def path_requires_auth(path: str) -> bool:
    if path == "/health":
        return False
    return path.startswith("/api/") or path.startswith("/ws/") or path == "/"
