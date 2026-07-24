"""Strategy registry API."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body
from fastapi.responses import JSONResponse

from db.accounts import account_is_execution_capable, get_account, list_accounts
from db.strategies import get_strategy, list_strategies, update_strategy
from signals.strategy_kinds import is_portfolio_kind
from signals.strategy_universe import available_universes

router = APIRouter(prefix="/api/strategies", tags=["strategies"])

# Propose-only by design — entries AND exits are phone-approved, never auto-routed
# through the execution chokepoint. The regime-local sleeves additionally carry
# mechanical kill lines (References/REGIME_SLEEVES_SPEC.md).
_PROPOSE_ONLY_IDS = frozenset({"session_node_spy", "generals_dip", "vol_event"})

# Percent-of-equity sizing is resolved in signals.strategy_scan._resolve_notional,
# which only runs for the generic-scan kinds. These four size themselves off
# notional_usd in their OWN paths (momentum's rebalance, the regime sleeves' loop,
# session-node), so storing a percent for them would persist a sizing rule that
# silently never applies to their orders. Reject it rather than mislead — the UI
# hides the control, and this blocks a direct API call too. Clearing (null) is
# always allowed.
_PERCENT_SIZING_UNSUPPORTED_IDS = frozenset(
    {"momentum_sp500", "generals_dip", "vol_event", "session_node_spy"}
)


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


@router.get("/regime-sleeves/status")
def api_regime_sleeves_status() -> dict[str, Any]:
    """Dormancy-visible status for the regime-local sleeves: enable state, the last
    scan result (WHY nothing fired — e.g. '0 flushes in top-30 today', 'term_flip
    dormant (vix3m stale)'), the current point-in-time universe, trigger flags, and
    the latest kill-line check."""
    from signals.regime_sleeves import sleeves_status

    return sleeves_status()


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
        # Portfolio strategies (momentum) rebalance in bulk and source "held" from their OWN
        # managed_positions, so they need a DEDICATED account — sharing one with another
        # strategy risks re-buying that strategy's overlapping names. Reject a shared bind.
        if is_portfolio_kind(strategy_id):
            shared_with = next(
                (
                    s["id"]
                    for s in list_strategies()
                    if s["id"] != strategy_id and s.get("account_id") == str(account_id)
                ),
                None,
            )
            if shared_with is not None:
                return JSONResponse(
                    status_code=400,
                    content={
                        "detail": (
                            f"{strategy_id} needs a DEDICATED account — {account_id} is "
                            f"already used by {shared_with}. Bind it to an empty account."
                        )
                    },
                )

    # Propose-only strategies (session-node + the regime-local sleeves): the UI hides
    # the auto option; block it server-side too so no direct API call can route their
    # entries/exits through the execution chokepoint unattended.
    if strategy_id in _PROPOSE_ONLY_IDS and (
        body.get("exit_mode") == "auto" or body.get("entry_mode") == "auto"
    ):
        return JSONResponse(
            status_code=400,
            content={"detail": f"{strategy_id} is propose-only"},
        )

    if (
        strategy_id in _PERCENT_SIZING_UNSUPPORTED_IDS
        and body.get("notional_pct") is not None
    ):
        return JSONResponse(
            status_code=400,
            content={
                "detail": (
                    f"{strategy_id} sizes off a fixed notional — percent-of-equity "
                    "is not wired into its scan path"
                )
            },
        )

    try:
        updated = update_strategy(
            strategy_id,
            enabled=body.get("enabled") if "enabled" in body else None,
            account_id=account_id if "account_id" in body else ...,
            notional_usd=body.get("notional_usd")
            if "notional_usd" in body
            else None,
            # Sentinel-defaulted: an explicit null clears percent sizing (back to $).
            notional_pct=body.get("notional_pct") if "notional_pct" in body else ...,
            exit_mode=body.get("exit_mode") if "exit_mode" in body else None,
            entry_mode=body.get("entry_mode") if "entry_mode" in body else None,
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
