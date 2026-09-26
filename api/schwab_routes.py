"""Read-only Schwab account snapshot API (real funded accounts — never executes)."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter

from brokers.schwab_accounts import get_schwab_accounts

router = APIRouter(prefix="/api/schwab", tags=["schwab"])


@router.get("/accounts")
async def schwab_accounts(refresh: bool = False) -> dict:
    """Live balances + positions + open orders across every linked Schwab account."""
    return await get_schwab_accounts(force=refresh)


@router.get("/history")
def schwab_history(account_id: str | None = None, limit: int = 400) -> dict:
    """Persisted daily equity/day-P&L snapshots per account (accurate, no FIFO)."""
    from db.schwab_history import list_schwab_daily

    return {"snapshots": list_schwab_daily(account_id, limit=limit)}


@router.get("/equity-curve")
def schwab_equity(
    account_id: str | None = None,
    days: int = 30,
    range: Literal["1D", "3D", "1W", "1M", "1Y", "ALL"] | None = None,
) -> dict:
    """Daily equity curve for a real Schwab account (schwab_<last4>) or all real
    (omitted / all_real), using recorded Schwab balances including holdings."""
    from signals.schwab_equity import schwab_equity_curve

    scope = (
        None
        if account_id in (None, "", "all", "all_real", "overview")
        else account_id
    )
    return schwab_equity_curve(scope, days=max(1, min(days, 3650)), range_key=range)
