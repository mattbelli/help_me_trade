"""Strategy registry API."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body
from fastapi.responses import JSONResponse

from db.accounts import account_is_execution_capable, get_account, list_accounts
from db.strategies import get_strategy, list_strategies, update_strategy
from signals.strategy_universe import available_universes

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


@router.get("/accounts")
def api_list_strategy_accounts() -> dict[str, Any]:
    """Execution-capable accounts a strategy can be bound to (for the UI picker)."""
    accounts = [
        {
            "id": a["id"],
            "label": a["label"],
            "broker": a["broker"],
            "is_default": bool(a.get("is_default")),
        }
        for a in list_accounts(enabled_only=True)
        if a.get("execution_capable")
    ]
    return {"accounts": accounts}


@router.get("/universes")
def api_list_strategy_universes() -> dict[str, Any]:
    """Universe options a strategy can scan (built-ins + pinnable named watchlists)."""
    return {"universes": available_universes()}


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

    # Session-node is propose-only by design (exits are phone prompts, never auto-sold).
    # The UI hides the auto option; block it server-side too so no direct API call can
    # route this strategy's exits through the execution chokepoint.
    if strategy_id == "session_node_spy" and body.get("exit_mode") == "auto":
        return JSONResponse(
            status_code=400,
            content={"detail": "session_node_spy is propose-only"},
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
            universe=body.get("universe") if "universe" in body else None,
            max_concurrent_positions=body.get("max_concurrent_positions")
            if "max_concurrent_positions" in body
            else ...,
            max_deployed_usd=body.get("max_deployed_usd")
            if "max_deployed_usd" in body
            else ...,
        )
    except ValueError as exc:
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    return {"strategy": _strategy_to_api(updated)}
