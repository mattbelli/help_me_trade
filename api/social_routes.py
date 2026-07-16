"""Social mention buzz API — per-symbol baseline + trending (read-only).

Phase 1: honest mention counts and ET hour-of-day baselines only. No LLM, no
scan integration. Registered by ``api/main`` via ``app.include_router`` (behind
SecurityMiddleware), same pattern as ``api/ticker_routes``.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query

router = APIRouter(prefix="/api/social", tags=["social"])


@router.get("/buzz")
def api_social_buzz(symbol: str = Query(..., min_length=1)) -> dict[str, Any]:
    from db.base import DEFAULT_DB_PATH
    from db.social_mentions import get_buzz

    return get_buzz(symbol, db_path=DEFAULT_DB_PATH)


@router.get("/trending")
def api_social_trending(limit: int = Query(20, ge=1, le=100)) -> dict[str, Any]:
    from db.base import DEFAULT_DB_PATH
    from db.social_mentions import get_trending

    return {"trending": get_trending(db_path=DEFAULT_DB_PATH, limit=limit)}
