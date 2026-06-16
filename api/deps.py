"""FastAPI dependencies."""

from __future__ import annotations

from fastapi import Request

from api.auth import verify_request_host, verify_request_token


def require_api_auth(request: Request) -> None:
    verify_request_host(request)
    verify_request_token(request)
