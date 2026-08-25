"""Manual desk endpoints — the cockpit's order surface (ticket / chart / DOM).

Money-moving verbs (place / cancel / replace / flatten) require the ADMIN
token — the same authority as a desk approval, and for the same reason: the
shared API_TOKEN is compiled into the web bundle and sits on every client.
`GET /limits` is read-only and rides the shared token.
"""

from __future__ import annotations

import logging
from decimal import Decimal, InvalidOperation
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from api import auth as _auth
from api.auth import (
    _token_matches,
    extract_token_from_request,
    verify_admin_token,
)
from config.settings import is_kill_switch_active, trading_config
from db.accounts import default_execution_account, get_account
from db.base import DEFAULT_DB_PATH
from db.order_executions import count_executions_today
from db.sqlite import get_latest_snapshot
from trading.manual_orders import (
    cancel_manual_order,
    close_manual_position,
    place_manual_order,
    replace_manual_order,
)
from trading.market_hours import is_market_open

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/manual", tags=["manual-trading"])


# Money verbs are approvals, not reads: ADMIN token only, never the shared
# token (compiled into the web bundle). One authority check, shared with the
# risk-control routes.
_require_admin = verify_admin_token


def _has_admin(request: Request) -> bool:
    return _auth.admin_enabled() and _token_matches(
        extract_token_from_request(request), _auth.ADMIN_TOKEN
    )


class ManualOrderBody(BaseModel):
    symbol: str = Field(min_length=1, max_length=32)
    side: str
    qty: str
    order_type: str = "market"
    limit_price: str | None = None
    account_id: str | None = None
    time_in_force: str = "day"
    extended_hours: bool = False
    # Client-minted per-gesture id: a double-click / retry replays the first
    # outcome instead of placing a second order.
    client_request_id: str | None = Field(default=None, max_length=64)


class ReplaceBody(BaseModel):
    limit_price: str
    client_request_id: str | None = Field(default=None, max_length=64)


class CloseBody(BaseModel):
    symbol: str = Field(min_length=1, max_length=32)
    account_id: str | None = None
    client_request_id: str | None = Field(default=None, max_length=64)


_STATUS_CODE = {
    "invalid": 400,
    "not_found": 404,
}


def _respond(result: dict[str, Any]) -> dict[str, Any]:
    """Verb outcomes pass through verbatim; only malformed input and unknown
    ids become HTTP errors. A RiskEngine block is a 200 with status=blocked —
    the UI renders the reason, it is not a transport failure."""
    code = _STATUS_CODE.get(str(result.get("status")))
    if code is not None:
        raise HTTPException(status_code=code, detail=str(result.get("reason")))
    return result


@router.post("/orders")
async def api_place_manual_order(body: ManualOrderBody, request: Request) -> dict[str, Any]:
    _require_admin(request)
    args = body.model_dump(exclude={"client_request_id"})
    result = await place_manual_order(
        args, request_id=body.client_request_id, db_path=DEFAULT_DB_PATH
    )
    return _respond(result)


@router.post("/orders/{execution_id}/cancel")
async def api_cancel_manual_order(execution_id: str, request: Request) -> dict[str, Any]:
    _require_admin(request)
    result = await cancel_manual_order(execution_id, db_path=DEFAULT_DB_PATH)
    return _respond(result)


@router.post("/orders/{execution_id}/replace")
async def api_replace_manual_order(
    execution_id: str, body: ReplaceBody, request: Request
) -> dict[str, Any]:
    _require_admin(request)
    try:
        limit_price = Decimal(str(body.limit_price))
    except (InvalidOperation, ValueError):
        raise HTTPException(status_code=400, detail="limit_price must be a number") from None
    result = await replace_manual_order(
        execution_id,
        limit_price=limit_price,
        request_id=body.client_request_id,
        db_path=DEFAULT_DB_PATH,
    )
    return _respond(result)


@router.post("/positions/close")
async def api_close_manual_position(body: CloseBody, request: Request) -> dict[str, Any]:
    _require_admin(request)
    result = await close_manual_position(
        body.symbol,
        account_id=body.account_id,
        request_id=body.client_request_id,
        db_path=DEFAULT_DB_PATH,
    )
    return _respond(result)


@router.get("/limits")
def api_manual_limits(request: Request, account_id: str | None = None) -> dict[str, Any]:
    """The clicker's dashboard: how much budget is left before a gate bites."""
    cfg = trading_config()
    account = (
        get_account(account_id, db_path=DEFAULT_DB_PATH)
        if account_id
        else default_execution_account(db_path=DEFAULT_DB_PATH)
    )
    open_orders = 0
    if account is not None:
        snapshot = get_latest_snapshot(
            str(account["broker"]).strip().lower(),
            account_id=str(account["id"]),
            db_path=DEFAULT_DB_PATH,
        )
        if snapshot:
            open_orders = len(snapshot.get("open_orders") or [])
    return {
        "execution_mode": cfg["execution_mode"],
        "kill_switch_active": is_kill_switch_active(),
        "entries_halted": cfg["entries_halted"],
        "market_open": is_market_open(),
        "orders_today": count_executions_today(db_path=DEFAULT_DB_PATH),
        "orders_today_account": (
            count_executions_today(
                account_id=str(account["id"]), db_path=DEFAULT_DB_PATH
            )
            if account is not None
            else 0
        ),
        "max_daily_orders": cfg["max_daily_orders"],
        "max_open_orders": cfg["max_open_orders"],
        "open_orders": open_orders,
        "max_order_value_usd": cfg["max_order_value_usd"],
        "account_id": account["id"] if account is not None else None,
        "admin_unlocked": _has_admin(request),
    }
