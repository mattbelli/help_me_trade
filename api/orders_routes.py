"""Order execution blotter — read-only list of ``order_executions`` rows (paper
Alpaca fills/rejections/etc, audited by the execution chokepoint).

Places no orders and mutates nothing; ``trading/execution_service.py`` is the only
writer to this table. Registered by ``api/main`` via ``app.include_router`` (behind
SecurityMiddleware), same as ``api/scan_routes``.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query

from db.order_executions import list_executions

router = APIRouter(prefix="/api/orders", tags=["orders"])

_MAX_LIMIT = 500

# Curated fields for the blotter response — the DB row also carries audit-only
# fields (risk_checks_json, broker_order_id, proposal_id, ...) not needed here.
_BLOTTER_FIELDS = (
    "id",
    "symbol",
    "side",
    "qty",
    "order_type",
    "limit_price",
    "status",
    "filled_qty",
    "avg_fill_price",
    "execution_mode",
    "submitted_at",
    "created_at",
)


@router.get("/executions")
def api_order_executions(
    status: str | None = None,
    limit: int = Query(default=100, ge=1, le=_MAX_LIMIT),
    account_id: str | None = None,
) -> dict[str, Any]:
    rows = list_executions(status=status, account_id=account_id, limit=limit)
    executions = [{field: row.get(field) for field in _BLOTTER_FIELDS} for row in rows]
    return {"executions": executions, "count": len(executions)}
