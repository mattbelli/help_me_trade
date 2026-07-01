"""Read-only Schwab account snapshot API (real funded accounts — never executes)."""

from __future__ import annotations

from fastapi import APIRouter

from brokers.schwab_accounts import get_schwab_accounts

router = APIRouter(prefix="/api/schwab", tags=["schwab"])


@router.get("/accounts")
async def schwab_accounts(refresh: bool = False) -> dict:
    """Live balances + positions + open orders across every linked Schwab account."""
    return await get_schwab_accounts(force=refresh)
