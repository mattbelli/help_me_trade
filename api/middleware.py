"""HTTP middleware for Host and API token checks, API cache policy, slow-request log."""

from __future__ import annotations

import logging
import time

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

logger = logging.getLogger(__name__)

# Nothing recorded how long a request took, so an intermittent "the chart just sits
# there" was undiagnosable: we could not tell a slow backend from a request that never
# arrived. Warm /api/bars is 8-30ms and a cold long-range intraday load is under a
# second (worst measured 5.2s), so a request past this threshold is always an anomaly
# worth one line. Absence of a line is the informative half — it means the browser
# never got its request to us.
_SLOW_REQUEST_SEC = 3.0


def _loggable_query(request: Request) -> str:
    """Query string with the API token stripped — `apiUrlWithToken` puts the token in
    the query for WS/SSE URLs, and the journal is not a place for secrets."""
    kept = [(k, v) for k, v in request.query_params.multi_items() if k != "token"]
    return "?" + "&".join(f"{k}={v}" for k, v in kept) if kept else ""


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

        started = time.perf_counter()
        response = await call_next(request)
        elapsed = time.perf_counter() - started
        if elapsed >= _SLOW_REQUEST_SEC:
            logger.warning(
                "slow request %.1fs %s %s%s",
                elapsed,
                request.method,
                request.url.path,
                _loggable_query(request),
            )
        return _no_store(response)


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
