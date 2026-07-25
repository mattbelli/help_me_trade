"""HTTP middleware for Host and API token checks, plus API cache policy."""

from __future__ import annotations

from fastapi import HTTPException
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from api.auth import (
    MUTATING_METHODS,
    path_requires_auth,
    verify_mutation_token,
    verify_request_host,
    verify_request_token,
)


class SecurityMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        if request.method == "OPTIONS":
            return await call_next(request)

        if path_requires_auth(request.url.path):
            try:
                verify_request_host(request)
                if request.method in MUTATING_METHODS:
                    verify_mutation_token(request)
                else:
                    verify_request_token(request)
            except HTTPException as exc:
                return _no_store(
                    JSONResponse(
                        status_code=exc.status_code,
                        content={"detail": exc.detail},
                    )
                )

        return _no_store(await call_next(request))


def _no_store(response: Response) -> Response:
    """Mark API responses uncacheable server-side.

    The frontend used to pass `cache: "no-store"` per fetch, but Chromium skips
    its CORS *preflight* cache for any request in a no-store/no-cache/reload
    cache mode — so every poll paid a second OPTIONS round trip and ~49% of all
    backend requests were preflights carrying no data (measured 2026-07-25).
    Declaring no-store on the response keeps bodies unreusable while leaving the
    request in the default cache mode, so preflights cache for `max_age`.

    This app serves only dynamic API responses (no StaticFiles/FileResponse), so
    it applies everywhere. Endpoints that set their own Cache-Control keep it —
    the SSE streams in ai_routes.py rely on that.
    """
    response.headers.setdefault("Cache-Control", "no-store")
    return response
