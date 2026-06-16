"""HTTP middleware for Host and API token checks."""

from __future__ import annotations

from fastapi import HTTPException
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from api.auth import path_requires_auth, verify_request_host, verify_request_token


class SecurityMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        if request.method == "OPTIONS":
            return await call_next(request)

        if path_requires_auth(request.url.path):
            try:
                verify_request_host(request)
                verify_request_token(request)
            except HTTPException as exc:
                return JSONResponse(
                    status_code=exc.status_code,
                    content={"detail": exc.detail},
                )

        return await call_next(request)
