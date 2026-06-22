"""Strategy registry API."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body
from fastapi.responses import JSONResponse

from db.accounts import account_is_execution_capable, get_account
from db.strategies import get_strategy, list_strategies, update_strategy

router = APIRouter(prefix="/api/strategies", tags=["strategies"])


def _strategy_to_api(strategy: dict[str, Any]) -> dict[str, Any]:
    out = dict(strategy)
    account_id = strategy.get("account_id")
    if account_id:
        account = get_account(str(account_id))
        out["account_label"] = account["label"] if account else None
    else:
        out["account_label"] = None
    return out


@router.get("")
def api_list_strategies() -> dict[str, Any]:
    return {"strategies": [_strategy_to_api(s) for s in list_strategies()]}


@router.put("/{strategy_id}")
def api_update_strategy(
    strategy_id: str,
    body: dict[str, Any] = Body(...),
) -> dict[str, Any]:
    if get_strategy(strategy_id) is None:
        return JSONResponse(status_code=404, content={"detail": "Strategy not found"})

    account_id = body.get("account_id")
    if account_id is not None and account_id != "":
        if not account_is_execution_capable(str(account_id)):
            return JSONResponse(
                status_code=400,
                content={"detail": "account_id must be an execution-capable account"},
            )

    try:
        updated = update_strategy(
            strategy_id,
            enabled=body.get("enabled") if "enabled" in body else None,
            account_id=account_id if "account_id" in body else ...,
            notional_usd=body.get("notional_usd")
            if "notional_usd" in body
            else None,
            exit_mode=body.get("exit_mode") if "exit_mode" in body else None,
            params=body.get("params") if "params" in body else None,
        )
    except ValueError as exc:
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    return {"strategy": _strategy_to_api(updated)}
