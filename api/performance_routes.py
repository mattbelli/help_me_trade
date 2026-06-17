"""Performance dashboard API."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query

from db.analytics import ai_performance_context, performance_dashboard
from db.trades import daily_trade_stats, symbol_trade_stats
from trading.sync import sync_all_enabled_brokers

router = APIRouter(prefix="/api/performance", tags=["performance"])


@router.get("/dashboard")
def api_performance_dashboard(
    account_id: str | None = None,
    period: str = Query(default="month"),
    from_date: str | None = None,
    to_date: str | None = None,
) -> dict[str, Any]:
    return performance_dashboard(
        account_id=account_id,
        period=period,
        custom_from=from_date,
        custom_to=to_date,
    )


@router.get("/ai-context")
def api_ai_performance_context(
    account_id: str | None = None,
    period: str = Query(default="year"),
) -> dict[str, Any]:
    return ai_performance_context(account_id=account_id, period=period)


@router.post("/sync")
def api_sync_trades() -> dict[str, Any]:
    results = sync_all_enabled_brokers()
    return {"results": results}


@router.get("/calendar")
def api_performance_calendar(
    account_id: str | None = None,
    from_date: str | None = None,
    to_date: str | None = None,
) -> dict[str, Any]:
    return {
        "days": daily_trade_stats(
            account_id=account_id,
            from_date=from_date,
            to_date=to_date,
        )
    }


@router.get("/symbols")
def api_performance_symbols(
    account_id: str | None = None,
    period: str = "all",
    from_date: str | None = None,
    to_date: str | None = None,
) -> dict[str, Any]:
    from db.analytics import resolve_period

    f, t = resolve_period(period, custom_from=from_date, custom_to=to_date)
    return {
        "symbols": symbol_trade_stats(account_id=account_id, from_date=f, to_date=t)
    }
