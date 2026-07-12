"""Fill-attribution orphan repair surface.

Read + a single bookkeeping mutation over ``fill_attribution_orphans``: list the
orphans (strategy BUY fills with no exit management attached) and let the founder
mark one 'repaired' after attaching management by hand. Pure DB bookkeeping — it
never touches the broker or the execution record (that stays the RiskEngine /
execution-chokepoint's job).
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

import db.base as db_base
from api.auth import verify_mutation_token
from db.fill_attribution import list_orphans, mark_orphan_repaired

router = APIRouter(prefix="/api", tags=["fill-orphans"])


@router.get("/fill-orphans")
def api_list_fill_orphans(status: str | None = None, limit: int = 200):
    orphans = list_orphans(
        status=status, limit=limit, db_path=db_base.DEFAULT_DB_PATH
    )
    return {"orphans": orphans}


@router.post("/fill-orphans/{orphan_id}/repair")
def api_repair_fill_orphan(orphan_id: str, request: Request):
    """Founder-only: flip an open/notified orphan to 'repaired'. Bookkeeping
    only; idempotent (already-repaired / unknown ids are a no-op 404)."""
    verify_mutation_token(request)
    repaired = mark_orphan_repaired(orphan_id, db_path=db_base.DEFAULT_DB_PATH)
    if not repaired:
        return JSONResponse(
            {"error": "orphan not found or already repaired"}, status_code=404
        )
    return {"ok": True, "orphan_id": orphan_id, "status": "repaired"}
